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
                "location": "DK",
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
    assert calls[0]["browser_mode"] == "headed"
    assert calls[0]["max_tabs"] == 50
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


def test_search_url_only_encodes_target_urls_without_cdp(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    async def fake_run_live_search(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        raise AssertionError("url-only search must not open live cdp")

    monkeypatch.setattr(cli, "run_live_search", fake_run_live_search)

    result = runner.invoke(
        cli.app,
        [
            "search",
            "--input-json",
            str(write_intent(tmp_path)),
            "--url-only",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload[0]["status"] == "encoded"
    assert payload[0]["live_mode"] is False
    assert payload[0]["target_url"].startswith("https://www.google.com/travel/flights/search?")
    assert "tfs=" in payload[0]["target_url"]
    assert "hl=en" in payload[0]["target_url"]
    assert "curr=EUR" in payload[0]["target_url"]
    assert "gl=DK" in payload[0]["target_url"]
    assert payload[0]["results"] == []


def test_itinerary_inspect_uses_headed_live_cdp_by_default(
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
    assert calls[0]["browser_mode"] == "headed"
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
    assert calls[0]["browser_mode"] == "headed"
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
            "--return-top-k",
            "3",
            "--min-complete-selections",
            "3",
            "--selection-concurrency",
            "4",
            "--date-range-count",
            "2",
            "--search-deadline-seconds",
            "90",
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
    assert calls[0]["return_top_k"] == 3
    assert calls[0]["min_complete_selections"] == 3
    assert calls[0]["selection_concurrency"] == 4
    assert calls[0]["date_range_count"] == 2
    assert calls[0]["search_deadline_seconds"] == 90.0
    assert calls[0]["max_tabs"] == 7
    assert calls[0]["project_root"] == tmp_path


def test_preflight_google_flights_defaults_to_headed_with_50_tab_capacity(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run_google_flights_preflight(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        calls.append(kwargs)
        return 0, {"status": "ok", "selections": []}

    monkeypatch.setattr(cli, "run_google_flights_preflight", fake_run_google_flights_preflight)

    result = runner.invoke(
        cli.app,
        [
            "preflight",
            "google-flights",
            "--min-complete-selections",
            "1",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["browser_mode"] == "headed"
    assert calls[0]["max_tabs"] == 50


def test_preflight_google_flights_rejects_min_complete_above_top_k(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "preflight",
            "google-flights",
            "--top-k",
            "3",
            "--return-top-k",
            "1",
            "--min-complete-selections",
            "4",
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 6, result.output
    assert "--min-complete-selections cannot exceed --top-k * --return-top-k" in result.output


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


def test_preflight_headless_heal_uses_accept_all_recovery(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run_headless_heal(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        calls.append(kwargs)
        return 0, {
            "status": "ok",
            "browser_mode": "headless",
            "consent": {"status": "ok", "choice": "accept-all"},
        }

    monkeypatch.setattr(cli, "run_headless_heal", fake_run_headless_heal)

    result = runner.invoke(
        cli.app,
        [
            "preflight",
            "headless-heal",
            "--project-root",
            str(tmp_path),
            "--max-tabs",
            "9",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert calls[0]["browser_mode"] == "headless"
    assert calls[0]["consent_choice"] == "accept-all"
    assert calls[0]["close_google_flights_tabs"] is True
    assert calls[0]["repair"] is True
    assert calls[0]["restart_daemon"] is False
    assert calls[0]["max_tabs"] == 9
    assert calls[0]["project_root"] == tmp_path


def test_preflight_headless_heal_rejects_unknown_consent_choice(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "preflight",
            "headless-heal",
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
            "--allow-over-budget",
            "--operation-retries",
            "4",
            "--timeout-seconds",
            "23",
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
    assert calls[0]["allow_over_budget"] is True
    assert calls[0]["operation_retries"] == 4
    assert calls[0]["timeout_seconds"] == 23


def test_itinerary_select_defaults_to_headed_with_50_tab_capacity(
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
            "--project-root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["browser_mode"] == "headed"
    assert calls[0]["max_tabs"] == 50
