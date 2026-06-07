"""Offline service functions for the first agentic CLI implementation."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from gflights.codec import CodecError, decode_query_value
from gflights.domain import SearchIntent


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
    project_root = path.resolve()
    config_root = project_root / ".gflights"
    artifacts_root = config_root / "artifacts"
    fixture_root = config_root / "fixtures"
    run_root = config_root / "runs"
    for directory in (artifacts_root, fixture_root, run_root):
        directory.mkdir(parents=True, exist_ok=True)
    config_path = config_root / "config.json"
    config = {
        "version": 1,
        "artifacts_root": str(artifacts_root),
        "fixture_root": str(fixture_root),
        "run_root": str(run_root),
        "live_google_flights_by_default": False,
        "browser_default_mode": "headless",
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    return {
        "status": "ok",
        "project_root": str(project_root),
        "config_path": str(config_path),
        "artifacts_root": str(artifacts_root),
        "fixture_root": str(fixture_root),
        "run_root": str(run_root),
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


def scan_dates(input_json: Path, offline_fixtures: Path) -> list[dict[str, Any]]:
    return [_date_scan_result(intent, offline_fixtures) for intent in load_intents(input_json)]


def _date_scan_result(intent: SearchIntent, offline_fixtures: Path) -> dict[str, Any]:
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
    }


def _inclusive_days(start: str, end: str) -> int:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    return max((end_date - start_date).days + 1, 1)


def replay_fixture(path: Path) -> tuple[int, dict[str, Any]]:
    fixture = load_json(path)
    expected = fixture["expected"]
    evidence = {
        "fixture_id": fixture["fixture_id"],
        "run_id": fixture["run_id"],
        "source_surfaces": [fixture["source_surface"]],
        "artifacts": [str(path)],
    }

    if fixture["fixture_type"] == "blocked_stop_state":
        return expected["exit_code"], {
            "status": expected["status"],
            "browser_mode": expected["browser_mode"],
            "fallback": expected["fallback"],
            "confidence": fixture["confidence"],
            "evidence": evidence,
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


def search_offline(input_json: Path, offline_fixtures: Path) -> tuple[int, dict[str, Any]]:
    intent = SearchIntent.model_validate(load_json(input_json))
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
    }


def doctor_report() -> dict[str, Any]:
    return {
        "status": "ok",
        "browser": {
            "default_mode": "headless",
            "headed_fallback_allowed": True,
        },
        "validation": {
            "live_google_flights_by_default": False,
            "default_command": "make validate",
        },
        "toolchain": {
            "python_project": True,
            "package": "google-flights-search-cli",
        },
    }
