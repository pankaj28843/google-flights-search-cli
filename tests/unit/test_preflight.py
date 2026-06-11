from __future__ import annotations

import asyncio
from typing import Any

from gflights import preflight


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
        f"{payload['evidence']['run_id']}-selection-rank-{rank}" for rank in range(1, 6)
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


def test_google_flights_preflight_retries_transient_search_page_error(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    search_run_ids: list[str] = []
    selected_ranks: list[int] = []

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

    monkeypatch.setattr(preflight.asyncio, "sleep", fake_sleep)
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
