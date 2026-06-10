from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from gflights.browser import BrowserMode, CdpResult
from gflights.live_selection import run_live_itinerary_selection


class FakeSelectionCdpAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], BrowserMode, float]] = []
        self.selection_calls = 0

    async def run_json(
        self,
        args: Sequence[str],
        *,
        browser_mode: BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> CdpResult:
        call_args = list(args)
        self.calls.append((call_args, browser_mode, timeout_seconds))
        if call_args[0] == "pages":
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "budget": {
                        "tab_count": 2,
                        "max_tabs": 3,
                        "tabs_over_budget": False,
                        "browser_mode": browser_mode,
                    },
                    "pages": [
                        {
                            "id": "page-1",
                            "type": "page",
                            "title": "Google Flights",
                            "url": "https://www.google.com/travel/flights/search?tfs=old",
                            "attached": False,
                        }
                    ],
                },
            )
        if call_args[0] == "open":
            return cdp_result(call_args, {"ok": True, "page": {"id": "page-1"}})
        if call_args[:2] == ["wait", "eval"]:
            condition = call_args[2]
            value = "booking_summary" if "booking options" in condition.lower() else "fare_rows"
            return cdp_result(call_args, {"ok": True, "result": {"value": value}})
        if call_args[:2] == ["text", "body"]:
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "items": [
                        {
                            "text": (
                                "Search results DKK 16,582 round trip Air India Nonstop 8 hr 45 min"
                            )
                        }
                    ],
                },
            )
        if call_args[:2] == ["wait", "network-idle"]:
            return cdp_result(call_args, {"ok": True})
        if call_args[0] == "eval" and call_args[1] == "window.location.href":
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "result": {
                        "value": "https://www.google.com/travel/flights/booking?tfs=encoded"
                    },
                },
            )
        if call_args[0] == "snapshot":
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "items": [
                        {
                            "text": (
                                "Itinerary summary\nDelhi to Copenhagen\nRound trip\nEconomy\n"
                                "2 passengers\nDKK 13,997 Lowest total price\nSelected flights\n"
                                "Outbound Fri, Oct 2\n"
                                "1:00 PM Indira Gandhi International Airport (DEL) to 6:05 AM+1 Copenhagen Airport (CPH)\n"
                                "Travel time: 12 hr 35 min\n"
                                "Finnair Economy Airbus A350 flight AY 122\n"
                                "2 hr 25 min layover Helsinki (HEL)\n"
                                "Return Tue, Nov 24\n"
                                "1:00 PM Copenhagen Airport (CPH) to 6:35 AM+1 Indira Gandhi International Airport (DEL)\n"
                                "Travel time: 11 hr 5 min\n"
                                "Finnair Economy Airbus A350 flight AY 121\n"
                                "1 hr 20 min layover Helsinki (HEL)\n"
                                "Booking options\nBook with Finnair\nAirline\nDKK 13,997\nContinue\n"
                                "Prices include required taxes + fees for 2 adults. Optional charges and bag fees may apply."
                            )
                        }
                    ],
                },
            )
        if call_args[0] == "eval":
            self.selection_calls += 1
            selected_text = (
                "7:40 AM – 6:05 AM+1 Finnair 12 hr 25 min DEL–CPH "
                "1 stop HEL 467 kg CO2e DKK 13,997 round trip"
                if self.selection_calls == 1
                else "1:00 PM – 6:35 AM+1 Finnair 11 hr 5 min CPH–DEL "
                "1 stop HEL 452 kg CO2e DKK 13,997 round trip"
            )
            row_rank = 2 if "Math.max(1, 2)" in call_args[1] else 1
            match_text = "7:40 AM" if "7:40 AM" in call_args[1] else "1:00 PM"
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "result": {
                        "value": {
                            "selected": True,
                            "text": selected_text,
                            "rowRank": row_rank,
                            "matchText": match_text,
                            "candidateCount": 3,
                            "matchCount": 1,
                        }
                    },
                },
            )
        if call_args[0] == "click":
            return cdp_result(call_args, {"ok": True, "click": {"clicked": True}})
        if call_args[:2] == ["page", "close"]:
            return cdp_result(call_args, {"ok": True})
        raise AssertionError(f"unexpected cdp call: {call_args}")


def cdp_result(
    args: list[str],
    payload: dict[str, object],
    *,
    status: str = "ok",
    exit_code: int = 0,
    browser_mode: BrowserMode = "headless",
) -> CdpResult:
    return CdpResult(
        argv=["cdp", *args],
        browser_mode=browser_mode,
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
        status=status,
        exit_code=exit_code,
        json_payload=payload,
        stop_state=payload.get("stop_state")
        if isinstance(payload.get("stop_state"), str)
        else None,
    )


def test_live_itinerary_selection_returns_booking_url_and_closes_tab(tmp_path: Path) -> None:
    adapter = FakeSelectionCdpAdapter()

    exit_code, payload = asyncio.run(
        run_live_itinerary_selection(
            search_url="https://www.google.com/travel/flights/search?tfs=encoded&hl=en&curr=DKK",
            project_root=tmp_path,
            adapter=adapter,  # type: ignore[arg-type]
            run_id="gf-test-selection",
            preferred_carrier="Air India",
            require_nonstop=True,
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["booking_url"].startswith("https://www.google.com/travel/flights/booking?")
    assert payload["selection"]["outbound"]["selected"] is True
    assert payload["selection"]["return"]["selected"] is True
    assert payload["selected_outbound"]["carrier"] == "Finnair"
    assert payload["selected_outbound"]["segments"][0]["flight_number"] == "AY 122"
    assert payload["selected_return"]["carrier"] == "Finnair"
    assert payload["booking_options"][0]["provider"] == "Finnair"
    assert adapter.selection_calls == 2

    run_root = tmp_path / "runs" / "gf-test-selection"
    assert (run_root / "outbound-settlement.json").is_file()
    assert (run_root / "return-settlement.json").is_file()
    assert (run_root / "booking-settlement.json").is_file()
    assert (run_root / "booking-snapshot.json").is_file()
    assert (run_root / "managed-tab-close.json").is_file()
    assert adapter.calls[-1][0] == ["page", "close", "--target", "page-1"]


def test_live_itinerary_selection_reuses_explicit_google_flights_target(
    tmp_path: Path,
) -> None:
    adapter = FakeSelectionCdpAdapter()

    exit_code, payload = asyncio.run(
        run_live_itinerary_selection(
            search_url="https://www.google.com/travel/flights/search?tfs=encoded&hl=en&curr=DKK",
            project_root=tmp_path,
            adapter=adapter,  # type: ignore[arg-type]
            run_id="gf-test-selection-reuse",
            preferred_carrier="Finnair",
            outbound_row_rank=2,
            return_row_rank=1,
            outbound_match_text="7:40 AM",
            return_match_text="1:00 PM",
            reuse_target="google-flights",
            max_tabs=3,
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["managed_tab_id"] == "page-1"
    assert payload["tab_budget"]["managed_tab_created"] is False
    assert payload["tab_budget"]["cleanup_status"] == "skipped_reused_tab"
    assert payload["tab_budget"]["before"]["tab_count"] == 2
    open_call = next(call for call in adapter.calls if call[0][0] == "open")
    assert open_call[0] == [
        "open",
        "https://www.google.com/travel/flights/search?tfs=encoded&hl=en&curr=DKK",
        "--new-tab=false",
        "--target",
        "page-1",
    ]
    assert not any(call[0][:2] == ["page", "close"] for call in adapter.calls)
    selection_evals = [
        call[0][1]
        for call in adapter.calls
        if call[0][0] == "eval" and call[0][1] != "window.location.href"
    ]
    assert "7:40 AM" in selection_evals[0]
    assert "Math.max(1, 2)" in selection_evals[0]
    assert "1:00 PM" in selection_evals[1]
