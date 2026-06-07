"""Offline service functions for the first agentic CLI implementation."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from gflights.app_state import AppState, PriceCache, init_app_state, price_cache_for_state
from gflights.codec import CodecError, decode_query_value
from gflights.domain import SearchIntent
from gflights.itinerary_extraction import extract_selected_itinerary
from gflights.ranking import rank_observed_pairs
from gflights.result_extraction import extract_primary_results
from gflights.route_resolution import (
    RouteFixtureSource,
    extract_route_choices_from_snapshot,
    resolve_route_from_fixtures,
)

DatePairProbe = Callable[[SearchIntent], dict[str, Any]]


class ServiceError(Exception):
    def __init__(self, exit_code: int, payload: dict[str, Any]) -> None:
        super().__init__(payload.get("status", "service_error"))
        self.exit_code = exit_code
        self.payload = payload


def json_schema_for(model: str) -> dict[str, Any]:
    if model != "search-intent":
        raise ServiceError(
            3,
            {
                "status": "unsupported",
                "unsupported": [{"field": "model", "value": model}],
                "warnings": [],
            },
        )
    return SearchIntent.model_json_schema()


def init_project(path: Path) -> dict[str, Any]:
    project_root = path.expanduser().resolve()
    state = init_app_state(project_root)
    return {
        "status": "ok",
        "project_root": str(project_root),
        "app_state_root": str(state.root),
        "config_path": str(state.config_path),
        "database_path": str(state.database_path),
        "artifacts_root": str(state.artifact_root),
        "fixture_root": str(state.fixture_root),
        "run_root": str(state.run_root),
        "cache_max_age_seconds": state.cache_max_age_seconds,
    }


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def load_intents(path: Path) -> list[SearchIntent]:
    raw = load_json(path)
    items = raw if isinstance(raw, list) else [raw]
    try:
        return [SearchIntent.model_validate(item) for item in items]
    except ValidationError as exc:
        raise ServiceError(
            2,
            {
                "status": "ambiguous",
                "errors": json.loads(exc.json()),
                "warnings": [],
            },
        ) from exc


def parse_intents(path: Path) -> list[dict[str, Any]]:
    return [{"status": "ok", **intent.model_dump(mode="json")} for intent in load_intents(path)]


def scan_dates(
    input_json: Path,
    offline_fixtures: Path | None = None,
    *,
    project_root: Path | None = None,
    now: datetime | None = None,
    date_pair_probe: DatePairProbe | None = None,
) -> list[dict[str, Any]]:
    intents = load_intents(input_json)
    if (
        offline_fixtures is not None
        and project_root is None
        and now is None
        and date_pair_probe is None
    ):
        return [_date_scan_result(intent, offline_fixtures) for intent in intents]

    state = init_app_state(project_root)
    cache = price_cache_for_state(state)
    observed_at = now or datetime.now(UTC)
    return [
        _date_scan_live_or_cache_result(
            intent,
            state=state,
            cache=cache,
            now=observed_at,
            date_pair_probe=date_pair_probe,
            route_fixtures=offline_fixtures,
        )
        for intent in intents
    ]


def exit_code_for_payload(payload: dict[str, Any] | list[dict[str, Any]]) -> int:
    outputs = payload if isinstance(payload, list) else [payload]
    return _aggregate_exit_code(
        [_exit_code_for_status(str(item.get("status"))) for item in outputs]
    )


def _date_scan_result(intent: SearchIntent, offline_fixtures: Path) -> dict[str, Any]:
    route_resolution = _route_resolution_for_intent(intent, offline_fixtures)
    if route_resolution["status"] == "ambiguous":
        return _date_scan_route_ambiguity_result(intent, route_resolution)
    if route_resolution["status"] == "unsupported":
        return _date_scan_route_unsupported_result(intent, route_resolution)

    departure_count = _inclusive_days(intent.departure_window.start, intent.departure_window.end)
    if intent.trip_type == "round_trip" and intent.return_window is not None:
        return_count = _inclusive_days(intent.return_window.start, intent.return_window.end)
        generated_pairs = departure_count * return_count
        return_date = intent.return_window.start
    else:
        generated_pairs = departure_count
        return_date = None

    ranked_pair: dict[str, Any] = {
        "departure_date": intent.departure_window.start,
        "return_date": return_date,
        "scoring_explanation": {
            "policy": "offline_fixture_bootstrap",
            "components": [
                {
                    "name": "evidence_policy",
                    "value": "offline fixtures only",
                }
            ],
        },
        "evidence": {
            "source_surfaces": ["offline-fixtures"],
            "artifacts": [str(offline_fixtures)],
        },
    }
    return {
        "query_id": intent.query_id,
        "status": "ok",
        "generated_pairs": generated_pairs,
        "probed_pairs": 1,
        "ranked_pairs": [ranked_pair],
        "ranking_policy": "offline_fixture_bootstrap",
        "unsupported": [],
        "warnings": [],
        "evidence": {
            "run_id": "offline-fixture-bootstrap",
            "source_surfaces": ["offline-fixtures"],
            "artifacts": [str(offline_fixtures)],
        },
        "route_resolution": route_resolution,
    }


def _inclusive_days(start: str, end: str) -> int:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    return max((end_date - start_date).days + 1, 1)


def _date_scan_live_or_cache_result(
    intent: SearchIntent,
    *,
    state: AppState,
    cache: PriceCache,
    now: datetime,
    date_pair_probe: DatePairProbe | None,
    route_fixtures: Path | None,
) -> dict[str, Any]:
    if route_fixtures is not None:
        route_resolution = _route_resolution_for_intent(intent, route_fixtures)
        if route_resolution["status"] == "ambiguous":
            return _date_scan_route_ambiguity_result(intent, route_resolution)
        if route_resolution["status"] == "unsupported":
            return _date_scan_route_unsupported_result(intent, route_resolution)

    pairs = _date_pairs(intent)
    counts = {"fresh_cache": 0, "probed": 0, "unsupported": 0, "skipped": 0}
    pair_coverage: list[dict[str, Any]] = []
    ranked_pairs: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    warnings: list[str] = []
    source_surfaces = {"sqlite-cache", "docs/detailed-cli-spec.md"}
    artifacts = {str(state.database_path)}

    for departure_date, return_date in pairs:
        fresh_prices = cache.get_fresh_prices_for_dates(
            query_id=intent.query_id,
            departure_date=departure_date,
            return_date=return_date,
            currency=intent.currency,
            now=now,
        )
        if fresh_prices:
            counts["fresh_cache"] += 1
            best = fresh_prices[0]
            evidence = {
                "run_id": best["source_run_id"],
                "source_surfaces": ["sqlite-cache"],
                "artifacts": [str(state.database_path)],
            }
            price = _price_object(
                best["price_payload"],
                fallback_amount=best["price_amount"],
                currency=best["currency"],
            )
            pair_coverage.append(
                {
                    "departure_date": departure_date,
                    "return_date": return_date,
                    "status": "fresh_cache",
                    "observations": len(fresh_prices),
                    "best_observed_price": price,
                    "evidence": evidence,
                }
            )
            ranked_pairs.append(
                _ranked_pair(
                    departure_date=departure_date,
                    return_date=return_date,
                    result=best["price_payload"],
                    price=price,
                    result_count=len(fresh_prices),
                    evidence=evidence,
                    source="fresh_cache",
                )
            )
            continue

        if date_pair_probe is not None:
            concrete_intent = _concrete_intent(intent, departure_date, return_date)
            probe_payload = date_pair_probe(concrete_intent)
            evidence = _probe_evidence(probe_payload)
            source_surfaces.update(evidence["source_surfaces"])
            artifacts.update(evidence["artifacts"])
            priced_results = _priced_results(probe_payload.get("results"))
            if priced_results:
                counts["probed"] += 1
                best_result = priced_results[0]
                price = _price_object(best_result, currency=intent.currency)
                pair_coverage.append(
                    {
                        "departure_date": departure_date,
                        "return_date": return_date,
                        "status": "probed",
                        "observation_status": probe_payload.get("status", "unknown"),
                        "observations": len(priced_results),
                        "best_observed_price": price,
                        "evidence": evidence,
                    }
                )
                ranked_pairs.append(
                    _ranked_pair(
                        departure_date=departure_date,
                        return_date=return_date,
                        result=best_result,
                        price=price,
                        result_count=len(priced_results),
                        evidence=evidence,
                        source="live_probe",
                    )
                )
                continue

            if probe_payload.get("status") == "unsupported":
                counts["unsupported"] += 1
                pair_unsupported = _pair_unsupported(
                    probe_payload,
                    departure_date=departure_date,
                    return_date=return_date,
                )
                unsupported.extend(pair_unsupported)
                pair_coverage.append(
                    {
                        "departure_date": departure_date,
                        "return_date": return_date,
                        "status": "unsupported",
                        "unsupported": pair_unsupported,
                        "evidence": evidence,
                    }
                )
                continue

            if probe_payload.get("status") == "skipped":
                counts["skipped"] += 1
                reason = str(probe_payload.get("reason") or "date pair was not probed")
                pair_coverage.append(
                    {
                        "departure_date": departure_date,
                        "return_date": return_date,
                        "status": "skipped",
                        "reason": reason,
                        "evidence": evidence,
                    }
                )
                warnings.append(f"date pair {departure_date}/{return_date or ''} skipped: {reason}")
                continue

            counts["probed"] += 1
            pair_coverage.append(
                {
                    "departure_date": departure_date,
                    "return_date": return_date,
                    "status": "probed",
                    "observation_status": probe_payload.get("status", "unknown"),
                    "observations": 0,
                    "evidence": evidence,
                }
            )
            warnings.append(
                f"date pair {departure_date}/{return_date or ''} was probed but returned no priced rows"
            )
            continue

        counts["skipped"] += 1
        evidence = {
            "run_id": "date-scan-live-or-cache",
            "source_surfaces": ["docs/detailed-cli-spec.md"],
            "artifacts": [],
        }
        pair_coverage.append(
            {
                "departure_date": departure_date,
                "return_date": return_date,
                "status": "skipped",
                "reason": "no fresh cache entry and no live date-pair probe adapter was supplied",
                "evidence": evidence,
            }
        )
        warnings.append(
            f"date pair {departure_date}/{return_date or ''} needs a live probe or fresh cache"
        )

    ranked_pairs = rank_observed_pairs(intent, ranked_pairs)
    status = (
        "ok"
        if ranked_pairs and counts["skipped"] == 0 and counts["unsupported"] == 0
        else "experimental"
    )
    return {
        "query_id": intent.query_id,
        "status": status,
        "generated_pairs": len(pairs),
        "probed_pairs": counts["probed"],
        "coverage_counts": counts,
        "pair_coverage": pair_coverage,
        "ranked_pairs": ranked_pairs,
        "ranking_policy": "comfort_aware_v1",
        "unsupported": unsupported,
        "warnings": warnings,
        "cache": {
            "database_path": str(state.database_path),
            "fresh_pairs_used": counts["fresh_cache"],
            "max_age_seconds": state.cache_max_age_seconds,
        },
        "evidence": {
            "run_id": "date-scan-live-or-cache",
            "source_surfaces": sorted(source_surfaces),
            "artifacts": sorted(artifacts),
        },
    }


def _date_pairs(intent: SearchIntent) -> list[tuple[str, str | None]]:
    departures = _date_values(intent.departure_window.start, intent.departure_window.end)
    if intent.trip_type == "round_trip" and intent.return_window is not None:
        returns = _date_values(intent.return_window.start, intent.return_window.end)
        return [(departure, return_date) for departure in departures for return_date in returns]
    return [(departure, None) for departure in departures]


def _date_values(start: str, end: str) -> list[str]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    days = max((end_date - start_date).days + 1, 1)
    return [(start_date + timedelta(days=offset)).isoformat() for offset in range(days)]


def _concrete_intent(
    intent: SearchIntent,
    departure_date: str | int,
    return_date: str | int | None,
) -> SearchIntent:
    departure_value = _date_string(departure_date)
    return_value = _date_string(return_date) if return_date is not None else None
    payload = intent.model_dump(mode="json")
    payload["departure_window"] = {"start": departure_value, "end": departure_value}
    payload["return_window"] = (
        {"start": return_value, "end": return_value}
        if intent.trip_type == "round_trip" and return_value is not None
        else None
    )
    return SearchIntent.model_validate(payload)


def _date_string(value: str | int) -> str:
    if isinstance(value, str):
        return value
    return date.fromordinal(value).isoformat()


def _price_object(
    result: dict[str, Any],
    *,
    fallback_amount: Any | None = None,
    currency: str,
) -> dict[str, Any]:
    price = result.get("price")
    if isinstance(price, dict) and price.get("amount") is not None:
        return {
            "amount": price["amount"],
            "currency": price.get("currency") or currency,
            "text": price.get("text") or str(price["amount"]),
        }
    amount = result.get("amount", fallback_amount)
    return {
        "amount": amount,
        "currency": result.get("currency") or currency,
        "text": result.get("text") or str(amount),
    }


def _priced_results(results: Any) -> list[dict[str, Any]]:
    if not isinstance(results, list):
        return []
    priced = [
        result
        for result in results
        if isinstance(result, dict)
        and isinstance(result.get("price"), dict)
        and result["price"].get("amount") is not None
    ]
    return sorted(
        priced,
        key=lambda result: (
            result["price"]["amount"],
            int(result.get("duration_minutes") or 999999),
            _stop_count(result),
        ),
    )


def _stop_count(result: dict[str, Any]) -> int:
    stops = result.get("stops")
    if isinstance(stops, dict) and stops.get("count") is not None:
        return int(stops["count"])
    return 999999


def _ranked_pair(
    *,
    departure_date: str,
    return_date: str | None,
    result: dict[str, Any],
    price: dict[str, Any],
    result_count: int,
    evidence: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    return {
        "departure_date": departure_date,
        "return_date": return_date,
        "best_observed_price": price,
        "result_count": result_count,
        "top_result_summary": {
            "result_id": result.get("result_id"),
            "carriers": result.get("carriers", []),
            "duration_minutes": result.get("duration_minutes"),
            "stops": result.get("stops"),
            "layovers": result.get("layovers", []),
            "emissions": result.get("emissions"),
        },
        "source": source,
        "evidence": evidence,
    }


def _probe_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        return {
            "run_id": "date-pair-probe",
            "source_surfaces": ["date-pair-probe"],
            "artifacts": [],
        }
    surfaces = evidence.get("source_surfaces")
    artifacts = evidence.get("artifacts")
    return {
        "run_id": str(evidence.get("run_id") or "date-pair-probe"),
        "source_surfaces": [str(item) for item in surfaces] if isinstance(surfaces, list) else [],
        "artifacts": [str(item) for item in artifacts] if isinstance(artifacts, list) else [],
    }


def _pair_unsupported(
    payload: dict[str, Any],
    *,
    departure_date: str,
    return_date: str | None,
) -> list[dict[str, Any]]:
    unsupported = payload.get("unsupported")
    if not isinstance(unsupported, list):
        return []
    items: list[dict[str, Any]] = []
    for entry in unsupported:
        if not isinstance(entry, dict):
            continue
        items.append(
            {
                **entry,
                "departure_date": departure_date,
                "return_date": return_date,
            }
        )
    return items


def _route_resolution_for_intent(
    intent: SearchIntent,
    offline_fixtures: Path,
) -> dict[str, Any]:
    ambiguities: list[dict[str, Any]] = []
    resolved_fields: list[str] = []
    selected_fields: list[str] = []
    unsupported: list[dict[str, Any]] = []
    source_surfaces = {"offline-fixtures"}
    artifacts = {str(offline_fixtures)}

    for field in ("origin", "destination"):
        endpoint = getattr(intent, field)
        if endpoint.selected is not None:
            selected_fields.append(field)
            selected_evidence = endpoint.selected.evidence or {}
            surfaces = selected_evidence.get("source_surfaces")
            if isinstance(surfaces, list):
                source_surfaces.update(str(item) for item in surfaces)
            evidence_artifacts = selected_evidence.get("artifacts")
            if isinstance(evidence_artifacts, list):
                artifacts.update(str(item) for item in evidence_artifacts)
            continue
        if endpoint.kind != "city_or_airport":
            continue

        exit_code, payload = resolve_route(endpoint.text, offline_fixtures)
        artifacts.update(str(item) for item in _payload_artifacts(payload))
        surfaces = payload.get("evidence", {}).get("source_surfaces")
        if isinstance(surfaces, list):
            source_surfaces.update(str(item) for item in surfaces)

        if exit_code == 0:
            resolved_fields.append(field)
            continue
        if exit_code == 2:
            ambiguities.append(
                {
                    "field": field,
                    "input_text": endpoint.text,
                    "ambiguity_reason": payload.get("ambiguity_reason") or "multiple_route_choices",
                    "choices": payload.get("choices", []),
                    "evidence": payload.get("evidence", {}),
                }
            )
            continue
        unsupported.extend(payload.get("unsupported", []))

    status = "unsupported" if unsupported else "ambiguous" if ambiguities else "ok"
    return {
        "status": status,
        "selected_fields": selected_fields,
        "resolved_fields": resolved_fields,
        "route_ambiguities": ambiguities,
        "unsupported": unsupported,
        "evidence": {
            "run_id": "route-resolution-preflight",
            "source_surfaces": sorted(source_surfaces),
            "artifacts": sorted(artifacts),
        },
    }


def _search_route_ambiguity_payload(
    intent: SearchIntent,
    route_resolution: dict[str, Any],
) -> dict[str, Any]:
    return {
        "query_id": intent.query_id,
        "status": "ambiguous",
        "confidence": "unknown",
        "results": [],
        "route_ambiguities": route_resolution["route_ambiguities"],
        "unsupported": [],
        "warnings": ["selected route choice is required before search"],
        "route_resolution": route_resolution,
        "evidence": route_resolution["evidence"],
    }


def _date_scan_route_ambiguity_result(
    intent: SearchIntent,
    route_resolution: dict[str, Any],
) -> dict[str, Any]:
    return {
        "query_id": intent.query_id,
        "status": "ambiguous",
        "generated_pairs": 0,
        "probed_pairs": 0,
        "coverage_counts": {
            "fresh_cache": 0,
            "probed": 0,
            "unsupported": 0,
            "skipped": 0,
        },
        "pair_coverage": [],
        "ranked_pairs": [],
        "ranking_policy": "not_applicable_route_ambiguity",
        "route_ambiguities": route_resolution["route_ambiguities"],
        "unsupported": [],
        "warnings": ["selected route choice is required before date scanning"],
        "route_resolution": route_resolution,
        "evidence": route_resolution["evidence"],
    }


def _search_route_unsupported_payload(
    intent: SearchIntent,
    route_resolution: dict[str, Any],
) -> dict[str, Any]:
    return {
        "query_id": intent.query_id,
        "status": "unsupported",
        "confidence": "unknown",
        "results": [],
        "route_ambiguities": route_resolution["route_ambiguities"],
        "unsupported": route_resolution["unsupported"],
        "warnings": ["route choice could not be resolved from reviewed evidence"],
        "route_resolution": route_resolution,
        "evidence": route_resolution["evidence"],
    }


def _date_scan_route_unsupported_result(
    intent: SearchIntent,
    route_resolution: dict[str, Any],
) -> dict[str, Any]:
    return {
        "query_id": intent.query_id,
        "status": "unsupported",
        "generated_pairs": 0,
        "probed_pairs": 0,
        "coverage_counts": {
            "fresh_cache": 0,
            "probed": 0,
            "unsupported": 0,
            "skipped": 0,
        },
        "pair_coverage": [],
        "ranked_pairs": [],
        "ranking_policy": "not_applicable_route_unsupported",
        "route_ambiguities": route_resolution["route_ambiguities"],
        "unsupported": route_resolution["unsupported"],
        "warnings": ["route choice could not be resolved from reviewed evidence"],
        "route_resolution": route_resolution,
        "evidence": route_resolution["evidence"],
    }


def _payload_artifacts(payload: dict[str, Any]) -> list[str]:
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        return []
    artifacts = evidence.get("artifacts")
    return [str(item) for item in artifacts] if isinstance(artifacts, list) else []


def replay_fixture(path: Path) -> tuple[int, dict[str, Any]]:
    fixture = load_json(path)
    fixture_type = fixture["fixture_type"]
    expected = fixture.get("expected", {})
    evidence = {
        "fixture_id": fixture["fixture_id"],
        "run_id": fixture["run_id"],
        "source_surfaces": [fixture["source_surface"]],
        "artifacts": [str(path)],
    }

    if fixture_type == "blocked_stop_state":
        return expected["exit_code"], {
            "status": expected["status"],
            "browser_mode": expected["browser_mode"],
            "fallback": expected["fallback"],
            "confidence": fixture["confidence"],
            "evidence": evidence,
        }

    if fixture_type == "primary_results_visible_text":
        results = extract_primary_results(
            fixture.get("snapshot", {}),
            source_surface=fixture["source_surface"],
            evidence_artifact=fixture.get("source_artifact", str(path)),
            confidence=fixture["confidence"],
        )
        return 0, {
            "status": expected["status"] if results else "no_results",
            "confidence": fixture["confidence"] if results else "unknown",
            "results": results,
            "unsupported": [],
            "warnings": [] if results else ["no primary result rows found in visible text fixture"],
            "evidence": evidence,
        }

    if fixture_type == "selected_itinerary_visible_text":
        itinerary = extract_selected_itinerary(
            fixture,
            source_surface=fixture["source_surface"],
            confidence=fixture["confidence"],
        )
        selected_evidence = {
            **evidence,
            "artifacts": [str(path), *fixture.get("source_artifacts", [])],
        }
        return 0, {
            "status": expected["status"] if itinerary["segments"] else "no_results",
            "confidence": fixture["confidence"] if itinerary["segments"] else "unknown",
            "itinerary": itinerary,
            "unsupported": [],
            "warnings": []
            if itinerary["segments"]
            else ["no selected-itinerary segments found in visible text fixture"],
            "evidence": selected_evidence,
        }

    if fixture_type == "route_autocomplete_choices":
        return 0, {
            "status": expected.get("status", "ok"),
            "confidence": fixture["confidence"],
            "queries": fixture.get("queries", []),
            "unsupported": [],
            "warnings": [],
            "evidence": {
                **evidence,
                "artifacts": [str(path), *fixture.get("source_artifacts", [])],
            },
        }

    if fixture_type == "route_autocomplete_visible_text":
        outputs: list[dict[str, Any]] = []
        exit_codes: list[int] = []
        for query in fixture.get("queries", []):
            if not isinstance(query, dict):
                continue
            exit_code, payload = extract_route_choices_from_snapshot(
                input_text=str(query.get("input_text") or ""),
                field=str(query.get("field") or ""),
                snapshot=query.get("snapshot", {}),
                evidence_artifact=str(path),
                source_surface=str(fixture["source_surface"]),
                confidence=str(fixture.get("confidence") or "weak"),
            )
            exit_codes.append(exit_code)
            outputs.append(payload)
        return _aggregate_exit_code(exit_codes), {
            "status": _aggregate_status(outputs),
            "confidence": fixture["confidence"],
            "queries": outputs,
            "unsupported": _aggregate_unsupported(outputs),
            "warnings": [],
            "evidence": {
                **evidence,
                "artifacts": [str(path), *fixture.get("source_artifacts", [])],
            },
        }

    return 0, {
        "status": expected["status"],
        "confidence": fixture["confidence"],
        "results": expected.get("results", []),
        "unsupported": [],
        "warnings": [],
        "evidence": evidence,
    }


def decode_codec_fixture(path: Path) -> dict[str, Any]:
    fixture = load_json(path)
    expected = fixture["expected"]
    evidence = {
        "fixture_id": fixture["fixture_id"],
        "run_id": fixture["run_id"],
        "source_surfaces": [fixture["source_surface"]],
        "artifacts": [str(path)],
    }
    try:
        decoded = decode_query_value(fixture["key"], fixture["raw_value"])
    except CodecError as exc:
        return {
            "status": "stale_fixture",
            "key": fixture["key"],
            "raw_value": fixture["raw_value"],
            "confidence": "unknown",
            "wire_paths": [],
            "warnings": [str(exc)],
            "evidence": evidence,
        }

    stale_warnings = _codec_stale_warnings(
        expected_paths=expected.get("wire_paths", []),
        observed_paths=decoded.wire_paths,
    )
    codec_payload = {
        "encoding": decoded.encoding,
        "decoded_byte_length": decoded.decoded_byte_length,
        "round_trip_value": decoded.round_trip_value,
        "round_trip_ok": decoded.round_trip_value == fixture["raw_value"],
        "observed_wire_paths": decoded.wire_paths,
        "string_anchors": decoded.string_anchors,
    }
    if stale_warnings:
        return {
            "status": "stale_fixture",
            "key": fixture["key"],
            "raw_value": fixture["raw_value"],
            "confidence": "unknown",
            "wire_paths": expected.get("wire_paths", []),
            "warnings": stale_warnings,
            "codec": codec_payload,
            "evidence": evidence,
        }

    return {
        "status": expected["status"],
        "key": fixture["key"],
        "raw_value": fixture["raw_value"],
        "confidence": expected["confidence"],
        "wire_paths": expected["wire_paths"],
        "warnings": [],
        "codec": codec_payload,
        "evidence": evidence,
    }


def _codec_stale_warnings(
    *, expected_paths: list[dict[str, Any]], observed_paths: list[dict[str, Any]]
) -> list[str]:
    warnings: list[str] = []
    for expected_path in expected_paths:
        path = expected_path.get("path")
        value = expected_path.get("value")
        matched = any(
            observed_path.get("path") == path and observed_path.get("value") == value
            for observed_path in observed_paths
        )
        if not matched:
            warnings.append(f"expected wire path {path}={value!r} was not observed")
    return warnings


def _aggregate_status(outputs: list[dict[str, Any]]) -> str:
    statuses = [str(output.get("status") or "unknown") for output in outputs]
    for status in ("tool_error", "blocked", "stale_fixture", "unsupported", "ambiguous"):
        if status in statuses:
            return status
    return "ok" if statuses and all(status == "ok" for status in statuses) else "unknown"


def _aggregate_unsupported(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for output in outputs:
        unsupported = output.get("unsupported")
        if isinstance(unsupported, list):
            items.extend(item for item in unsupported if isinstance(item, dict))
    return items


def search_offline(
    input_json: Path, offline_fixtures: Path
) -> tuple[int, dict[str, Any] | list[dict[str, Any]]]:
    intents = load_intents(input_json)
    if len(intents) == 1:
        return _search_offline_intent(intents[0], offline_fixtures)

    outputs: list[dict[str, Any]] = []
    exit_codes: list[int] = []
    for intent in intents:
        exit_code, payload = _search_offline_intent(intent, offline_fixtures)
        exit_codes.append(exit_code)
        outputs.append(payload)
    return _aggregate_exit_code(exit_codes), outputs


def _search_offline_intent(
    intent: SearchIntent, offline_fixtures: Path
) -> tuple[int, dict[str, Any]]:
    route_resolution = _route_resolution_for_intent(intent, offline_fixtures)
    if route_resolution["status"] == "ambiguous":
        return 2, _search_route_ambiguity_payload(intent, route_resolution)
    if route_resolution["status"] == "unsupported":
        return 3, _search_route_unsupported_payload(intent, route_resolution)

    google_filters = intent.google_filters or {}
    if (
        google_filters.get("require_live_google_filter")
        and "maximum_layover_minutes" in google_filters
    ):
        return 3, {
            "query_id": intent.query_id,
            "status": "unsupported",
            "confidence": "unknown",
            "results": [],
            "unsupported": [
                {
                    "field": "google_filters.maximum_layover_minutes",
                    "status": "deferred",
                    "reason": "live Google Flights layover filters are deferred until focused evidence proves them",
                }
            ],
            "warnings": [],
            "evidence": {
                "run_id": "offline-unsupported-filter",
                "source_surfaces": ["docs/detailed-cli-spec.md"],
                "artifacts": [str(offline_fixtures)],
            },
        }

    return 0, {
        "query_id": intent.query_id,
        "status": "ok",
        "confidence": "weak",
        "results": [],
        "unsupported": [],
        "warnings": ["search uses offline bootstrap behavior only"],
        "evidence": {
            "run_id": "offline-search-bootstrap",
            "source_surfaces": ["offline-fixtures"],
            "artifacts": [str(offline_fixtures)],
        },
        "route_resolution": route_resolution,
    }


def resolve_route(input_text: str, offline_fixtures: Path) -> tuple[int, dict[str, Any]]:
    return resolve_route_from_fixtures(
        input_text,
        _route_fixture_sources(offline_fixtures),
    )


def _route_fixture_sources(offline_fixtures: Path) -> list[RouteFixtureSource]:
    paths = (
        [offline_fixtures]
        if offline_fixtures.is_file()
        else sorted(offline_fixtures.glob("*.json"))
    )
    sources: list[RouteFixtureSource] = []
    for path in paths:
        try:
            fixture = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(fixture, dict)
            and fixture.get("fixture_type") == "route_autocomplete_choices"
        ):
            sources.append((str(path), fixture))
    return sources


def _aggregate_exit_code(exit_codes: list[int]) -> int:
    for exit_code in (6, 5, 4, 3, 2):
        if exit_code in exit_codes:
            return exit_code
    return 0


def _exit_code_for_status(status: str) -> int:
    if status == "tool_error":
        return 6
    if status == "stale_fixture":
        return 5
    if status == "blocked":
        return 4
    if status in {"unsupported", "deferred"}:
        return 3
    if status == "ambiguous":
        return 2
    return 0


def doctor_report() -> dict[str, Any]:
    state = init_app_state()
    return {
        "status": "ok",
        "browser": {
            "default_mode": "headless",
            "headed_fallback_allowed": True,
        },
        "validation": {
            "live_google_flights_by_default": True,
            "google_flights_live_env": "1",
            "default_command": "make validate",
        },
        "app_state": {
            "root": str(state.root),
            "config_path": str(state.config_path),
            "database_path": str(state.database_path),
            "run_root": str(state.run_root),
            "cache_max_age_seconds": state.cache_max_age_seconds,
        },
        "toolchain": {
            "python_project": True,
            "package": "google-flights-search-cli",
        },
    }
