from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from gflights import cli


runner = CliRunner()


def write_intent(path: Path) -> Path:
    intent_path = path / "intent.json"
    intent_path.write_text(
        json.dumps(
            {
                "query_id": "default-live-cph-del",
                "origin": {"text": "CPH", "kind": "airport_code"},
                "destination": {"text": "DEL", "kind": "airport_code"},
                "trip_type": "round_trip",
                "departure_window": {"start": "2026-10-01", "end": "2026-10-01"},
                "return_window": {"start": "2026-11-24", "end": "2026-11-24"},
                "passengers": {"adults": 1},
                "cabin": "economy",
                "currency": "EUR",
                "language": "en",
                "sort": "top_flights",
            }
        )
    )
    return intent_path


def test_search_defaults_to_live_cdp_without_offline_fixtures(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run_live_search(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        calls.append(kwargs)
        return 0, {"status": "experimental", "warnings": [], "results": []}

    monkeypatch.setattr(cli, "run_live_search", fake_run_live_search)

    result = runner.invoke(
        cli.app,
        [
            "search",
            "--input-json",
            str(write_intent(tmp_path)),
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "experimental"
    assert calls[0]["browser_mode"] == "headless"
    assert calls[0]["interact_with_form"] is False


def test_search_live_form_uses_live_cdp_by_default(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run_live_search(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        calls.append(kwargs)
        return 0, {"status": "experimental", "warnings": [], "results": []}

    monkeypatch.setattr(cli, "run_live_search", fake_run_live_search)

    result = runner.invoke(
        cli.app,
        [
            "search",
            "--input-json",
            str(write_intent(tmp_path)),
            "--project-root",
            str(tmp_path),
            "--live-form",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["interact_with_form"] is True


def test_itinerary_inspect_uses_headless_live_cdp_by_default(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run_live_itinerary_inspection(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        calls.append(kwargs)
        return 4, {
            "status": "payment_or_booking_boundary",
            "warnings": [],
            "itinerary": None,
        }

    monkeypatch.setattr(
        cli,
        "run_live_itinerary_inspection",
        fake_run_live_itinerary_inspection,
    )

    result = runner.invoke(
        cli.app,
        [
            "itinerary",
            "inspect",
            "--booking-url",
            "https://www.google.com/travel/flights/booking?tfs=redacted",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 4, result.output
    assert json.loads(result.stdout)["status"] == "payment_or_booking_boundary"
    assert calls[0]["browser_mode"] == "headless"
    assert calls[0]["project_root"] == tmp_path


def test_route_resolve_defaults_to_live_cdp_without_offline_fixtures(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run_live_route_resolution(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        calls.append(kwargs)
        return 3, {
            "status": "unsupported",
            "warnings": [],
            "selected": None,
            "choices": [],
            "unsupported": [
                {
                    "field": "route.resolve.live_autocomplete_extraction",
                    "status": "deferred",
                }
            ],
        }

    monkeypatch.setattr(cli, "run_live_route_resolution", fake_run_live_route_resolution)

    result = runner.invoke(
        cli.app,
        [
            "route",
            "resolve",
            "--input-text",
            "CPH",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 3, result.output
    assert json.loads(result.stdout)["status"] == "unsupported"
    assert calls[0]["input_text"] == "CPH"
    assert calls[0]["browser_mode"] == "headless"
    assert calls[0]["project_root"] == tmp_path


def test_dates_scan_live_probe_requires_positive_max_probes(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "dates",
            "scan",
            "--input-json",
            str(write_intent(tmp_path)),
            "--live-probe",
            "--max-probes",
            "0",
            "--json",
        ],
    )

    assert result.exit_code == 6, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "tool_error"
    assert payload["error"] == "--live-probe requires --max-probes greater than zero"
