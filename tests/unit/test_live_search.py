from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from gflights.app_state import PriceCache
from gflights.browser import BrowserMode, CdpAdapter, CdpResult, ProcessResult
from gflights.live_search import run_live_search

FIXTURES = Path(__file__).resolve().parents[1] / "e2e" / "fixtures"


class FakeCdpAdapter:
    def __init__(
        self,
        results: list[CdpResult],
        *,
        close_result: CdpResult | None = None,
        settlement_terminal_result: CdpResult | None = None,
        settlement_network_result: CdpResult | None = None,
        settlement_text_samples: list[str] | None = None,
        accessible_rows_payload: dict[str, object] | None = None,
    ) -> None:
        self.results = results
        self.close_result = close_result
        self.settlement_terminal_result = settlement_terminal_result
        self.settlement_network_result = settlement_network_result
        self.settlement_text_samples = settlement_text_samples or [
            "Search results DKK 12,054 round trip Nonstop",
            "Search results DKK 12,054 round trip Nonstop",
        ]
        self.accessible_rows_payload = accessible_rows_payload or {
            "ok": True,
            "result": {"value": []},
        }
        self.calls: list[tuple[list[str], BrowserMode, float]] = []
        self._settlement_network_pending = False

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
        if call_args[:2] == ["page", "close"]:
            if self.close_result is not None:
                return self.close_result
            return cdp_result(call_args, {"ok": True})
        if call_args[:2] == ["wait", "eval"] and "document.body" in call_args[2]:
            result = self.settlement_terminal_result or cdp_result(
                call_args, {"ok": True, "result": {"value": "fare_rows"}}
            )
            terminal = ""
            payload = result.json_payload or {}
            value = payload.get("result")
            if isinstance(value, dict) and isinstance(value.get("value"), str):
                terminal = value["value"]
            self._settlement_network_pending = terminal not in {
                "fare_rows",
                "booking_summary",
            }
            return result
        if (
            self._settlement_network_pending
            and call_args[:2] == ["wait", "network-idle"]
            and call_args[-1] == "1s"
        ):
            self._settlement_network_pending = False
            return self.settlement_network_result or cdp_result(call_args, {"ok": True})
        if call_args[:2] == ["text", "body"]:
            text = (
                self.settlement_text_samples.pop(0)
                if self.settlement_text_samples
                else "Search results DKK 12,054 round trip Nonstop"
            )
            return cdp_result(call_args, {"ok": True, "items": [{"text": text}]})
        if call_args[0] == "eval" and "collectAccessibleFlightRows" in call_args[1]:
            return cdp_result(call_args, self.accessible_rows_payload)
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


def assert_uuid4(value: str) -> None:
    parsed = UUID(value)
    assert parsed.version == 4
    assert str(parsed) == value


def write_intent(path: Path) -> Path:
    intent_path = path / "intent.json"
    intent_path.write_text(
        json.dumps(
            {
                "query_id": "live-cph-del",
                "origin": {"text": "CPH", "kind": "airport_code"},
                "destination": {"text": "DEL", "kind": "airport_code"},
                "trip_type": "round_trip",
                "departure_window": {"start": "2026-10-01", "end": "2026-10-01"},
                "return_window": {"start": "2026-11-24", "end": "2026-11-24"},
                "passengers": {
                    "adults": 2,
                    "children": 0,
                    "infants_in_seat": 0,
                    "infants_on_lap": 0,
                },
                "cabin": "economy",
                "currency": "EUR",
                "language": "en",
                "sort": "top_flights",
            }
        )
    )
    return intent_path


def write_lucknow_oneway_intent(path: Path) -> Path:
    intent_path = path / "intent.json"
    intent_path.write_text(
        json.dumps(
            {
                "query_id": "live-cph-lko",
                "origin": {"text": "CPH", "kind": "airport_code"},
                "destination": {
                    "text": "Lucknow",
                    "kind": "city_or_airport",
                    "selected": {
                        "text": "Lucknow, Uttar Pradesh, India",
                        "kind": "city",
                        "display_name": "Lucknow, Uttar Pradesh, India",
                        "code_or_id": "/m/022tq4",
                        "confidence": "strong",
                        "evidence": {
                            "source_surfaces": [
                                "route-autocomplete-visible-text",
                                "protobuf-decode-report",
                            ],
                            "artifacts": [
                                "route_autocomplete_choices_fixture.json",
                                "decode-report.md",
                            ],
                        },
                    },
                },
                "trip_type": "one_way",
                "departure_window": {"start": "2026-06-15", "end": "2026-06-15"},
                "return_window": None,
                "passengers": {
                    "adults": 1,
                    "children": 0,
                    "infants_in_seat": 0,
                    "infants_on_lap": 0,
                },
                "cabin": "economy",
                "currency": "EUR",
                "language": "en",
                "location": "US",
                "sort": "price",
            }
        )
    )
    return intent_path


def successful_capture_results(
    snapshot_payload: dict[str, object] | None = None,
) -> list[CdpResult]:
    results = [
        cdp_result(
            ["open"],
            {
                "ok": True,
                "page": {
                    "id": "page-1",
                    "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                },
            },
        ),
        cdp_result(["wait"], {"ok": True}),
    ]
    results.extend(
        [
            cdp_result(
                ["snapshot"],
                snapshot_payload or {"ok": True, "items": [{"text": "Flights"}]},
            ),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )
    return results


def compact_current_results_snapshot() -> dict[str, object]:
    return {
        "ok": True,
        "snapshot": {
            "items": [
                {
                    "text": (
                        "Search results Loading results 5 results returned. "
                        "2:50 PM CPH 2:35 PM+1 LKO €3,561 round trip "
                        "2 stops19 hr 15 minBritish Airways, IndiGo +10% emissions "
                        "9:55 AM CPH 6:35 AM+1 LKO Economy + Premium Economy "
                        "€3,794 round trip 2 stops16 hr 10 minKLM, IndiGo +37% emissions"
                    )
                }
            ]
        },
    }


def test_live_search_orchestration_opens_google_flights_and_records_artifacts(
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
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Flights"}]}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-live-search",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert payload["query_id"] == "live-cph-del"
    assert payload["browser_mode"] == "headless"
    assert payload["live_mode"] is True
    assert payload["results"] == []
    assert payload["query_population"]["status"] == "encoded"
    assert payload["unsupported"][0]["field"] == "live_result_extraction"
    assert payload["evidence"]["run_id"] == "gf-test-live-search"
    assert payload["evidence"]["source_surfaces"][0] == "query-state:tfs"
    assert payload["evidence"]["source_surfaces"][1:] == [
        "cdp:open",
        "cdp:wait",
        "cdp:wait:terminal-dom",
        "cdp:settlement",
        "cdp:snapshot",
        "cdp:eval:accessible-flight-row-expand",
        "cdp:eval:accessible-flight-rows",
        "cdp:network",
        "cdp:page-close",
    ]

    run_root = tmp_path / "runs" / "gf-test-live-search"
    assert (run_root / "intent.json").is_file()
    assert (run_root / "query-state.json").is_file()
    assert (run_root / "command-log.json").is_file()
    assert (run_root / "open.json").is_file()
    assert (run_root / "snapshot.json").is_file()
    assert (run_root / "network.json").is_file()
    assert (run_root / "managed-tab-close.json").is_file()
    assert adapter.calls[0][0][0] == "open"
    assert adapter.calls[0][0][1].startswith("https://www.google.com/travel/flights/search?")
    assert "tfs=" in adapter.calls[0][0][1]
    assert adapter.calls[1:] == [
        (["wait", "load-state", "domcontentloaded", "--target", "page-1"], "headless", 30.0),
        (adapter.calls[2][0], "headless", 30.0),
        (["snapshot", "--target", "page-1", "--limit", "80"], "headless", 30.0),
        (adapter.calls[4][0], "headless", 10.0),
        (adapter.calls[5][0], "headless", 10.0),
        (["network", "--target", "page-1", "--limit", "50", "--wait", "1s"], "headless", 30.0),
        (["page", "close", "--target", "page-1"], "headless", 60.0),
    ]


def test_live_search_warns_when_managed_tab_close_fails(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?tfs=encoded&tfu=encoded&hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Flights"}]}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ],
        close_result=cdp_result(
            ["page", "close", "--target", "page-1"],
            {"ok": False, "message": "target already closed"},
            status="tool_error",
            exit_code=6,
        ),
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-close-warning",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert payload["evidence"]["source_surfaces"][-1] == "cdp:page-close"
    assert any(
        "managed cdp page cleanup returned tool_error" in warning for warning in payload["warnings"]
    )
    close_calls = [
        call for call in adapter.calls if call[0] == ["page", "close", "--target", "page-1"]
    ]
    assert len(close_calls) == 3
    assert {call[2] for call in close_calls} == {60.0}
    artifact = tmp_path / "runs" / "gf-test-close-warning" / "managed-tab-close.json"
    close_artifact = json.loads(artifact.read_text())
    assert close_artifact["status"] == "tool_error"
    assert close_artifact["attempt_count"] == 3
    assert close_artifact["max_attempts"] == 3
    assert [attempt["status"] for attempt in close_artifact["attempts"]] == [
        "tool_error",
        "tool_error",
        "tool_error",
    ]


def test_live_search_opens_populated_query_state_url_when_supported(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?tfs=encoded&tfu=encoded&hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Flights"}]}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_lucknow_oneway_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-query-state",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    opened_url = adapter.calls[0][0][1]
    assert opened_url.startswith("https://www.google.com/travel/flights/search?")
    assert "tfs=CBwQAhokEgoyMDI2LTA2LTE1" in opened_url
    assert "tfu=EgYIAhAAGAA" in opened_url
    assert "hl=en" in opened_url
    assert "curr=EUR" in opened_url
    assert "gl=US" in opened_url
    assert payload["query_population"]["status"] == "encoded"
    assert payload["query_population"]["confidence"] == "strong"
    assert "query-state:tfs" in payload["evidence"]["source_surfaces"]
    assert "query-state:tfu" in payload["evidence"]["source_surfaces"]
    assert "cdp:wait:query-network-idle" not in payload["evidence"]["source_surfaces"]
    assert adapter.calls[2][0][:2] == ["wait", "eval"]
    assert any(
        "ranking objectives are still applied after row extraction" in warning
        for warning in payload["warnings"]
    )
    assert payload["unsupported"][0]["field"] == "live_result_extraction"
    assert (tmp_path / "runs" / "gf-test-query-state" / "query-state.json").is_file()
    assert not (tmp_path / "runs" / "gf-test-query-state" / "wait-query-network-idle.json").exists()


def test_live_search_tool_error_keeps_encoded_target_url(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": False,
                    "code": "connection_failed",
                    "message": "failed to read JSON message: use of closed network connection",
                },
                status="tool_error",
                exit_code=6,
            ),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-tool-error-url",
        )
    )

    assert exit_code == 6
    assert payload["status"] == "tool_error"
    assert payload["target_url"].startswith("https://www.google.com/travel/flights/search?")
    assert "tfs=" in payload["target_url"]
    assert "hl=en" in payload["target_url"]
    assert "curr=EUR" in payload["target_url"]
    assert payload["query_population"]["status"] == "encoded"


def test_live_search_recovers_workflow_created_target_with_uuid_trace(
    tmp_path: Path,
) -> None:
    page_id = "9A29955C057DBDACA7E81371E4DCB2C4"
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": False,
                    "message": (f"failed to record workflow-created page {page_id} after open"),
                },
                status="tool_error",
                exit_code=6,
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Flights"}]}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-recovered-open",
            max_tabs=8,
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert payload["managed_tab_id"] == page_id
    assert any("workflow-created-page recording error" in item for item in payload["warnings"])
    assert any(call[0] == ["page", "close", "--target", page_id] for call in adapter.calls)

    run_root = tmp_path / "runs" / "gf-test-recovered-open"
    task_trace = json.loads((run_root / "task-trace.json").read_text())
    assert task_trace["managed_tab_id"] == page_id
    assert_uuid4(task_trace["task"]["task_id"])
    assert_uuid4(task_trace["task"]["root_task_id"])
    assert_uuid4(task_trace["managed_tab_task"]["task_id"])
    assert task_trace["managed_tab_task"]["parent_task_id"] == task_trace["task"]["task_id"]
    assert task_trace["target_task_ids"] == {page_id: task_trace["managed_tab_task"]["task_id"]}

    close_trace = json.loads((run_root / "managed-tab-close.json").read_text())
    assert close_trace["target_task_ids"] == task_trace["target_task_ids"]


def test_live_search_reuse_policy_records_tab_budget_and_top_k_results(
    tmp_path: Path,
) -> None:
    adapter = FakeCdpAdapter(
        successful_capture_results(
            {
                "ok": True,
                "items": [
                    {
                        "text": (
                            "7:40 AM – 6:05 AM+1 Finnair 12 hr 25 min DEL–CPH "
                            "1 stop HEL 467 kg CO2e DKK 13,997 round trip "
                            "1:00 PM – 6:35 AM+1 Qatar Airways 15 hr 5 min DEL–CPH "
                            "1 stop DOH 752 kg CO2e DKK 12,900 round trip"
                        )
                    }
                ],
            }
        )
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headed",
            run_id="gf-test-search-reuse",
            managed_tab_policy="reuse",
            max_tabs=3,
            rank_objectives=["cheapest", "fastest", "least-layover", "balanced"],
            top_k=1,
            deny_transit=["DOH"],
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["managed_tab_id"] == "page-1"
    assert payload["tab_budget"]["managed_tab_created"] is False
    assert payload["tab_budget"]["cleanup_status"] == "skipped_reused_tab"
    assert payload["tab_budget"]["before"]["tab_count"] == 2
    assert payload["ranking"]["top_k"] == 1
    assert payload["ranking"]["transit_policy"]["deny"] == ["DOH"]
    assert payload["top_cheapest"][0]["carriers"] == ["Finnair"]
    assert payload["top_fastest"][0]["carriers"] == ["Finnair"]
    open_call = next(call for call in adapter.calls if call[0][0] == "open")
    assert open_call[0][2:] == ["--new-tab=false", "--target", "page-1"]
    assert not any(call[0][:2] == ["page", "close"] for call in adapter.calls)


def test_live_search_retries_transient_wait_context_error(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(
                ["wait"],
                {
                    "ok": False,
                    "code": "connection_failed",
                    "message": "cdp Runtime.evaluate failed: Cannot find default execution context (-32000)",
                },
                status="tool_error",
                exit_code=6,
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Flights"}]}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-wait-retry",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert payload["evidence"]["source_surfaces"][0] == "query-state:tfs"
    assert payload["evidence"]["source_surfaces"][1:] == [
        "cdp:open",
        "cdp:wait",
        "cdp:wait:retry",
        "cdp:wait:terminal-dom",
        "cdp:settlement",
        "cdp:snapshot",
        "cdp:eval:accessible-flight-row-expand",
        "cdp:eval:accessible-flight-rows",
        "cdp:network",
        "cdp:page-close",
    ]
    assert adapter.calls[1][0] == adapter.calls[2][0]
    run_root = tmp_path / "runs" / "gf-test-wait-retry"
    assert (run_root / "wait.json").is_file()
    assert (run_root / "wait-retry-1.json").is_file()


def test_live_search_waits_for_navigation_when_load_state_matches_about_blank(
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
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(
                ["wait"],
                {
                    "ok": True,
                    "wait": {
                        "kind": "load-state",
                        "state": "domcontentloaded",
                        "ready_state": "complete",
                        "url": "about:blank",
                        "matched": True,
                    },
                },
            ),
            cdp_result(
                ["wait"],
                {
                    "ok": True,
                    "wait": {
                        "kind": "eval",
                        "matched": True,
                        "value": True,
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Flights"}]}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-about-blank-navigation",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert adapter.calls[2] == (
        [
            "wait",
            "eval",
            'location.href !== "about:blank"',
            "--target",
            "page-1",
        ],
        "headless",
        10.0,
    )
    assert adapter.calls[3][0] == ["wait", "load-state", "domcontentloaded", "--target", "page-1"]
    run_root = tmp_path / "runs" / "gf-test-about-blank-navigation"
    assert (run_root / "wait-navigation-url.json").is_file()
    assert (run_root / "wait-after-navigation.json").is_file()


def test_live_search_retries_transient_snapshot_context_error(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(
                ["snapshot"],
                {
                    "ok": False,
                    "code": "connection_failed",
                    "message": "snapshot target page-1: cdp Runtime.evaluate failed: Cannot find default execution context (-32000)",
                },
                status="tool_error",
                exit_code=6,
            ),
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Flights"}]}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-snapshot-retry",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert payload["evidence"]["source_surfaces"][0] == "query-state:tfs"
    assert payload["evidence"]["source_surfaces"][1:] == [
        "cdp:open",
        "cdp:wait",
        "cdp:wait:terminal-dom",
        "cdp:settlement",
        "cdp:snapshot",
        "cdp:snapshot:retry",
        "cdp:eval:accessible-flight-row-expand",
        "cdp:eval:accessible-flight-rows",
        "cdp:network",
        "cdp:page-close",
    ]
    run_root = tmp_path / "runs" / "gf-test-snapshot-retry"
    assert (run_root / "snapshot.json").is_file()
    assert (run_root / "snapshot-retry-1.json").is_file()


def test_live_search_keeps_snapshot_output_when_network_capture_fails(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Flights"}]}),
            cdp_result(
                ["network"],
                {
                    "ok": False,
                    "code": "connection_failed",
                    "message": "capture network target page-1: daemon read i/o timeout",
                },
                status="tool_error",
                exit_code=6,
            ),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-network-timeout",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert payload["evidence"]["source_surfaces"][-2:] == ["cdp:network", "cdp:page-close"]
    assert any("network evidence capture failed" in warning for warning in payload["warnings"])
    assert (tmp_path / "runs" / "gf-test-network-timeout" / "network.json").is_file()


def test_live_search_stops_on_blocked_headless_state(tmp_path: Path) -> None:
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
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-blocked",
        )
    )

    assert exit_code == 4
    assert payload["status"] == "blocked"
    assert payload["stop_state"] == "unusual_traffic"
    assert payload["fallback"]["recommended_browser_mode"] == "headed"
    assert payload["evidence"]["run_id"] == "gf-test-blocked"
    assert len(adapter.calls) == 1
    assert (tmp_path / "runs" / "gf-test-blocked" / "open.json").is_file()


def test_live_search_stops_when_terminal_wait_returns_login_required(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights/search?tfs=encoded",
                    },
                },
            ),
            cdp_result(["wait", "load-state"], {"ok": True}),
        ],
        settlement_terminal_result=cdp_result(
            ["wait", "eval"],
            {
                "ok": True,
                "wait": {
                    "matched": True,
                    "evidence": {"value": "login_required"},
                },
            },
        ),
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-consent-stop",
        )
    )

    assert exit_code == 4
    assert payload["status"] == "login_required"
    assert payload["stop_state"] == "login_required"
    assert payload["fallback"]["recommended_browser_mode"] == "headed"
    assert not any(call[0][:2] == ["text", "body"] for call in adapter.calls)


def test_live_search_can_execute_fake_form_interaction_steps(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            *[cdp_result(["form"], {"ok": True}) for _ in range(12)],
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Search results"}]}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-live-form",
            interact_with_form=True,
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert "cdp:form:origin-fill" in payload["evidence"]["source_surfaces"]
    assert "cdp:form:search-submit" in payload["evidence"]["source_surfaces"]
    assert adapter.calls[2][0] == [
        "fill",
        "Where from?",
        "CPH",
        "--by",
        "label",
        "--exact",
        "--target",
        "page-1",
        "--wait-text",
        "CPH",
    ]
    assert any(call[0][0:2] == ["click", "Add adult"] for call in adapter.calls)
    assert any(call[0][0] == "snapshot" for call in adapter.calls)
    assert any(call[0][0] == "eval" and "Flight details" in call[0][1] for call in adapter.calls)
    assert any(
        call[0][0] == "eval" and "collectAccessibleFlightRows" in call[0][1]
        for call in adapter.calls
    )

    run_root = tmp_path / "runs" / "gf-test-live-form"
    assert (run_root / "form-origin-fill.json").is_file()
    assert (run_root / "form-search-submit.json").is_file()


def test_live_search_extracts_primary_results_from_snapshot_payload(tmp_path: Path) -> None:
    fixture = json.loads((FIXTURES / "primary_results_visible_text_fixture.json").read_text())
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], fixture["snapshot"]),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-results",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["confidence"] == "weak"
    assert payload["unsupported"] == []
    assert payload["results"][0]["carriers"] == ["KLM", "IndiGo"]
    assert payload["results"][0]["price"] == {"amount": 3206, "currency": "EUR", "text": "€3,206"}
    assert payload["results"][0]["duration_minutes"] == 920
    assert payload["results"][0]["evidence"]["artifacts"] == [
        str(tmp_path / "runs" / "gf-test-results" / "snapshot.json")
    ]


def test_live_search_prefers_accessible_rows_over_snapshot_payload(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=USD",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], {"ok": True, "items": [{"text": "Flights"}]}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ],
        accessible_rows_payload={
            "ok": True,
            "result": {
                "value": [
                    {
                        "rank": 3,
                        "text": (
                            "8:59 PM JFK 12:36 AM+1 SFO $275 round trip "
                            "Nonstop6 hr 37 minJetBlue +15% emissions"
                        ),
                        "ariaLabel": (
                            "Select flight, JetBlue flight with JetBlue. "
                            "Total duration 6 hr 37 min. Nonstop. "
                            "From 275 US dollars round trip. "
                            "1 carry-on bag included. 0 checked bags included."
                        ),
                        "combinedText": (
                            "8:59 PM JFK 12:36 AM+1 SFO $275 round trip "
                            "Nonstop6 hr 37 minJetBlue +15% emissions "
                            "Select flight, JetBlue flight with JetBlue. "
                            "Total duration 6 hr 37 min. Nonstop. "
                            "From 275 US dollars round trip. "
                            "1 carry-on bag included. 0 checked bags included."
                        ),
                    }
                ]
            },
        },
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-accessible-results",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["confidence"] == "medium"
    assert payload["results"][0]["source_surface"] == "primary-results-accessible-rows"
    assert payload["results"][0]["carriers"] == ["JetBlue"]
    assert payload["results"][0]["price"] == {"amount": 275, "currency": "USD", "text": "$275"}
    assert payload["results"][0]["baggage_summary"]["checked_bags_included"] == 0
    assert payload["results"][0]["evidence"]["artifacts"] == [
        str(tmp_path / "runs" / "gf-test-accessible-results" / "accessible-rows.json")
    ]
    assert (tmp_path / "runs" / "gf-test-accessible-results" / "accessible-rows.json").is_file()


def test_live_search_retries_loading_snapshot_before_parsing_rows(tmp_path: Path) -> None:
    fixture = json.loads((FIXTURES / "primary_results_visible_text_fixture.json").read_text())
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(
                ["snapshot"],
                {"ok": True, "snapshot": {"items": [{"text": "Search results Loading results"}]}},
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], fixture["snapshot"]),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-results-retry",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["results"][0]["price"]["amount"] == 3206
    assert payload["results"][0]["evidence"]["artifacts"] == [
        str(tmp_path / "runs" / "gf-test-results-retry" / "snapshot-results-retry-1.json")
    ]
    assert (
        ["wait", "network-idle", "--target", "page-1", "--idle", "2s"],
        "headless",
        10.0,
    ) in adapter.calls
    assert any(
        call[0] == ["snapshot", "--target", "page-1", "--limit", "120"] for call in adapter.calls
    )


def test_live_search_does_not_retry_when_loading_snapshot_already_has_rows(
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
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(["snapshot"], compact_current_results_snapshot()),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-loading-with-rows",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert [result["price"]["amount"] for result in payload["results"]] == [3561, 3794]
    assert payload["cache"]["price_observations_written"] == 2
    assert all(
        call[0][0:2] != ["wait", "network-idle"] or call[0][-1] != "2s" for call in adapter.calls
    )


def test_live_search_reports_loading_snapshot_as_specific_unsupported(
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
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(
                ["snapshot"],
                {"ok": True, "snapshot": {"items": [{"text": "Search results Loading results"}]}},
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(
                ["snapshot"],
                {"ok": True, "snapshot": {"items": [{"text": "Search results Loading results"}]}},
            ),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-loading-timeout",
        )
    )

    assert exit_code == 3
    assert payload["status"] == "unsupported"
    assert payload["results"] == []
    assert payload["unsupported"][0]["field"] == "live_result_extraction.loading_results"
    assert "bounded evidence wait" in payload["unsupported"][0]["reason"]


def test_live_search_reports_google_page_error_as_retryable_tool_error(
    tmp_path: Path,
) -> None:
    google_error_text = "Search results No results returned. Oops, something went wrong. Reload"
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=USD",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(
                ["snapshot"],
                {"ok": True, "snapshot": {"items": [{"text": google_error_text}]}},
            ),
            cdp_result(["network"], {"ok": True, "requests": []}),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ],
        settlement_terminal_result=cdp_result(
            ["wait", "eval"],
            {"ok": True, "result": {"value": "google_page_error"}},
        ),
        settlement_text_samples=[google_error_text, google_error_text],
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-google-page-error",
        )
    )

    assert exit_code == 6
    assert payload["status"] == "tool_error"
    assert payload["stop_state"] == "google_page_error"
    assert "transient error" in payload["error"]


def test_live_search_currency_footer_is_not_terminal_readiness(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=DKK",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(
                ["snapshot"],
                {"ok": True, "snapshot": {"items": [{"text": "Search results CurrencyDKK"}]}},
            ),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ],
        settlement_terminal_result=cdp_result(
            ["wait", "eval"],
            {"ok": False, "message": "terminal content timeout"},
            status="tool_error",
            exit_code=6,
        ),
        settlement_text_samples=["Search results CurrencyDKK", "Search results CurrencyDKK"],
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-currency-footer",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "experimental"
    assert any(
        "terminal Google Flights content did not appear" in warning
        for warning in payload["warnings"]
    )
    settlement = json.loads(
        (tmp_path / "runs" / "gf-test-currency-footer" / "settlement.json").read_text()
    )
    assert settlement["terminal_status"] == "tool_error"
    assert settlement["terminal_condition"] == "unknown"
    assert settlement["body_stability"]["status"] == "stable"


def test_live_search_retries_snapshot_after_result_wait_timeout(tmp_path: Path) -> None:
    fixture = json.loads((FIXTURES / "primary_results_visible_text_fixture.json").read_text())
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(
                ["snapshot"],
                {"ok": True, "snapshot": {"items": [{"text": "Search results Loading results"}]}},
            ),
            cdp_result(
                ["wait"],
                {"ok": False, "message": "cdp command timed out after 10s"},
                status="tool_error",
                exit_code=6,
            ),
            cdp_result(["snapshot"], fixture["snapshot"]),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-results-retry-after-timeout",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["results"][0]["price"]["amount"] == 3206
    assert any("network-idle wait failed" in warning for warning in payload["warnings"])
    assert any(
        call[0] == ["snapshot", "--target", "page-1", "--limit", "120"] for call in adapter.calls
    )


def test_live_search_reports_visible_no_results_status(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(
                ["snapshot"],
                {
                    "ok": True,
                    "snapshot": {
                        "items": [{"text": "Search results No flights found. Try changing dates."}]
                    },
                },
            ),
            cdp_result(["network"], {"ok": True, "requests": []}),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-no-results",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "no_results"
    assert payload["unsupported"] == []


def test_live_search_writes_extracted_result_prices_to_sqlite_cache(tmp_path: Path) -> None:
    fixture = json.loads((FIXTURES / "primary_results_visible_text_fixture.json").read_text())
    adapter = FakeCdpAdapter(successful_capture_results(fixture["snapshot"]))

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-cache",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    with sqlite3.connect(tmp_path / "cache" / "cache.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT cache_key, query_id, departure_date, return_date, currency,
                   price_amount, price_payload, source_run_id
            FROM flight_price_cache
            ORDER BY price_amount
            """
        ).fetchall()

    assert len(rows) == 2
    first = rows[0]
    assert first["query_id"] == "live-cph-del"
    assert first["departure_date"] == "2026-10-01"
    assert first["return_date"] == "2026-11-24"
    assert first["currency"] == "EUR"
    assert first["price_amount"] == 3206
    assert first["source_run_id"] == "gf-test-cache"
    assert "live-cph-del" in first["cache_key"]
    assert "2026-10-01" in first["cache_key"]
    cached_payload = json.loads(first["price_payload"])
    assert cached_payload["price"] == {"amount": 3206, "currency": "EUR", "text": "€3,206"}
    assert cached_payload["result_id"] == "visible-text-result-1"
    assert cached_payload["carriers"] == ["KLM", "IndiGo"]
    assert "requests" not in cached_payload
    assert "stdout" not in cached_payload
    assert "json_payload" not in cached_payload
    assert (
        PriceCache(tmp_path / "cache" / "cache.sqlite").get_fresh_price(first["cache_key"])
        is not None
    )


def test_live_search_preserves_json_array_input_order(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            *successful_capture_results(),
            *successful_capture_results(),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=FIXTURES / "search_intents.json",
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
        )
    )

    assert exit_code == 0
    assert isinstance(payload, list)
    assert [item["query_id"] for item in payload] == [
        "del-cph-window-oct-nov",
        "cph-lko-oneway-jun",
    ]
    assert len(adapter.calls) == 16


def test_live_search_real_adapter_batch_uses_bounded_concurrency(tmp_path: Path) -> None:
    active = 0
    max_active = 0
    page_counter = 0

    async def runner(argv: Sequence[str], timeout_seconds: float) -> ProcessResult:
        del timeout_seconds
        nonlocal active, max_active, page_counter
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.001)
            if "open" in argv:
                page_counter += 1
                payload = {"ok": True, "page": {"id": f"page-{page_counter}"}}
            elif "snapshot" in argv:
                payload = {"ok": True, "items": [{"text": "Flights"}]}
            elif "text" in argv:
                payload = {"ok": True, "items": [{"text": "Search results DKK 12,054 round trip"}]}
            elif "network" in argv:
                payload = {"ok": True, "requests": []}
            else:
                payload = {"ok": True, "result": {"value": "fare_rows"}}
            return ProcessResult(0, json.dumps(payload), "")
        finally:
            active -= 1

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=FIXTURES / "search_intents.json",
            project_root=tmp_path,
            adapter=CdpAdapter(runner=runner),
            browser_mode="headless",
            batch_concurrency=2,
        )
    )

    assert exit_code == 0
    assert isinstance(payload, list)
    assert [item["query_id"] for item in payload] == [
        "del-cph-window-oct-nov",
        "cph-lko-oneway-jun",
    ]
    assert max_active > 1
    task_traces = [item["evidence"]["task_trace"] for item in payload]
    root_task_ids = {trace["root_task_id"] for trace in task_traces}
    task_ids = {trace["task_id"] for trace in task_traces}
    assert len(root_task_ids) == 1
    assert len(task_ids) == 2
    root_task_id = next(iter(root_task_ids))
    assert_uuid4(root_task_id)
    for trace in task_traces:
        assert_uuid4(trace["task_id"])
        assert trace["parent_task_id"] == root_task_id


def test_live_search_reports_query_population_boundary_per_batch_item(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            *successful_capture_results(),
            *successful_capture_results(),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=FIXTURES / "search_intents.json",
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
        )
    )

    assert exit_code == 0
    assert isinstance(payload, list)
    assert payload[0]["query_population"]["status"] == "unsupported"
    assert payload[0]["unsupported"][0]["field"] == "departure_window"
    assert "tfs=" not in payload[0]["target_url"]
    assert payload[0]["target_url"].startswith("https://www.google.com/travel/flights?")
    assert payload[1]["query_population"]["status"] == "encoded"
    assert "tfs=" in payload[1]["target_url"]
    assert payload[1]["target_url"].startswith("https://www.google.com/travel/flights/search?")
    assert payload[1]["unsupported"][0]["field"] == "live_result_extraction"


def test_live_search_stops_on_form_interaction_stop_state(tmp_path: Path) -> None:
    adapter = FakeCdpAdapter(
        [
            cdp_result(
                ["open"],
                {
                    "ok": True,
                    "page": {
                        "id": "page-1",
                        "url": "https://www.google.com/travel/flights?hl=en&curr=EUR",
                    },
                },
            ),
            cdp_result(["wait"], {"ok": True}),
            cdp_result(
                ["form"],
                {"status": "blocked", "stop_state": "human_required"},
                status="blocked",
                exit_code=4,
                fallback={
                    "recommended_browser_mode": "headed",
                    "reason": "headless blocked or human confirmation required",
                },
            ),
        ]
    )

    exit_code, payload = asyncio.run(
        run_live_search(
            input_json=write_intent(tmp_path),
            project_root=tmp_path,
            adapter=adapter,
            browser_mode="headless",
            run_id="gf-test-form-blocked",
            interact_with_form=True,
        )
    )

    assert exit_code == 4
    assert payload["status"] == "blocked"
    assert payload["stop_state"] == "human_required"
    assert payload["fallback"]["recommended_browser_mode"] == "headed"
    assert len(adapter.calls) == 4
    assert (tmp_path / "runs" / "gf-test-form-blocked" / "form-origin-fill.json").is_file()
