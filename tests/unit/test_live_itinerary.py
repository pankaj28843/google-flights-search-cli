from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from gflights.browser import BrowserMode, CdpResult
from gflights.live_itinerary import run_live_itinerary_inspection

FIXTURES = Path(__file__).resolve().parents[1] / "e2e" / "fixtures"


class FakeCdpAdapter:
    def __init__(self, results: list[CdpResult]) -> None:
        self.results = results
        self.calls: list[tuple[list[str], BrowserMode, float]] = []

    async def run_json(
        self,
        args: Sequence[str],
        *,
        browser_mode: BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> CdpResult:
        self.calls.append((list(args), browser_mode, timeout_seconds))
        return self.results.pop(0)


def cdp_result(
    args: list[str],
    payload: dict[str, object],
    *,
    status: str = "ok",
    exit_code: int = 0,
    browser_mode: BrowserMode = "headless",
    fallback: dict[str, str] | None = None,
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
        fallback=fallback,
    )


def booking_url() -> str:
    return "https://www.google.com/travel/flights/booking?tfs=redacted&tfu=redacted&hl=en"


def test_live_itinerary_stops_on_payment_boundary_snapshot(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {"ok": True, "page": {"id": "page-1", "url": booking_url()}},
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(
                ["snapshot"],
                {"status": "payment_or_booking_boundary"},
                status="payment_or_booking_boundary",
                exit_code=4,
                fallback={
                    "recommended_browser_mode": "headed",
                    "reason": "headless blocked or human confirmation required",
                },
            ),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_itinerary_inspection(
            booking_url=booking_url(),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-itinerary-boundary",
        )
    )

    assert exit_code == 4
    assert payload["status"] == "payment_or_booking_boundary"
    assert payload["stop_state"] == "payment_or_booking_boundary"
    assert payload["fallback"]["recommended_browser_mode"] == "headed"
    assert payload["itinerary"] is None
    assert len(adapter.calls) == 3
    assert (tmp_path / "runs" / "gf-itinerary-boundary" / "snapshot.json").is_file()
    assert (tmp_path / "runs" / "gf-itinerary-boundary" / "command-log.json").is_file()


def test_live_itinerary_stops_on_personal_data_prompt(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {"ok": True, "page": {"id": "page-1", "url": booking_url()}},
            ),
            cdp_result(
                ["wait"],
                {"status": "personal_data_required"},
                status="personal_data_required",
                exit_code=4,
            ),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_itinerary_inspection(
            booking_url=booking_url(),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-itinerary-personal-data",
        )
    )

    assert exit_code == 4
    assert payload["status"] == "personal_data_required"
    assert payload["stop_state"] == "personal_data_required"
    assert payload["warnings"] == [
        "live itinerary inspection stopped before crossing a provider, login, payment, personal-data, or access-control boundary"
    ]
    assert len(adapter.calls) == 2


def test_live_itinerary_extracts_visible_details_without_clicking_provider_continue(
    tmp_path: Path,
) -> None:
    fixture = json.loads((FIXTURES / "selected_itinerary_visible_text_fixture.json").read_text())
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {"ok": True, "page": {"id": "page-1", "url": booking_url()}},
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], fixture["snapshot"]),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_itinerary_inspection(
            booking_url=booking_url(),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-itinerary-visible",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["itinerary"]["segments"][0]["flight_number"] == "KL 1268"
    assert payload["itinerary"]["booking_options"][0]["provider"] == "KLM"
    assert payload["itinerary"]["boundary"]["provider_continue_visible"] is True
    assert payload["itinerary"]["boundary"]["provider_continue_clicked"] is False
    assert payload["itinerary"]["boundary"]["checkout_entered"] is False
    assert [call[0][0] for call in adapter.calls] == ["open", "wait", "snapshot"]
    assert all("click" not in call[0] for call in adapter.calls)
    assert "cdp:snapshot:selected-itinerary" in payload["evidence"]["source_surfaces"]
