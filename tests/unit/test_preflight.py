from __future__ import annotations

import asyncio
import json
from typing import Any

from gflights import preflight


class FakeCdpAdapter:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def run_json(
        self,
        args: list[str],
        *,
        browser_mode: preflight.BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> preflight.CdpResult:
        del timeout_seconds
        self.calls.append(list(args))
        payload: dict[str, Any] = {"ok": True}
        if args == ["daemon", "health"]:
            payload = {"ok": True, "health": {"state": "healthy"}}
        elif args == ["pages"]:
            payload = {
                "ok": True,
                "pages": [
                    {
                        "id": "google-tab-1",
                        "title": "Copenhagen to New Delhi | Google Flights",
                        "url": "https://www.google.com/travel/flights/search?bad=1",
                    },
                    {
                        "id": "other-tab",
                        "title": "Example",
                        "url": "https://example.com",
                    },
                ],
            }
        elif args[:2] == ["page", "close"]:
            payload = {"ok": True, "status": "ok"}
        elif args[:2] == ["daemon", "health-check"]:
            payload = {"ok": True, "health": {"state": "healthy"}}
        elif args[0] == "open":
            payload = {"ok": True, "page": {"id": "consent-page", "url": args[1]}}
        elif args[:2] == ["wait", "load-state"]:
            payload = {"ok": True, "status": "ok"}
        elif args[:2] == ["wait", "eval"]:
            payload = {"ok": True, "result": {"value": "body_ready"}}
        elif args[:2] == ["text", "body"]:
            text_calls = [call for call in self.calls if call[:2] == ["text", "body"]]
            text = (
                "Before you continue to Google Accept all"
                if len(text_calls) == 1
                else "Google Flights"
            )
            payload = {"ok": True, "items": [{"text": text}]}
        elif args[:2] == ["click", "Accept all"]:
            payload = {"ok": True, "status": "ok", "clicked": True}
        elif args[:3] == ["storage", "cookies", "list"]:
            payload = {"ok": True, "cookies": [{"name": "SOCS"}, {"name": "NID"}]}
        return preflight.CdpResult(
            argv=["cdp", *args],
            browser_mode=browser_mode,
            returncode=0,
            stdout="{}",
            stderr="",
            status=str(payload.get("status") or "ok"),
            exit_code=0,
            json_payload=payload,
        )


class FakeDiagnosticCdpAdapter(FakeCdpAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.pages_calls = 0

    async def run_json(
        self,
        args: list[str],
        *,
        browser_mode: preflight.BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> preflight.CdpResult:
        if args == ["pages"]:
            del timeout_seconds
            self.calls.append(list(args))
            self.pages_calls += 1
            diagnostic_tab = {
                "id": f"diagnostic-tab-{self.pages_calls}",
                "title": "cdp-headless-health",
                "url": "data:text/html,data-cdp-health",
            }
            payload = {
                "ok": True,
                "pages": [
                    {
                        "id": "google-tab-1",
                        "title": "Copenhagen to New Delhi | Google Flights",
                        "url": "https://www.google.com/travel/flights/search?bad=1",
                    },
                    diagnostic_tab,
                    {
                        "id": "other-tab",
                        "title": "Example",
                        "url": "https://example.com",
                    },
                ],
            }
            return preflight.CdpResult(
                argv=["cdp", *args],
                browser_mode=browser_mode,
                returncode=0,
                stdout="{}",
                stderr="",
                status="ok",
                exit_code=0,
                json_payload=payload,
            )
        return await super().run_json(
            args,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
        )


class HealthCheckNavigateFailedAdapter(FakeCdpAdapter):
    async def run_json(
        self,
        args: list[str],
        *,
        browser_mode: preflight.BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> preflight.CdpResult:
        if args[:2] == ["daemon", "health-check"]:
            del timeout_seconds
            self.calls.append(list(args))
            payload = {
                "ok": False,
                "state": "failed",
                "message": "headless health-check failed: navigate_failed",
                "daemon": {"health": {"state": "healthy"}},
            }
            return preflight.CdpResult(
                argv=["cdp", *args],
                browser_mode=browser_mode,
                returncode=1,
                stdout="{}",
                stderr="",
                status="tool_error",
                exit_code=6,
                error="headless health-check failed: navigate_failed",
                json_payload=payload,
            )
        return await super().run_json(
            args,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
        )


def test_headless_heal_repairs_accepts_consent_and_closes_google_tabs(tmp_path: Any) -> None:
    adapter = FakeCdpAdapter()

    exit_code, payload = asyncio.run(
        preflight.run_headless_heal(
            project_root=tmp_path,
            consent_choice="accept-all",
            adapter=adapter,  # type: ignore[arg-type]
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["closed_google_flights_tab_count"] == 1
    assert payload["closed_google_flights_tabs"][0]["id"] == "google-tab-1"
    assert payload["consent"]["status"] == "ok"
    assert payload["consent"]["needed"] is True
    assert payload["consent"]["clicked"] is True
    assert payload["consent"]["locator"]["label"] == "Accept all"
    assert "SOCS" in payload["google_cookie_names"]
    assert [
        "daemon",
        "health-check",
        "--repair",
        "--out-dir",
        str(tmp_path / "runs" / payload["evidence"]["run_id"] / "cdp-health-check"),
    ] in adapter.calls
    assert ["page", "close", "--target", "google-tab-1"] in adapter.calls
    assert ["page", "close", "--target", "other-tab"] not in adapter.calls


def test_headless_heal_reports_failed_health_check_when_final_health_recovers(
    tmp_path: Any,
) -> None:
    adapter = HealthCheckNavigateFailedAdapter()

    exit_code, payload = asyncio.run(
        preflight.run_headless_heal(
            project_root=tmp_path,
            consent_choice="skip",
            adapter=adapter,  # type: ignore[arg-type]
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["health_check"]["status"] == "tool_error"
    assert payload["health_check"]["state"] == "failed"
    assert payload["health_after"]["state"] == "healthy"
    assert any("final daemon health is healthy" in warning for warning in payload["warnings"])


def test_headless_heal_closes_cdp_diagnostic_tabs_without_closing_other_tabs(
    tmp_path: Any,
) -> None:
    adapter = FakeDiagnosticCdpAdapter()

    exit_code, payload = asyncio.run(
        preflight.run_headless_heal(
            project_root=tmp_path,
            consent_choice="accept-all",
            adapter=adapter,  # type: ignore[arg-type]
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["closed_google_flights_tab_count"] == 1
    assert payload["closed_diagnostic_tab_count"] == 2
    assert [item["id"] for item in payload["closed_diagnostic_tabs"]] == [
        "diagnostic-tab-1",
        "diagnostic-tab-2",
    ]
    assert ["page", "close", "--target", "google-tab-1"] in adapter.calls
    assert ["page", "close", "--target", "diagnostic-tab-1"] in adapter.calls
    assert ["page", "close", "--target", "diagnostic-tab-2"] in adapter.calls
    assert ["page", "close", "--target", "other-tab"] not in adapter.calls


def test_headless_heal_can_restart_daemon_before_cleanup(tmp_path: Any) -> None:
    adapter = FakeCdpAdapter()

    exit_code, payload = asyncio.run(
        preflight.run_headless_heal(
            project_root=tmp_path,
            consent_choice="skip",
            restart_daemon=True,
            adapter=adapter,  # type: ignore[arg-type]
        )
    )

    assert exit_code == 0
    assert payload["restart_daemon_requested"] is True
    assert payload["restart_daemon"]["status"] == "ok"
    assert ["daemon", "restart", "--reconnect", "30s"] in adapter.calls
    assert adapter.calls.index(["daemon", "restart", "--reconnect", "30s"]) < adapter.calls.index(
        ["pages"]
    )


def test_google_flights_preflight_selects_combinations_concurrently(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    active = 0
    max_active = 0
    selected_pairs: list[tuple[int, int]] = []
    selection_run_ids: list[str] = []

    async def fake_run_live_search(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        return (
            0,
            {
                "status": "ok",
                "target_url": "https://www.google.com/travel/flights/search?synthetic=1",
                "results": [{"rank": rank} for rank in range(1, 6)],
                "rankings": {},
            },
        )

    async def fake_run_live_itinerary_selection(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        nonlocal active, max_active
        outbound_rank = int(kwargs["outbound_row_rank"])
        return_rank = int(kwargs["return_row_rank"])
        selected_pairs.append((outbound_rank, return_rank))
        selection_run_ids.append(str(kwargs["run_id"]))
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return (
            0,
            {
                "status": "ok",
                "booking_url": (
                    "https://www.google.com/travel/flights/booking?"
                    f"outbound={outbound_rank}&return={return_rank}"
                ),
                "selected_outbound": {"row_rank": outbound_rank},
                "selected_return": {"row_rank": return_rank},
                "booking_options": [
                    {
                        "provider": "Synthetic",
                        "price": {"amount": outbound_rank * 100 + return_rank},
                    }
                ],
                "warnings": [],
                "evidence": {"run_id": f"selection-{outbound_rank}-{return_rank}", "artifacts": []},
            },
        )

    monkeypatch.setattr(preflight, "run_live_search", fake_run_live_search)
    monkeypatch.setattr(
        preflight,
        "run_live_itinerary_selection",
        fake_run_live_itinerary_selection,
    )

    exit_code, payload = asyncio.run(
        preflight.run_google_flights_preflight(
            project_root=tmp_path,
            consent_choice="skip",
            top_k=3,
            return_top_k=2,
            selection_concurrency=3,
            max_tabs=7,
            adapter=FakeCdpAdapter(),  # type: ignore[arg-type]
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["selection_concurrency"] == 3
    assert payload["outbound_selection_count"] == 3
    assert payload["return_selection_count"] == 2
    assert payload["selection_count"] == 6
    assert payload["complete_selection_count"] == 6
    assert selected_pairs == [(1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (3, 2)]
    assert len(selection_run_ids) == len(set(selection_run_ids)) == 6
    assert selection_run_ids == [
        f"{payload['evidence']['run_id']}-route-01-selection-o{outbound:02d}-r{return_:02d}"
        for outbound, return_ in selected_pairs
    ]
    assert max_active == 3
    assert all("payload" not in item for item in payload["selections"])
    assert [
        (item["outbound_rank"], item["return_rank"]) for item in payload["selections"]
    ] == selected_pairs
    assert all(item["booking_options_status"] == "available" for item in payload["selections"])


def test_google_flights_preflight_blocks_partial_rank_completion(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    async def fake_run_live_search(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        return (
            0,
            {
                "status": "ok",
                "target_url": "https://www.google.com/travel/flights/search?synthetic=1",
                "results": [{"rank": rank} for rank in range(1, 4)],
                "rankings": {},
            },
        )

    async def fake_run_live_itinerary_selection(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        outbound_rank = int(kwargs["outbound_row_rank"])
        return_rank = int(kwargs["return_row_rank"])
        combination_number = (outbound_rank - 1) * 2 + return_rank
        booking_url = (
            f"https://www.google.com/travel/flights/booking?combination={combination_number}"
            if combination_number < 3
            else f"https://www.google.com/travel/flights/search?combination={combination_number}"
        )
        return (
            0,
            {
                "status": "ok",
                "booking_url": booking_url,
                "selected_outbound": {"row_rank": outbound_rank},
                "selected_return": {"row_rank": return_rank},
                "booking_options": [
                    {"provider": "Synthetic", "price": {"amount": combination_number}}
                ]
                if combination_number < 3
                else [],
                "warnings": [],
                "evidence": {
                    "run_id": f"selection-{outbound_rank}-{return_rank}",
                    "artifacts": [],
                },
            },
        )

    monkeypatch.setattr(preflight, "run_live_search", fake_run_live_search)
    monkeypatch.setattr(
        preflight,
        "run_live_itinerary_selection",
        fake_run_live_itinerary_selection,
    )

    exit_code, payload = asyncio.run(
        preflight.run_google_flights_preflight(
            project_root=tmp_path,
            consent_choice="skip",
            top_k=2,
            return_top_k=2,
            selection_concurrency=3,
            max_tabs=5,
            adapter=FakeCdpAdapter(),  # type: ignore[arg-type]
        )
    )

    assert exit_code == 4
    assert payload["status"] == "blocked"
    assert payload["selection_count"] == 4
    assert payload["complete_selection_count"] == 2
    assert payload["selections"][2]["booking_options_status"] == "missing"
    assert any(
        "selected 2/4 requested outbound/return combinations" in warning
        for warning in payload["warnings"]
    )


def test_google_flights_preflight_accepts_partial_rank_completion_with_minimum(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    async def fake_run_live_search(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        return (
            0,
            {
                "status": "ok",
                "target_url": "https://www.google.com/travel/flights/search?synthetic=1",
                "results": [{"rank": rank} for rank in range(1, 6)],
                "rankings": {},
            },
        )

    async def fake_run_live_itinerary_selection(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        outbound_rank = int(kwargs["outbound_row_rank"])
        return_rank = int(kwargs["return_row_rank"])
        combination_number = (outbound_rank - 1) * 2 + return_rank
        return (
            0,
            {
                "status": "ok",
                "booking_url": (
                    "https://www.google.com/travel/flights/booking?"
                    f"combination={combination_number}"
                )
                if combination_number <= 3
                else (
                    f"https://www.google.com/travel/flights/search?combination={combination_number}"
                ),
                "selected_outbound": {"row_rank": outbound_rank},
                "selected_return": {"row_rank": return_rank},
                "booking_options": [
                    {"provider": "Synthetic", "price": {"amount": combination_number}}
                ]
                if combination_number <= 3
                else [],
                "warnings": [],
                "evidence": {
                    "run_id": f"selection-{outbound_rank}-{return_rank}",
                    "artifacts": [],
                },
            },
        )

    monkeypatch.setattr(preflight, "run_live_search", fake_run_live_search)
    monkeypatch.setattr(
        preflight,
        "run_live_itinerary_selection",
        fake_run_live_itinerary_selection,
    )

    exit_code, payload = asyncio.run(
        preflight.run_google_flights_preflight(
            project_root=tmp_path,
            consent_choice="skip",
            top_k=3,
            return_top_k=2,
            min_complete_selections=3,
            selection_concurrency=5,
            max_tabs=5,
            adapter=FakeCdpAdapter(),  # type: ignore[arg-type]
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["outbound_selection_count"] == 3
    assert payload["return_selection_count"] == 2
    assert payload["selection_count"] == 6
    assert payload["minimum_complete_selection_count"] == 3
    assert payload["complete_selection_count"] == 3
    assert payload["selections"][3]["booking_options_status"] == "missing"
    assert any("minimum required is 3" in warning for warning in payload["warnings"])


def test_google_flights_preflight_falls_back_to_next_public_route(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    search_run_ids: list[str] = []

    async def fake_run_live_search(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        search_run_ids.append(str(kwargs["run_id"]))
        if len(search_run_ids) <= 3:
            return (
                6,
                {
                    "status": "tool_error",
                    "stop_state": "google_page_error",
                    "warnings": ["Google Flights transient page error"],
                },
            )
        return (
            0,
            {
                "status": "ok",
                "target_url": "https://www.google.com/travel/flights/search?synthetic=2",
                "results": [{"rank": rank} for rank in range(1, 4)],
                "rankings": {},
            },
        )

    async def fake_run_live_itinerary_selection(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        outbound_rank = int(kwargs["outbound_row_rank"])
        return_rank = int(kwargs["return_row_rank"])
        return (
            0,
            {
                "status": "ok",
                "booking_url": (
                    "https://www.google.com/travel/flights/booking?"
                    f"outbound={outbound_rank}&return={return_rank}"
                ),
                "selected_outbound": {"row_rank": outbound_rank},
                "selected_return": {"row_rank": return_rank},
                "booking_options": [
                    {
                        "provider": "Synthetic",
                        "price": {"amount": outbound_rank * 100 + return_rank},
                    }
                ],
                "warnings": [],
                "evidence": {"run_id": f"selection-{outbound_rank}-{return_rank}", "artifacts": []},
            },
        )

    async def fake_run_headless_heal(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        return 0, {"status": "ok", "closed_google_flights_tab_count": 0, "evidence": {}}

    monkeypatch.setattr(preflight, "run_live_search", fake_run_live_search)
    monkeypatch.setattr(
        preflight,
        "run_live_itinerary_selection",
        fake_run_live_itinerary_selection,
    )
    monkeypatch.setattr(preflight, "run_headless_heal", fake_run_headless_heal)

    exit_code, payload = asyncio.run(
        preflight.run_google_flights_preflight(
            project_root=tmp_path,
            consent_choice="skip",
            top_k=3,
            return_top_k=1,
            min_complete_selections=3,
            selection_concurrency=3,
            max_tabs=5,
            adapter=FakeCdpAdapter(),  # type: ignore[arg-type]
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["preflight_route"] == "LAX-LAS"
    assert len(payload["diagnostics"]["route_attempts"]) == 2
    assert payload["diagnostics"]["route_attempts"][0]["status"] == "search_blocked"
    assert payload["diagnostics"]["route_attempts"][1]["status"] == "ok"
    assert any("fallback synthetic route LAX-LAS" in warning for warning in payload["warnings"])


def test_google_flights_preflight_retries_transient_search_page_error(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    search_run_ids: list[str] = []
    selected_pairs: list[tuple[int, int]] = []
    heal_calls: list[dict[str, Any]] = []

    async def fake_sleep(_seconds: float) -> None:
        return None

    async def fake_run_live_search(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        search_run_ids.append(str(kwargs["run_id"]))
        if len(search_run_ids) == 1:
            return (
                6,
                {
                    "status": "tool_error",
                    "stop_state": "google_page_error",
                    "error": "Google Flights page reported a transient error and offered Reload",
                    "results": [],
                },
            )
        return (
            0,
            {
                "status": "ok",
                "target_url": "https://www.google.com/travel/flights/search?synthetic=1",
                "results": [{"rank": rank} for rank in range(1, 3)],
                "rankings": {},
            },
        )

    async def fake_run_live_itinerary_selection(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        outbound_rank = int(kwargs["outbound_row_rank"])
        return_rank = int(kwargs["return_row_rank"])
        selected_pairs.append((outbound_rank, return_rank))
        return (
            0,
            {
                "status": "ok",
                "booking_url": (
                    "https://www.google.com/travel/flights/booking?"
                    f"outbound={outbound_rank}&return={return_rank}"
                ),
                "selected_outbound": {"row_rank": outbound_rank},
                "selected_return": {"row_rank": return_rank},
                "booking_options": [
                    {
                        "provider": "Synthetic",
                        "price": {"amount": outbound_rank * 100 + return_rank},
                    }
                ],
                "warnings": [],
                "evidence": {"run_id": f"selection-{outbound_rank}-{return_rank}", "artifacts": []},
            },
        )

    async def fake_run_headless_heal(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        heal_calls.append(kwargs)
        return (
            0,
            {
                "status": "ok",
                "closed_google_flights_tab_count": 1,
                "evidence": {"run_id": f"heal-{len(heal_calls)}", "artifacts": []},
            },
        )

    monkeypatch.setattr(preflight.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(preflight, "run_live_search", fake_run_live_search)
    monkeypatch.setattr(
        preflight,
        "run_live_itinerary_selection",
        fake_run_live_itinerary_selection,
    )
    monkeypatch.setattr(preflight, "run_headless_heal", fake_run_headless_heal)

    exit_code, payload = asyncio.run(
        preflight.run_google_flights_preflight(
            project_root=tmp_path,
            consent_choice="skip",
            top_k=2,
            return_top_k=1,
            selection_concurrency=2,
            max_tabs=5,
            adapter=FakeCdpAdapter(),  # type: ignore[arg-type]
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert selected_pairs == [(1, 1), (2, 1)]
    assert search_run_ids[0].endswith("-search")
    assert search_run_ids[1].endswith("-search-attempt-02")
    assert len(search_run_ids) == 2
    assert len(heal_calls) == 1
    assert heal_calls[0]["consent_choice"] == "skip"
    attempts = payload["search"]["attempts"]
    assert attempts[0]["heal_before_next_attempt"]["status"] == "ok"


def test_google_flights_preflight_bounds_synthetic_search_timeout(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    async def fake_run_live_search(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        await asyncio.sleep(1)
        return 0, {"status": "ok"}

    monkeypatch.setattr(preflight, "run_live_search", fake_run_live_search)

    exit_code, payload = asyncio.run(
        preflight.run_google_flights_preflight(
            project_root=tmp_path,
            consent_choice="skip",
            top_k=3,
            min_complete_selections=3,
            search_deadline_seconds=0.01,
            adapter=FakeCdpAdapter(),  # type: ignore[arg-type]
        )
    )

    assert exit_code == 6
    assert payload["status"] == "blocked"
    assert payload["stop_state"] == "preflight_search_timeout"
    route_attempts = payload["diagnostics"]["route_attempts"]
    assert len(route_attempts) == 1
    assert route_attempts[0]["status"] == "search_blocked"
    assert route_attempts[0]["search_retryable"] is False
    assert route_attempts[0]["search_deadline_seconds"] == 0.01


def test_google_flights_preflight_requires_each_requested_date_range(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    search_date_range_indexes: list[int] = []
    search_departure_dates: list[str] = []
    selected_pairs: list[tuple[int, int]] = []

    async def fake_run_live_search(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        intent = json.loads(kwargs["input_json"].read_text())
        search_date_range_indexes.append(int(intent["preflight_date_range_index"]))
        search_departure_dates.append(intent["departure_window"]["start"])
        return (
            0,
            {
                "status": "ok",
                "target_url": (
                    "https://www.google.com/travel/flights/search?"
                    f"range={intent['preflight_date_range_index']}"
                ),
                "results": [{"rank": 1}],
                "rankings": {},
            },
        )

    async def fake_run_live_itinerary_selection(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        outbound_rank = int(kwargs["outbound_row_rank"])
        return_rank = int(kwargs["return_row_rank"])
        selected_pairs.append((outbound_rank, return_rank))
        return (
            0,
            {
                "status": "ok",
                "booking_url": (
                    "https://www.google.com/travel/flights/booking?"
                    f"outbound={outbound_rank}&return={return_rank}"
                ),
                "selected_outbound": {"row_rank": outbound_rank},
                "selected_return": {"row_rank": return_rank},
                "booking_options": [
                    {
                        "provider": "Synthetic",
                        "price": {"amount": outbound_rank * 100 + return_rank},
                    }
                ],
                "warnings": [],
                "evidence": {"run_id": f"selection-{outbound_rank}-{return_rank}", "artifacts": []},
            },
        )

    monkeypatch.setattr(preflight, "run_live_search", fake_run_live_search)
    monkeypatch.setattr(
        preflight,
        "run_live_itinerary_selection",
        fake_run_live_itinerary_selection,
    )

    exit_code, payload = asyncio.run(
        preflight.run_google_flights_preflight(
            project_root=tmp_path,
            consent_choice="skip",
            top_k=1,
            return_top_k=2,
            min_complete_selections=1,
            selection_concurrency=1,
            date_range_count=2,
            max_tabs=5,
            adapter=FakeCdpAdapter(),  # type: ignore[arg-type]
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["date_range_count"] == 2
    assert payload["complete_date_range_count"] == 2
    assert search_date_range_indexes == [1, 2]
    assert len(set(search_departure_dates)) == 2
    assert selected_pairs == [(1, 1), (1, 2), (1, 1), (1, 2)]
    assert [item["date_range_index"] for item in payload["date_range_results"]] == [1, 2]
    assert all(item["selection_count"] == 2 for item in payload["date_range_results"])
    assert all(item["status"] == "ok" for item in payload["date_range_results"])


def test_close_page_targets_opens_headless_keepalive_before_closing_last_domain_tab(
    tmp_path: Any,
) -> None:
    adapter = FakeCdpAdapter()
    executed: list[dict[str, Any]] = []
    artifacts: list[str] = []
    source_surfaces: list[str] = []
    target = {
        "id": "google-tab-1",
        "title": "Google Flights",
        "url": "https://www.google.com/travel/flights/search?bad=1",
        "attached": False,
        "type": "page",
    }
    task_trace = preflight.new_task_trace(
        command="gflights.preflight.google-flights",
        name="cleanup",
        run_id="run",
    )

    closed = asyncio.run(
        preflight._close_page_targets(
            adapter=adapter,  # type: ignore[arg-type]
            browser_mode="headless",
            timeout_seconds=45.0,
            run_root=tmp_path,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            targets=[target],
            pages_payload={"ok": True, "pages": [target]},
            task_trace=task_trace,
            artifact_prefix="cleanup",
            source_surface="cdp:page-close:test",
        )
    )

    assert closed == [
        {
            "id": "google-tab-1",
            "url": "https://www.google.com/travel/flights/search?bad=1",
            "status": "ok",
        }
    ]
    assert ["open", preflight.HEADLESS_KEEPALIVE_URL] in adapter.calls
    assert ["page", "close", "--target", "google-tab-1"] in adapter.calls
    assert adapter.calls.index(["open", preflight.HEADLESS_KEEPALIVE_URL]) < adapter.calls.index(
        ["page", "close", "--target", "google-tab-1"]
    )


def test_close_page_targets_preserves_last_headless_blank_keepalive(
    tmp_path: Any,
) -> None:
    adapter = FakeCdpAdapter()
    executed: list[dict[str, Any]] = []
    artifacts: list[str] = []
    source_surfaces: list[str] = []
    target = {
        "id": "blank-tab-1",
        "title": "about:blank",
        "url": "about:blank",
        "attached": False,
        "type": "page",
    }
    task_trace = preflight.new_task_trace(
        command="gflights.preflight.headless-heal",
        name="cleanup",
        run_id="run",
    )

    closed = asyncio.run(
        preflight._close_page_targets(
            adapter=adapter,  # type: ignore[arg-type]
            browser_mode="headless",
            timeout_seconds=45.0,
            run_root=tmp_path,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            targets=[target],
            pages_payload={
                "ok": True,
                "budget": {"browser_mode": "headless"},
                "pages": [target],
            },
            task_trace=task_trace,
            artifact_prefix="cleanup",
            source_surface="cdp:page-close:test",
        )
    )

    assert closed == []
    assert ["page", "close", "--target", "blank-tab-1"] not in adapter.calls
    assert ["open", preflight.HEADLESS_KEEPALIVE_URL] not in adapter.calls
