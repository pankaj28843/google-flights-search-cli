from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from gflights.browser import BrowserMode, CdpResult
from gflights.live_route import run_live_route_resolution

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
        if list(args)[:2] == ["page", "close"]:
            return cdp_result(list(args), {"ok": True})
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


def recoverable_context_error(args: list[str]) -> CdpResult:
    return cdp_result(
        args,
        {
            "code": "connection_failed",
            "message": "cdp Runtime.evaluate failed: Cannot find default execution context (-32000)",
        },
        status="tool_error",
        exit_code=6,
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
    assert [call[0][0] for call in adapter.calls] == ["open", "wait", "page"]
    assert (tmp_path / "runs" / "gf-route-personal-data" / "wait.json").is_file()
    assert (tmp_path / "runs" / "gf-route-personal-data" / "managed-tab-close.json").is_file()


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
            cdp_result(["fill"], {"ok": True}),
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
        "cdp:route-autocomplete-fill",
        "cdp:snapshot:route-autocomplete",
        "cdp:page-close",
    ]
    assert adapter.calls == [
        (
            ["open", "https://www.google.com/travel/flights?hl=en"],
            "headless",
            30.0,
        ),
        (["wait", "load-state", "domcontentloaded", "--target", "page-1"], "headless", 30.0),
        (
            [
                "fill",
                "Where to?",
                "CPH",
                "--by",
                "label",
                "--exact",
                "--target",
                "page-1",
                "--wait-text",
                "CPH",
            ],
            "headless",
            30.0,
        ),
        (["snapshot", "--target", "page-1", "--limit", "120"], "headless", 30.0),
        (["page", "close", "--target", "page-1"], "headless", 60.0),
    ]
    assert (tmp_path / "runs" / "gf-route-snapshot" / "input.json").is_file()
    assert (tmp_path / "runs" / "gf-route-snapshot" / "snapshot.json").is_file()


def test_live_route_extracts_choices_from_autocomplete_snapshot(tmp_path: Path) -> None:
    fixture = json.loads((FIXTURES / "route_autocomplete_visible_text_fixture.json").read_text())
    washington_query = next(
        query for query in fixture["queries"] if query["input_text"] == "Washington DC"
    )
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
            cdp_result(["fill"], {"ok": True}),
            cdp_result(["snapshot"], washington_query["snapshot"]),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_route_resolution(
            input_text="Washington DC",
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-route-washington",
        )
    )

    assert exit_code == 2
    assert payload["status"] == "ambiguous"
    assert payload["selected"] is None
    assert [choice["code_or_id"] for choice in payload["choices"]] == [
        None,
        "DCA",
        "IAD",
        "BWI",
    ]
    assert payload["live_mode"] is True
    assert payload["browser_mode"] == "headless"
    assert payload["evidence"]["source_surfaces"] == [
        "cdp:open",
        "cdp:wait",
        "cdp:route-autocomplete-fill",
        "cdp:snapshot:route-autocomplete",
        "route-autocomplete-visible-text",
        "cdp:page-close",
    ]


def test_live_route_auto_run_ids_are_unique_for_rapid_invocations(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            *_unsupported_route_results("page-1"),
            *_unsupported_route_results("page-2"),
        ]
    )

    first_exit_code, first_payload = asyncio.run(
        run_live_route_resolution(
            input_text="CPH",
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
        )
    )
    second_exit_code, second_payload = asyncio.run(
        run_live_route_resolution(
            input_text="Lucknow",
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
        )
    )

    assert first_exit_code == 3
    assert second_exit_code == 3
    first_run_id = first_payload["evidence"]["run_id"]
    second_run_id = second_payload["evidence"]["run_id"]
    assert first_run_id != second_run_id
    assert (tmp_path / "runs" / first_run_id / "input.json").is_file()
    assert (tmp_path / "runs" / second_run_id / "input.json").is_file()


def test_live_route_retries_transient_wait_context_error(tmp_path: Path) -> None:
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
            recoverable_context_error(["wait"]),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["fill"], {"ok": True}),
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
            run_id="gf-route-wait-retry",
        )
    )

    assert exit_code == 3
    assert payload["status"] == "unsupported"
    assert [call[0][0] for call in adapter.calls] == [
        "open",
        "wait",
        "wait",
        "fill",
        "snapshot",
        "page",
    ]
    assert "cdp:wait:retry" in payload["evidence"]["source_surfaces"]
    assert (tmp_path / "runs" / "gf-route-wait-retry" / "wait-retry-1.json").is_file()
    assert (tmp_path / "runs" / "gf-route-wait-retry" / "managed-tab-close.json").is_file()


def test_live_route_waits_past_about_blank_before_fill(tmp_path: Path) -> None:
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
                {
                    "ok": True,
                    "wait": {
                        "matched": True,
                        "url": "about:blank",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True, "wait": {"matched": True}}),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["fill"], {"ok": True}),
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
            run_id="gf-route-about-blank",
        )
    )

    assert exit_code == 3
    assert [call[0][0] for call in adapter.calls] == [
        "open",
        "wait",
        "wait",
        "wait",
        "fill",
        "snapshot",
        "page",
    ]
    assert adapter.calls[2][0][:2] == ["wait", "eval"]
    assert "cdp:wait:navigation-url" in payload["evidence"]["source_surfaces"]
    assert "cdp:wait:after-navigation" in payload["evidence"]["source_surfaces"]
    run_root = tmp_path / "runs" / "gf-route-about-blank"
    assert (run_root / "wait-navigation-url.json").is_file()
    assert (run_root / "wait-after-navigation.json").is_file()


def test_live_route_retries_transient_snapshot_context_error(tmp_path: Path) -> None:
    fixture = json.loads((FIXTURES / "route_autocomplete_visible_text_fixture.json").read_text())
    washington_query = next(
        query for query in fixture["queries"] if query["input_text"] == "Washington DC"
    )
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
            cdp_result(["fill"], {"ok": True}),
            recoverable_context_error(["snapshot"]),
            cdp_result(["snapshot"], washington_query["snapshot"]),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_route_resolution(
            input_text="Washington DC",
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-route-snapshot-retry",
        )
    )

    assert exit_code == 2
    assert payload["status"] == "ambiguous"
    assert "cdp:snapshot:route-autocomplete:retry" in payload["evidence"]["source_surfaces"]
    assert payload["choices"][0]["evidence"]["artifacts"] == [
        str(tmp_path / "runs" / "gf-route-snapshot-retry" / "snapshot-retry-1.json")
    ]
    assert (tmp_path / "runs" / "gf-route-snapshot-retry" / "snapshot-retry-1.json").is_file()


def test_live_route_falls_back_when_exact_label_fill_fails(tmp_path: Path) -> None:
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
                ["fill"],
                {"message": "no element matched exact label"},
                status="tool_error",
                exit_code=6,
            ),
            cdp_result(["fill"], {"ok": True}),
            cdp_result(
                ["snapshot"],
                {"ok": True, "items": [{"text": "Flights"}, {"text": "Lucknow"}]},
            ),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_route_resolution(
            input_text="Lucknow",
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-route-fill-fallback",
        )
    )

    assert exit_code == 3
    assert payload["status"] == "unsupported"
    fill_calls = [call[0] for call in adapter.calls if call[0][0] == "fill"]
    assert len(fill_calls) == 2
    assert "--exact" in fill_calls[0]
    assert "--exact" not in fill_calls[1]
    assert "cdp:route-autocomplete-fill:label-fuzzy" in payload["evidence"]["source_surfaces"]
    run_root = tmp_path / "runs" / "gf-route-fill-fallback"
    assert (run_root / "route-autocomplete-fill.json").is_file()
    assert (run_root / "route-autocomplete-fill-label-fuzzy.json").is_file()
    assert (run_root / "managed-tab-close.json").is_file()


def test_live_route_closes_opened_page_after_fill_tool_error(tmp_path: Path) -> None:
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
                ["fill"], {"message": "exact label missing"}, status="tool_error", exit_code=6
            ),
            cdp_result(
                ["fill"], {"message": "fuzzy label missing"}, status="tool_error", exit_code=6
            ),
            cdp_result(
                ["fill"],
                {"message": "destination label missing"},
                status="tool_error",
                exit_code=6,
            ),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_route_resolution(
            input_text="CPH",
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-route-fill-failure",
        )
    )

    assert exit_code == 6
    assert payload["status"] == "tool_error"
    assert [call[0][0] for call in adapter.calls] == [
        "open",
        "wait",
        "fill",
        "fill",
        "fill",
        "page",
    ]
    assert "cdp:page-close" in payload["evidence"]["source_surfaces"]
    assert (tmp_path / "runs" / "gf-route-fill-failure" / "managed-tab-close.json").is_file()


def _unsupported_route_results(page_id: str) -> list[CdpResult]:
    return [
        cdp_result(
            ["open"],
            {
                "ok": True,
                "page": {
                    "id": page_id,
                    "url": "https://www.google.com/travel/flights?hl=en",
                },
            },
        ),
        cdp_result(["wait"], {"ok": True}),
        cdp_result(["fill"], {"ok": True}),
        cdp_result(
            ["snapshot"],
            {"ok": True, "items": [{"text": "Flights"}, {"text": "Copenhagen"}]},
        ),
    ]
