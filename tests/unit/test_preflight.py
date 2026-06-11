from __future__ import annotations

import asyncio
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
    assert ["daemon", "health-check", "--repair", "--out-dir", str(tmp_path / "runs" / payload["evidence"]["run_id"] / "cdp-health-check")] in adapter.calls
    assert ["page", "close", "--target", "google-tab-1"] in adapter.calls
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
    assert adapter.calls.index(["daemon", "restart", "--reconnect", "30s"]) < adapter.calls.index(["pages"])


def test_google_flights_preflight_selects_ranks_concurrently(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    active = 0
    max_active = 0
    selected_ranks: list[int] = []
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
        rank = int(kwargs["row_rank"])
        selected_ranks.append(rank)
        selection_run_ids.append(str(kwargs["run_id"]))
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return (
            0,
            {
                "status": "ok",
                "booking_url": f"https://www.google.com/travel/flights/booking?rank={rank}",
                "selected_outbound": {"row_rank": rank},
                "selected_return": {"row_rank": rank},
                "booking_options": [{"provider": "Synthetic", "price": {"amount": rank}}],
                "warnings": [],
                "evidence": {"run_id": f"selection-{rank}", "artifacts": []},
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
            top_k=5,
            selection_concurrency=3,
            max_tabs=7,
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["selection_concurrency"] == 3
    assert payload["complete_selection_count"] == 5
    assert selected_ranks == [1, 2, 3, 4, 5]
    assert len(selection_run_ids) == len(set(selection_run_ids)) == 5
    assert selection_run_ids == [
        f"{payload['evidence']['run_id']}-route-01-selection-rank-{rank}"
        for rank in range(1, 6)
    ]
    assert max_active == 3
    assert all("payload" not in item for item in payload["selections"])
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
        rank = int(kwargs["row_rank"])
        booking_url = (
            f"https://www.google.com/travel/flights/booking?rank={rank}"
            if rank < 3
            else "https://www.google.com/travel/flights/search?rank=3"
        )
        return (
            0,
            {
                "status": "ok",
                "booking_url": booking_url,
                "selected_outbound": {"row_rank": rank},
                "selected_return": {"row_rank": rank},
                "booking_options": [{"provider": "Synthetic", "price": {"amount": rank}}]
                if rank < 3
                else [],
                "warnings": [],
                "evidence": {"run_id": f"selection-{rank}", "artifacts": []},
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
            selection_concurrency=3,
            max_tabs=5,
        )
    )

    assert exit_code == 4
    assert payload["status"] == "blocked"
    assert payload["selection_count"] == 3
    assert payload["complete_selection_count"] == 2
    assert payload["selections"][2]["booking_options_status"] == "missing"
    assert any("selected 2/3 requested rows" in warning for warning in payload["warnings"])


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
        rank = int(kwargs["row_rank"])
        return (
            0,
            {
                "status": "ok",
                "booking_url": f"https://www.google.com/travel/flights/booking?rank={rank}"
                if rank <= 3
                else f"https://www.google.com/travel/flights/search?rank={rank}",
                "selected_outbound": {"row_rank": rank},
                "selected_return": {"row_rank": rank},
                "booking_options": [{"provider": "Synthetic", "price": {"amount": rank}}]
                if rank <= 3
                else [],
                "warnings": [],
                "evidence": {"run_id": f"selection-{rank}", "artifacts": []},
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
            top_k=5,
            min_complete_selections=3,
            selection_concurrency=5,
            max_tabs=5,
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["selection_count"] == 5
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
        rank = int(kwargs["row_rank"])
        return (
            0,
            {
                "status": "ok",
                "booking_url": f"https://www.google.com/travel/flights/booking?rank={rank}",
                "selected_outbound": {"row_rank": rank},
                "selected_return": {"row_rank": rank},
                "booking_options": [{"provider": "Synthetic", "price": {"amount": rank}}],
                "warnings": [],
                "evidence": {"run_id": f"selection-{rank}", "artifacts": []},
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
            min_complete_selections=3,
            selection_concurrency=3,
            max_tabs=5,
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
    selected_ranks: list[int] = []
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
        rank = int(kwargs["row_rank"])
        selected_ranks.append(rank)
        return (
            0,
            {
                "status": "ok",
                "booking_url": f"https://www.google.com/travel/flights/booking?rank={rank}",
                "selected_outbound": {"row_rank": rank},
                "selected_return": {"row_rank": rank},
                "booking_options": [{"provider": "Synthetic", "price": {"amount": rank}}],
                "warnings": [],
                "evidence": {"run_id": f"selection-{rank}", "artifacts": []},
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
            selection_concurrency=2,
            max_tabs=5,
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert selected_ranks == [1, 2]
    assert search_run_ids[0].endswith("-search")
    assert search_run_ids[1].endswith("-search-attempt-02")
    assert len(search_run_ids) == 2
    assert len(heal_calls) == 1
    assert heal_calls[0]["consent_choice"] == "skip"
    attempts = payload["search"]["attempts"]
    assert attempts[0]["heal_before_next_attempt"]["status"] == "ok"
