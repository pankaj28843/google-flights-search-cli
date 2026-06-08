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
        if call_args[0] == "eval":
            self.selection_calls += 1
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "result": {
                        "value": {
                            "selected": True,
                            "text": "Air India Nonstop DKK 16,582 round trip",
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
    assert adapter.selection_calls == 2

    run_root = tmp_path / "runs" / "gf-test-selection"
    assert (run_root / "outbound-settlement.json").is_file()
    assert (run_root / "return-settlement.json").is_file()
    assert (run_root / "booking-settlement.json").is_file()
    assert (run_root / "managed-tab-close.json").is_file()
    assert adapter.calls[-1][0] == ["page", "close", "--target", "page-1"]
