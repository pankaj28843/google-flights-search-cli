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


def test_search_defaults_to_live_cdp_without_tdd_replay(
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
    assert calls[0]["rank_objectives"] == []
    assert calls[0]["top_k"] == 0


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


def test_search_passes_ranking_and_tab_budget_options(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run_live_search(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        calls.append(kwargs)
        return 0, {"status": "ok", "warnings": [], "results": []}

    monkeypatch.setattr(cli, "run_live_search", fake_run_live_search)

    result = runner.invoke(
        cli.app,
        [
            "search",
            "--input-json",
            str(write_intent(tmp_path)),
            "--rank",
            "cheapest,fastest,least-layover,balanced",
            "--top-k",
            "10",
            "--deny-transit",
            "DOH",
            "--managed-tab-policy",
            "reuse",
            "--max-tabs",
            "3",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["rank_objectives"] == [
        "cheapest",
        "fastest",
        "least-layover",
        "balanced",
    ]
    assert calls[0]["top_k"] == 10
    assert calls[0]["deny_transit"] == ["DOH"]
    assert calls[0]["managed_tab_policy"] == "reuse"
    assert calls[0]["max_tabs"] == 3


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


def test_route_resolve_defaults_to_live_cdp_without_tdd_replay(
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


def test_preflight_google_flights_uses_public_synthetic_smoke(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run_google_flights_preflight(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        calls.append(kwargs)
        return 0, {
            "status": "ok",
            "preflight_route": "JFK-SFO",
            "privacy": "synthetic public route",
            "selections": [],
        }

    monkeypatch.setattr(cli, "run_google_flights_preflight", fake_run_google_flights_preflight)

    result = runner.invoke(
        cli.app,
        [
            "preflight",
            "google-flights",
            "--browser-mode",
            "headless",
            "--consent-choice",
            "reject-all",
            "--top-k",
            "5",
            "--selection-concurrency",
            "4",
            "--max-tabs",
            "7",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["preflight_route"] == "JFK-SFO"
    assert calls[0]["browser_mode"] == "headless"
    assert calls[0]["consent_choice"] == "reject-all"
    assert calls[0]["top_k"] == 5
    assert calls[0]["selection_concurrency"] == 4
    assert calls[0]["max_tabs"] == 7
    assert calls[0]["project_root"] == tmp_path


def test_preflight_google_flights_rejects_unknown_consent_choice(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "preflight",
            "google-flights",
            "--consent-choice",
            "maybe",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 6, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "tool_error"
    assert "--consent-choice" in payload["error"]


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


def test_itinerary_select_passes_independent_leg_and_reuse_options(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run_live_itinerary_selection(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        calls.append(kwargs)
        return 0, {"status": "ok", "warnings": [], "booking_url": "https://example.test"}

    monkeypatch.setattr(cli, "run_live_itinerary_selection", fake_run_live_itinerary_selection)

    result = runner.invoke(
        cli.app,
        [
            "itinerary",
            "select",
            "--search-url",
            "https://www.google.com/travel/flights/search?tfs=redacted",
            "--outbound-row-rank",
            "2",
            "--return-row-rank",
            "1",
            "--outbound-match-text",
            "7:40 AM",
            "--return-match-text",
            "1:00 PM",
            "--reuse-target",
            "google-flights",
            "--max-tabs",
            "3",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["outbound_row_rank"] == 2
    assert calls[0]["return_row_rank"] == 1
    assert calls[0]["outbound_match_text"] == "7:40 AM"
    assert calls[0]["return_match_text"] == "1:00 PM"
    assert calls[0]["reuse_target"] == "google-flights"
    assert calls[0]["max_tabs"] == 3
