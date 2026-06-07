"""Default live Google Flights evidence orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from gflights.app_state import AppState, init_app_state, price_cache_for_state
from gflights.browser import BLOCKED_STOP_STATES, BrowserMode, CdpAdapter, CdpResult
from gflights.domain import SearchIntent
from gflights.live_cleanup import close_managed_page
from gflights.live_form import (
    UnsupportedLiveForm,
    plan_live_form_interaction,
    validate_live_form_support,
)
from gflights.query_state import UnsupportedQueryState, build_query_state
from gflights.result_extraction import extract_primary_results
from gflights.services import load_intents

GOOGLE_FLIGHTS_URL = "https://www.google.com/travel/flights"


async def run_live_search(
    *,
    input_json: Path,
    project_root: Path | None = None,
    adapter: CdpAdapter | None = None,
    browser_mode: BrowserMode = "headless",
    run_id: str | None = None,
    timeout_seconds: float = 30.0,
    interact_with_form: bool = False,
) -> tuple[int, dict[str, Any] | list[dict[str, Any]]]:
    adapter = adapter or CdpAdapter()
    intents = load_intents(input_json)
    state = init_app_state(project_root)
    if len(intents) == 1:
        return await _run_one_live_search(
            intent=intents[0],
            state=state,
            adapter=adapter,
            browser_mode=browser_mode,
            run_id=run_id,
            timeout_seconds=timeout_seconds,
            interact_with_form=interact_with_form,
        )

    outputs: list[dict[str, Any]] = []
    exit_codes: list[int] = []
    for index, intent in enumerate(intents, start=1):
        item_run_id = _batch_run_id(run_id, intent.query_id, index)
        exit_code, payload = await _run_one_live_search(
            intent=intent,
            state=state,
            adapter=adapter,
            browser_mode=browser_mode,
            run_id=item_run_id,
            timeout_seconds=timeout_seconds,
            interact_with_form=interact_with_form,
        )
        exit_codes.append(exit_code)
        outputs.append(payload)
    return _aggregate_exit_code(exit_codes), outputs


async def _run_one_live_search(
    *,
    intent: SearchIntent,
    state: AppState,
    adapter: CdpAdapter,
    browser_mode: BrowserMode,
    run_id: str | None,
    timeout_seconds: float,
    interact_with_form: bool,
) -> tuple[int, dict[str, Any]]:
    run_id = run_id or _new_run_id(intent.query_id)
    run_root = state.run_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    _write_json(run_root / "intent.json", intent.model_dump(mode="json"))

    executed: list[dict[str, Any]] = []
    artifacts: list[str] = [str(run_root / "intent.json")]
    source_surfaces: list[str] = []
    query_population = _build_query_population(intent, use_query_state=not interact_with_form)
    target_url = _google_flights_url(
        intent.language,
        intent.currency,
        params=query_population["params"],
    )
    _write_json(run_root / "query-state.json", query_population["artifact"])
    artifacts.append(str(run_root / "query-state.json"))
    source_surfaces.extend(query_population["source_surfaces"])
    if interact_with_form:
        try:
            validate_live_form_support(intent)
        except UnsupportedLiveForm as exc:
            _write_json(run_root / "command-log.json", executed)
            artifacts.append(str(run_root / "command-log.json"))
            return 3, _unsupported_live_form_payload(
                intent.query_id, run_id, browser_mode, exc, artifacts, source_surfaces
            )

    page_id = ""
    open_result = await _run_step(
        adapter=adapter,
        args=["open", target_url],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="open.json",
        source_surface="cdp:open",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    page_id = _page_id(open_result.json_payload)
    if _is_stop_result(open_result):
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            exit_code=open_result.exit_code or 4,
            payload=_stop_payload(
                intent_query_id=intent.query_id,
                run_id=run_id,
                browser_mode=browser_mode,
                result=open_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                target_url=target_url,
                query_population=query_population,
            ),
        )
    if open_result.status == "tool_error":
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            exit_code=6,
            payload=_tool_error_payload(
                intent.query_id,
                run_id,
                browser_mode,
                open_result,
                artifacts,
                source_surfaces,
                query_population,
            ),
        )

    wait_result = await _run_step(
        adapter=adapter,
        args=["wait", "load-state", "domcontentloaded", "--target", page_id],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="wait.json",
        source_surface="cdp:wait",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_recoverable_context_error(wait_result):
        wait_result = await _run_step(
            adapter=adapter,
            args=["wait", "load-state", "domcontentloaded", "--target", page_id],
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            artifact_name="wait-retry-1.json",
            source_surface="cdp:wait:retry",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
        )
    if _is_stop_result(wait_result):
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            exit_code=wait_result.exit_code or 4,
            payload=_stop_payload(
                intent_query_id=intent.query_id,
                run_id=run_id,
                browser_mode=browser_mode,
                result=wait_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                target_url=target_url,
                query_population=query_population,
            ),
        )
    if wait_result.status == "tool_error":
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            exit_code=6,
            payload=_tool_error_payload(
                intent.query_id,
                run_id,
                browser_mode,
                wait_result,
                artifacts,
                source_surfaces,
                query_population,
            ),
        )

    if query_population["params"].get("tfu") is not None:
        settle_result = await _run_step(
            adapter=adapter,
            args=["wait", "network-idle", "--target", page_id, "--idle", "1s"],
            browser_mode=browser_mode,
            timeout_seconds=min(timeout_seconds, 5.0),
            run_root=run_root,
            artifact_name="wait-query-network-idle.json",
            source_surface="cdp:wait:query-network-idle",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
        )
        if _is_stop_result(settle_result):
            return await _finish_live_search(
                adapter=adapter,
                page_id=page_id,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                executed=executed,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                exit_code=settle_result.exit_code or 4,
                payload=_stop_payload(
                    intent_query_id=intent.query_id,
                    run_id=run_id,
                    browser_mode=browser_mode,
                    result=settle_result,
                    artifacts=artifacts,
                    source_surfaces=source_surfaces,
                    target_url=target_url,
                    query_population=query_population,
                ),
            )
        if settle_result.status == "tool_error":
            query_population["warnings"].append(
                "encoded query-state network-idle wait failed; continuing to snapshot evidence"
            )

    if interact_with_form:
        for step in plan_live_form_interaction(intent, page_id=page_id).steps:
            result = await _run_step(
                adapter=adapter,
                args=step.args,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                artifact_name=step.artifact_name,
                source_surface=step.source_surface,
                executed=executed,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
            )
            if _is_stop_result(result):
                return await _finish_live_search(
                    adapter=adapter,
                    page_id=page_id,
                    browser_mode=browser_mode,
                    timeout_seconds=timeout_seconds,
                    run_root=run_root,
                    executed=executed,
                    artifacts=artifacts,
                    source_surfaces=source_surfaces,
                    exit_code=result.exit_code or 4,
                    payload=_stop_payload(
                        intent_query_id=intent.query_id,
                        run_id=run_id,
                        browser_mode=browser_mode,
                        result=result,
                        artifacts=artifacts,
                        source_surfaces=source_surfaces,
                        target_url=target_url,
                        query_population=query_population,
                    ),
                )
            if result.status == "tool_error":
                return await _finish_live_search(
                    adapter=adapter,
                    page_id=page_id,
                    browser_mode=browser_mode,
                    timeout_seconds=timeout_seconds,
                    run_root=run_root,
                    executed=executed,
                    artifacts=artifacts,
                    source_surfaces=source_surfaces,
                    exit_code=6,
                    payload=_tool_error_payload(
                        intent.query_id,
                        run_id,
                        browser_mode,
                        result,
                        artifacts,
                        source_surfaces,
                        query_population,
                    ),
                )

    snapshot_result = await _run_step(
        adapter=adapter,
        args=["snapshot", "--target", page_id, "--limit", "80"],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="snapshot.json",
        source_surface="cdp:snapshot",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_recoverable_context_error(snapshot_result):
        snapshot_result = await _run_step(
            adapter=adapter,
            args=["snapshot", "--target", page_id, "--limit", "80"],
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            artifact_name="snapshot-retry-1.json",
            source_surface="cdp:snapshot:retry",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
        )
    network_result = await _run_step(
        adapter=adapter,
        args=["network", "--target", page_id, "--limit", "50", "--wait", "1s"],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="network.json",
        source_surface="cdp:network",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    _write_command_log(run_root, executed, artifacts)

    if snapshot_result.status == "tool_error":
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            exit_code=6,
            payload=_tool_error_payload(
                intent.query_id,
                run_id,
                browser_mode,
                snapshot_result,
                artifacts,
                source_surfaces,
                query_population,
            ),
        )
    nonfatal_warnings: list[str] = []
    if network_result.status == "tool_error":
        nonfatal_warnings.append(
            "network evidence capture failed after snapshot evidence was collected"
        )

    extracted_results = extract_primary_results(
        snapshot_result.json_payload or {},
        source_surface="primary-results-visible-text",
        evidence_artifact=str(run_root / "snapshot.json"),
        confidence="weak",
    )
    price_observations_written = _write_price_observations(
        state=state,
        intent=intent,
        results=extracted_results,
        run_id=run_id,
    )
    if extracted_results:
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            exit_code=0,
            payload={
                "query_id": intent.query_id,
                "status": "ok",
                "confidence": "weak",
                "live_mode": True,
                "browser_mode": browser_mode,
                "target_url": target_url,
                "query_population": query_population["payload"],
                "results": extracted_results,
                "unsupported": [],
                "warnings": [
                    *query_population["warnings"],
                    *nonfatal_warnings,
                    "primary result rows were parsed from visible text evidence; missing optional fields are left empty or null",
                    *(
                        ["live form interaction is experimental and limited to observed controls"]
                        if interact_with_form
                        else []
                    ),
                    "Google Flights URL query/protobuf encoding is not guessed",
                ],
                "evidence": {
                    "run_id": run_id,
                    "artifacts": artifacts,
                    "source_surfaces": source_surfaces,
                },
                "cache": {
                    "database_path": str(state.database_path),
                    "price_observations_written": price_observations_written,
                },
            },
        )

    return await _finish_live_search(
        adapter=adapter,
        page_id=page_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        exit_code=0,
        payload={
            "query_id": intent.query_id,
            "status": "experimental",
            "confidence": "weak",
            "live_mode": True,
            "browser_mode": browser_mode,
            "target_url": target_url,
            "query_population": query_population["payload"],
            "results": [],
            "unsupported": [
                *query_population["unsupported"],
                {
                    "field": "live_result_extraction",
                    "status": "deferred",
                    "reason": "this live mode captures cdp evidence; durable Google Flights result extraction still requires focused evidence and parser tests",
                },
            ],
            "warnings": [
                *query_population["warnings"],
                *nonfatal_warnings,
                "live cdp mode is the default search path; no primary result rows were extracted from this snapshot",
                *(
                    ["live form interaction is experimental and limited to observed controls"]
                    if interact_with_form
                    else []
                ),
                "Google Flights URL query/protobuf encoding is not guessed",
            ],
            "evidence": {
                "run_id": run_id,
                "artifacts": artifacts,
                "source_surfaces": source_surfaces,
            },
            "cache": {
                "database_path": str(state.database_path),
                "price_observations_written": price_observations_written,
            },
        },
    )


async def _run_step(
    *,
    adapter: CdpAdapter,
    args: list[str],
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    artifact_name: str,
    source_surface: str,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
) -> CdpResult:
    result = await adapter.run_json(
        args,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
    )
    artifact_path = run_root / artifact_name
    _write_json(artifact_path, _result_artifact(result))
    artifacts.append(str(artifact_path))
    source_surfaces.append(source_surface)
    executed.append(
        {
            "args": args,
            "browser_mode": browser_mode,
            "timeout_seconds": timeout_seconds,
            "artifact": str(artifact_path),
            "status": result.status,
            "exit_code": result.exit_code,
        }
    )
    return result


async def _finish_live_search(
    *,
    adapter: CdpAdapter,
    page_id: str,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
    exit_code: int,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    await close_managed_page(
        adapter=adapter,
        page_id=page_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        warnings=_payload_warnings(payload),
    )
    _write_command_log(run_root, executed, artifacts)
    return exit_code, payload


def _payload_warnings(payload: dict[str, Any]) -> list[str] | None:
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        return warnings
    return None


def _write_command_log(
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
) -> None:
    command_log = run_root / "command-log.json"
    _write_json(command_log, executed)
    if str(command_log) not in artifacts:
        artifacts.append(str(command_log))


def _result_artifact(result: CdpResult) -> dict[str, Any]:
    return {
        "argv": result.argv,
        "browser_mode": result.browser_mode,
        "returncode": result.returncode,
        "status": result.status,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "json_payload": result.json_payload,
        "stop_state": result.stop_state,
        "fallback": result.fallback,
        "error": result.error,
        "timeout": result.timeout,
    }


def _stop_payload(
    *,
    intent_query_id: str,
    run_id: str,
    browser_mode: BrowserMode,
    result: CdpResult,
    artifacts: list[str],
    source_surfaces: list[str],
    target_url: str,
    query_population: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query_id": intent_query_id,
        "status": result.status if result.status in BLOCKED_STOP_STATES else "blocked",
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "target_url": target_url,
        "stop_state": result.stop_state or result.status,
        "query_population": query_population["payload"],
        "results": [],
        "unsupported": [*query_population["unsupported"]],
        "warnings": [
            *query_population["warnings"],
            "live search stopped before bypassing a browser safety boundary",
        ],
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }
    if result.fallback:
        payload["fallback"] = result.fallback
    return payload


def _tool_error_payload(
    query_id: str,
    run_id: str,
    browser_mode: BrowserMode,
    result: CdpResult,
    artifacts: list[str],
    source_surfaces: list[str],
    query_population: dict[str, Any],
) -> dict[str, Any]:
    return {
        "query_id": query_id,
        "status": "tool_error",
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "query_population": query_population["payload"],
        "results": [],
        "unsupported": [*query_population["unsupported"]],
        "warnings": [*query_population["warnings"]],
        "error": result.error,
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }


def _unsupported_live_form_payload(
    query_id: str,
    run_id: str,
    browser_mode: BrowserMode,
    error: UnsupportedLiveForm,
    artifacts: list[str],
    source_surfaces: list[str],
) -> dict[str, Any]:
    return {
        "query_id": query_id,
        "status": "unsupported",
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "results": [],
        "unsupported": [
            {
                "field": error.field,
                "value": error.value,
                "status": "deferred",
                "reason": error.reason,
            }
        ],
        "warnings": [],
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }


def _build_query_population(intent: SearchIntent, *, use_query_state: bool) -> dict[str, Any]:
    if not use_query_state:
        payload = {
            "status": "form",
            "confidence": "weak",
            "params": [],
            "source_surfaces": ["cdp:form"],
        }
        return {
            "params": {},
            "source_surfaces": [],
            "unsupported": [],
            "warnings": [
                "live form interaction will populate query state through observed controls"
            ],
            "payload": payload,
            "artifact": payload,
        }

    try:
        state = build_query_state(intent)
    except UnsupportedQueryState as exc:
        unsupported = [
            {
                "field": exc.field,
                "value": exc.value,
                "status": "deferred",
                "reason": exc.reason,
            }
        ]
        payload = {
            "status": "unsupported",
            "confidence": "unknown",
            "params": [],
            "unsupported": unsupported,
        }
        return {
            "params": {},
            "source_surfaces": ["query-state:unsupported"],
            "unsupported": unsupported,
            "warnings": [
                "live search opened the Google Flights shell because this intent could not be encoded into evidence-backed query state"
            ],
            "payload": payload,
            "artifact": payload,
        }

    payload = {
        "status": "encoded",
        "confidence": state.confidence,
        "params": list(state.params),
        "source_surfaces": state.source_surfaces,
        "evidence_refs": state.evidence_refs,
    }
    return {
        "params": state.params,
        "source_surfaces": state.source_surfaces,
        "unsupported": [],
        "warnings": state.warnings,
        "payload": payload,
        "artifact": payload,
    }


def _is_stop_result(result: CdpResult) -> bool:
    return bool(result.fallback) or result.status in BLOCKED_STOP_STATES


def _is_recoverable_context_error(result: CdpResult) -> bool:
    if result.status != "tool_error":
        return False
    payload = result.json_payload or {}
    message = str(payload.get("message") or result.error or result.stdout)
    return (
        payload.get("code") == "connection_failed"
        and "Cannot find default execution context" in message
    )


def _write_price_observations(
    *,
    state: AppState,
    intent: SearchIntent,
    results: list[dict[str, Any]],
    run_id: str,
) -> int:
    cache = price_cache_for_state(state)
    written = 0
    captured_at = datetime.now(timezone.utc)
    for result in results:
        price = result.get("price")
        if not isinstance(price, dict) or price.get("amount") is None:
            continue
        currency = str(price.get("currency") or result.get("currency") or intent.currency)
        cache.put_price(
            cache_key=_price_cache_key(intent, result, currency),
            query_id=intent.query_id,
            departure_date=intent.departure_window.start,
            return_date=_return_date(intent),
            currency=currency,
            price_amount=price["amount"],
            price_payload=_price_payload(result),
            captured_at=captured_at,
            source_run_id=run_id,
        )
        written += 1
    return written


def _price_cache_key(intent: SearchIntent, result: dict[str, Any], currency: str) -> str:
    route = _route_key(result) or f"{intent.origin.text}-{intent.destination.text}"
    return "|".join(
        [
            intent.query_id,
            route,
            intent.departure_window.start,
            _return_date(intent) or "",
            currency,
            intent.cabin,
            _passenger_key(intent),
            str(result.get("result_id") or ""),
        ]
    )


def _route_key(result: dict[str, Any]) -> str:
    origins = result.get("origin_airports")
    destinations = result.get("destination_airports")
    if isinstance(origins, list) and origins and isinstance(destinations, list) and destinations:
        return f"{origins[0]}-{destinations[0]}"
    return ""


def _return_date(intent: SearchIntent) -> str | None:
    if intent.trip_type != "round_trip" or intent.return_window is None:
        return None
    return intent.return_window.start


def _passenger_key(intent: SearchIntent) -> str:
    passengers = intent.passengers
    return (
        f"adults={passengers.adults};children={passengers.children};"
        f"infants_in_seat={passengers.infants_in_seat};"
        f"infants_on_lap={passengers.infants_on_lap}"
    )


def _price_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "result_id": result.get("result_id"),
        "source_surface": result.get("source_surface"),
        "confidence": result.get("confidence"),
        "origin_airports": result.get("origin_airports", []),
        "destination_airports": result.get("destination_airports", []),
        "departure_times": result.get("departure_times", []),
        "arrival_times": result.get("arrival_times", []),
        "carriers": result.get("carriers", []),
        "duration_text": result.get("duration_text"),
        "duration_minutes": result.get("duration_minutes"),
        "stops": result.get("stops"),
        "layovers": result.get("layovers", []),
        "price": result.get("price"),
        "currency": result.get("currency"),
        "emissions": result.get("emissions"),
        "baggage_summary": result.get("baggage_summary"),
        "evidence": {
            "source_surfaces": (result.get("evidence") or {}).get("source_surfaces", []),
        },
    }


def _batch_run_id(run_id: str | None, query_id: str, index: int) -> str:
    if run_id is None:
        return _new_run_id(query_id)
    safe_query_id = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in query_id.lower())
    return f"{run_id}-{index}-{safe_query_id}"


def _aggregate_exit_code(exit_codes: list[int]) -> int:
    for exit_code in (6, 5, 4, 3, 2):
        if exit_code in exit_codes:
            return exit_code
    return 0


def _google_flights_url(
    language: str,
    currency: str,
    *,
    params: dict[str, str] | None = None,
) -> str:
    query_params = dict(params or {})
    query_params.setdefault("hl", language)
    query_params.setdefault("curr", currency)
    return f"{GOOGLE_FLIGHTS_URL}?{urlencode(query_params)}"


def _page_id(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    page = payload.get("page")
    if isinstance(page, dict) and isinstance(page.get("id"), str):
        return page["id"]
    target = payload.get("target")
    if isinstance(target, dict) and isinstance(target.get("id"), str):
        return target["id"]
    if isinstance(payload.get("id"), str):
        return payload["id"]
    return ""


def _new_run_id(query_id: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_query_id = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in query_id.lower())
    return f"gf-{now}-{safe_query_id}"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")
