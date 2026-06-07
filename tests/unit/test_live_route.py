from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from gflights.browser import BrowserMode, CdpResult
from gflights.live_route import run_live_route_resolution


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


def test_live_route_stops_on_blocked_open_state(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {"status": "blocked", "stop_state": "unusual_traffic"},
                status="blocked",
                exit_code=4,
                fallback={
                    "recommended_browser_mode": "headed",
                    "reason": "headless blocked or human confirmation required",
                },
            )
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_route_resolution(
            input_text="CPH",
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-route-blocked",
        )
    )

    assert exit_code == 4
    assert payload["status"] == "blocked"
    assert payload["stop_state"] == "unusual_traffic"
    assert payload["fallback"]["recommended_browser_mode"] == "headed"
    assert payload["input_text"] == "CPH"
    assert payload["selected"] is None
    assert payload["choices"] == []
    assert len(adapter.calls) == 1
    assert (tmp_path / "runs" / "gf-route-blocked" / "open.json").is_file()
    assert (tmp_path / "runs" / "gf-route-blocked" / "command-log.json").is_file()


def test_live_route_stops_on_personal_data_prompt_after_wait(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en",
                    },
                },
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
        run_live_route_resolution(
            input_text="Lucknow",
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-route-personal-data",
        )
    )

    assert exit_code == 4
    assert payload["status"] == "personal_data_required"
    assert payload["stop_state"] == "personal_data_required"
    assert payload["warnings"] == [
        "live route resolution stopped before bypassing a browser safety boundary"
    ]
    assert [call[0][0] for call in adapter.calls] == ["open", "wait"]
    assert (tmp_path / "runs" / "gf-route-personal-data" / "wait.json").is_file()


def test_live_route_successful_snapshot_returns_explicit_deferred_payload(
    tmp_path: Path,
) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(
                ["snapshot"],
                {"ok": True, "items": [{"text": "Flights"}, {"text": "Copenhagen"}]},
            ),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_route_resolution(
            input_text="CPH",
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-route-snapshot",
        )
    )

    assert exit_code == 3
    assert payload["status"] == "unsupported"
    assert payload["live_mode"] is True
    assert payload["browser_mode"] == "headless"
    assert payload["input_text"] == "CPH"
    assert payload["selected"] is None
    assert payload["choices"] == []
    assert payload["unsupported"][0]["field"] == "route.resolve.live_autocomplete_extraction"
    assert payload["evidence"]["source_surfaces"] == [
        "cdp:open",
        "cdp:wait",
        "cdp:snapshot:route-autocomplete",
    ]
    assert adapter.calls == [
        (
            ["open", "https://www.google.com/travel/flights?hl=en"],
            "headless",
            30.0,
        ),
        (["wait", "load-state", "domcontentloaded", "--target", "page-1"], "headless", 30.0),
        (["snapshot", "--target", "page-1", "--limit", "120"], "headless", 30.0),
    ]
    assert (tmp_path / "runs" / "gf-route-snapshot" / "input.json").is_file()
    assert (tmp_path / "runs" / "gf-route-snapshot" / "snapshot.json").is_file()
