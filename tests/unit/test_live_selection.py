from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from gflights.browser import BrowserMode, CdpResult
from gflights.live_selection import _is_transient_operation_failure, run_live_itinerary_selection


class FakeSelectionCdpAdapter:
    def __init__(self, *, selection_point: dict[str, float] | None = None) -> None:
        self.calls: list[tuple[list[str], BrowserMode, float]] = []
        self.selection_calls = 0
        self.stage_state_calls: dict[str, int] = {}
        self.click_attempts = 0
        self.selection_point = selection_point

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
        if call_args[0] == "eval" and "const requestedStage =" in call_args[1]:
            stage = _requested_stage(call_args[1])
            self.stage_state_calls[stage] = self.stage_state_calls.get(stage, 0) + 1
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "result": {
                        "value": _stage_state(stage),
                    },
                },
            )
        if call_args[0] == "eval" and "consideredRankCount" in call_args[1]:
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "result": {
                        "value": {
                            "consideredRankCount": 5,
                            "expandedClickCount": 5,
                            "alreadyExpandedCount": 0,
                            "records": [
                                {
                                    "rank": index,
                                    "expandedBefore": "false",
                                    "expandedAfter": "true",
                                }
                                for index in range(1, 6)
                            ],
                        }
                    },
                },
            )
        if call_args[0] == "eval" and "found: true" in call_args[1]:
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
                            "found": True,
                            "selected": False,
                            "text": selected_text,
                            "ariaLabel": selected_text,
                            "combinedText": selected_text,
                            "rowRank": row_rank,
                            "matchText": match_text,
                            "candidateCount": 3,
                            "matchCount": 1,
                            "locatorStrategy": "stage-heading > [role=list] > [role=link][aria-label*=Select flight] -> closest li",
                        }
                    },
                },
            )
        if call_args[0] == "eval" and "clickDispatch: 'dom-link-click'" in call_args[1]:
            self.click_attempts += 1
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "result": {
                        "value": {
                            "clicked": True,
                            "clickDispatch": "dom-link-click",
                        }
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
        if call_args[:3] == ["protocol", "exec", "Input.dispatchMouseEvent"]:
            return cdp_result(call_args, {"ok": True, "result": {}})
        if call_args[0] == "eval":
            return cdp_result(call_args, {"ok": True, "result": {"value": {}}})
        if call_args[0] == "click":
            return cdp_result(call_args, {"ok": True, "click": {"clicked": True}})
        if call_args[:2] == ["page", "close"]:
            return cdp_result(call_args, {"ok": True})
        raise AssertionError(f"unexpected cdp call: {call_args}")


class StableRetrySelectionCdpAdapter(FakeSelectionCdpAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.click_attempts = 0

    async def run_json(
        self,
        args: Sequence[str],
        *,
        browser_mode: BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> CdpResult:
        call_args = list(args)
        if call_args[0] == "eval" and "clickDispatch: 'dom-link-click'" in call_args[1]:
            self.calls.append((call_args, browser_mode, timeout_seconds))
            self.click_attempts += 1
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "result": {
                        "value": {
                            "clicked": self.click_attempts > 1,
                            "clickDispatch": "dom-link-click",
                        }
                    },
                },
            )
        return await super().run_json(
            args,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
        )


class ReturnStageTimeoutSelectionCdpAdapter(FakeSelectionCdpAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.return_stage_calls = 0

    async def run_json(
        self,
        args: Sequence[str],
        *,
        browser_mode: BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> CdpResult:
        call_args = list(args)
        if call_args[0] == "eval" and 'const requestedStage = "return"' in call_args[1]:
            self.return_stage_calls += 1
            if self.return_stage_calls == 1:
                return await super().run_json(
                    args,
                    browser_mode=browser_mode,
                    timeout_seconds=timeout_seconds,
                )
            self.calls.append((call_args, browser_mode, timeout_seconds))
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "result": {
                        "value": {
                            "requestedStage": "return",
                            "terminalCondition": "no_results",
                            "rowCount": 0,
                            "rows": [],
                        }
                    },
                },
            )
        return await super().run_json(
            args,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
        )


class SearchUrlAfterSelectionCdpAdapter(FakeSelectionCdpAdapter):
    async def run_json(
        self,
        args: Sequence[str],
        *,
        browser_mode: BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> CdpResult:
        call_args = list(args)
        if call_args[0] == "eval" and call_args[1] == "window.location.href":
            self.calls.append((call_args, browser_mode, timeout_seconds))
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "result": {
                        "value": "https://www.google.com/travel/flights/search?tfs=still-search"
                    },
                },
            )
        return await super().run_json(
            args,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
        )


class ContextRaceSelectionCdpAdapter(FakeSelectionCdpAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.outbound_wait_attempts = 0

    async def run_json(
        self,
        args: Sequence[str],
        *,
        browser_mode: BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> CdpResult:
        call_args = list(args)
        if call_args[0] == "eval" and 'const requestedStage = "outbound"' in call_args[1]:
            self.outbound_wait_attempts += 1
            if self.outbound_wait_attempts == 1:
                self.calls.append((call_args, browser_mode, timeout_seconds))
                return cdp_result(
                    call_args,
                    {
                        "ok": False,
                        "code": "connection_failed",
                        "message": (
                            "cdp Runtime.evaluate failed: Cannot find default execution context"
                        ),
                    },
                    status="tool_error",
                    exit_code=6,
                )
        return await super().run_json(
            args,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
        )


class GooglePageErrorRecoverySelectionCdpAdapter(FakeSelectionCdpAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.reloaded = False
        self.reload_calls = 0

    async def run_json(
        self,
        args: Sequence[str],
        *,
        browser_mode: BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> CdpResult:
        call_args = list(args)
        if call_args[0] == "eval" and "gflights-google-page-error-recovery" in call_args[1]:
            self.calls.append((call_args, browser_mode, timeout_seconds))
            self.reloaded = True
            self.reload_calls += 1
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "result": {
                        "value": {
                            "action": "click_reload",
                            "label": "Reload",
                            "marker": "gflights-google-page-error-recovery",
                        }
                    },
                },
            )
        if (
            call_args[0] == "eval"
            and 'const requestedStage = "outbound"' in call_args[1]
            and not self.reloaded
        ):
            self.calls.append((call_args, browser_mode, timeout_seconds))
            self.stage_state_calls["outbound"] = self.stage_state_calls.get("outbound", 0) + 1
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "result": {
                        "value": {
                            "requestedStage": "outbound",
                            "terminalCondition": "google_page_error",
                            "rowCount": 0,
                            "rows": [],
                            "currentUrl": "https://www.google.com/travel/flights/search?tfs=encoded",
                            "currentTitle": "Google Flights",
                            "hasBookingOptions": False,
                            "bodySample": "Oops, something went wrong Reload",
                        }
                    },
                },
            )
        return await super().run_json(
            args,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
        )


class ReturnSelectionTransientDisconnectAdapter(FakeSelectionCdpAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.failed_return_prepare = False

    async def run_json(
        self,
        args: Sequence[str],
        *,
        browser_mode: BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> CdpResult:
        call_args = list(args)
        if (
            call_args[0] == "eval"
            and "found: true" in call_args[1]
            and self.selection_calls == 1
            and not self.failed_return_prepare
        ):
            self.calls.append((call_args, browser_mode, timeout_seconds))
            self.failed_return_prepare = True
            message = (
                "check browser resource budget: failed to read JSON message: "
                "failed to get reader: use of closed network connection"
            )
            return cdp_result(
                call_args,
                {
                    "ok": False,
                    "code": "connection_failed",
                    "message": message,
                },
                status="tool_error",
                exit_code=6,
                error=message,
            )
        return await super().run_json(
            args,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
        )


def _requested_stage(js: str) -> str:
    for stage in ("outbound", "return", "booking"):
        if f'const requestedStage = "{stage}"' in js:
            return stage
    return "outbound"


def _stage_state(stage: str) -> dict[str, object]:
    if stage == "booking":
        return {
            "requestedStage": "booking",
            "terminalCondition": "booking_summary",
            "rowCount": 0,
            "rows": [],
            "currentUrl": "https://www.google.com/travel/flights/booking?tfs=encoded",
            "currentTitle": "Round trip | Google Flights",
            "hasBookingOptions": True,
            "bodySample": "Booking options Book with Finnair Airline DKK 13,997 Continue",
        }
    return {
        "requestedStage": stage,
        "terminalCondition": "fare_rows",
        "rowCount": 3,
        "rows": [],
        "currentUrl": "https://www.google.com/travel/flights/search?tfs=encoded",
        "currentTitle": "Google Flights",
        "hasBookingOptions": False,
        "bodySample": f"{stage} flights DKK 13,997 round trip Finnair",
    }


def cdp_result(
    args: list[str],
    payload: dict[str, object],
    *,
    status: str = "ok",
    exit_code: int = 0,
    browser_mode: BrowserMode = "headless",
    error: str = "",
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
        error=error,
        stop_state=payload.get("stop_state")
        if isinstance(payload.get("stop_state"), str)
        else None,
    )


def assert_uuid4(value: str) -> None:
    parsed = UUID(value)
    assert parsed.version == 4
    assert str(parsed) == value


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


def test_live_itinerary_selection_recovers_workflow_created_target_with_uuid_trace(
    tmp_path: Path,
) -> None:
    page_id = "9A29955C057DBDACA7E81371E4DCB2C4"

    class RecoveredOpenSelectionCdpAdapter(FakeSelectionCdpAdapter):
        async def run_json(
            self,
            args: Sequence[str],
            *,
            browser_mode: BrowserMode = "headless",
            timeout_seconds: float = 30.0,
        ) -> CdpResult:
            call_args = list(args)
            if call_args[0] == "open":
                self.calls.append((call_args, browser_mode, timeout_seconds))
                return cdp_result(
                    call_args,
                    {
                        "ok": False,
                        "message": (f"failed to record workflow-created page {page_id} after open"),
                    },
                    status="tool_error",
                    exit_code=6,
                    error="workflow-created page artifact write failed",
                )
            return await super().run_json(
                args,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
            )

    adapter = RecoveredOpenSelectionCdpAdapter()

    exit_code, payload = asyncio.run(
        run_live_itinerary_selection(
            search_url="https://www.google.com/travel/flights/search?tfs=encoded&hl=en&curr=DKK",
            project_root=tmp_path,
            adapter=adapter,  # type: ignore[arg-type]
            run_id="gf-test-selection-recovered-open",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert any("workflow-created-page recording error" in item for item in payload["warnings"])
    assert adapter.calls[-1][0] == ["page", "close", "--target", page_id]

    run_root = tmp_path / "runs" / "gf-test-selection-recovered-open"
    task_trace = json.loads((run_root / "task-trace.json").read_text())
    assert task_trace["managed_tab_id"] == page_id
    assert_uuid4(task_trace["task"]["task_id"])
    assert_uuid4(task_trace["task"]["root_task_id"])
    assert_uuid4(task_trace["managed_tab_task"]["task_id"])
    assert task_trace["managed_tab_task"]["parent_task_id"] == task_trace["task"]["task_id"]
    assert task_trace["target_task_ids"] == {page_id: task_trace["managed_tab_task"]["task_id"]}

    close_trace = json.loads((run_root / "managed-tab-close.json").read_text())
    assert close_trace["target_task_ids"] == task_trace["target_task_ids"]


def test_live_itinerary_selection_retries_animation_click_with_force(tmp_path: Path) -> None:
    adapter = StableRetrySelectionCdpAdapter()

    exit_code, payload = asyncio.run(
        run_live_itinerary_selection(
            search_url="https://www.google.com/travel/flights/search?tfs=encoded&hl=en&curr=DKK",
            project_root=tmp_path,
            adapter=adapter,  # type: ignore[arg-type]
            run_id="gf-test-selection-click-retry",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert adapter.click_attempts == 2
    assert not any(call[0][0] == "click" for call in adapter.calls)
    assert (
        tmp_path / "runs" / "gf-test-selection-click-retry" / "outbound-click-selected-row-01.json"
    ).is_file()
    assert (
        tmp_path / "runs" / "gf-test-selection-click-retry" / "return-click-selected-row-01.json"
    ).is_file()


def test_live_itinerary_selection_stops_when_return_stage_never_appears(
    tmp_path: Path,
) -> None:
    adapter = ReturnStageTimeoutSelectionCdpAdapter()

    exit_code, payload = asyncio.run(
        run_live_itinerary_selection(
            search_url="https://www.google.com/travel/flights/search?tfs=encoded&hl=en&curr=DKK",
            project_root=tmp_path,
            adapter=adapter,  # type: ignore[arg-type]
            run_id="gf-test-selection-return-not-ready",
        )
    )

    assert exit_code == 3
    assert payload["status"] == "unsupported"
    assert payload["booking_url"] == ""
    assert payload["settlement"]["stage"] == "return"
    assert payload["settlement"]["ready"] is False
    assert payload["unsupported"][0]["field"] == "google_flights_stage.return"
    assert adapter.selection_calls == 1


def test_live_itinerary_selection_retries_terminal_eval_after_context_race(
    tmp_path: Path,
) -> None:
    adapter = ContextRaceSelectionCdpAdapter()

    exit_code, payload = asyncio.run(
        run_live_itinerary_selection(
            search_url="https://www.google.com/travel/flights/search?tfs=encoded&hl=en&curr=DKK",
            project_root=tmp_path,
            adapter=adapter,  # type: ignore[arg-type]
            run_id="gf-test-selection-context-race",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert adapter.outbound_wait_attempts == 2
    assert adapter.selection_calls == 2
    assert (
        tmp_path / "runs" / "gf-test-selection-context-race" / "outbound-stage-state-02.json"
    ).is_file()


def test_live_itinerary_selection_recovers_google_page_error_with_reload(
    tmp_path: Path,
) -> None:
    adapter = GooglePageErrorRecoverySelectionCdpAdapter()

    exit_code, payload = asyncio.run(
        run_live_itinerary_selection(
            search_url="https://www.google.com/travel/flights/search?tfs=encoded&hl=en&curr=DKK",
            project_root=tmp_path,
            adapter=adapter,  # type: ignore[arg-type]
            run_id="gf-test-selection-google-page-error-reload",
            operation_retries=0,
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert adapter.reload_calls == 1
    run_root = tmp_path / "runs" / "gf-test-selection-google-page-error-reload"
    assert (run_root / "outbound-google-page-error-reload-01.json").is_file()
    assert (run_root / "outbound-post-reload-01-stage-state-01.json").is_file()
    assert not (
        tmp_path / "runs" / "gf-test-selection-google-page-error-reload-attempt-02"
    ).exists()
    settlement = json.loads((run_root / "outbound-settlement.json").read_text())
    assert settlement["terminal_condition"] == "fare_rows"
    reload_attempts = settlement["body_stability"]["google_page_error_reload_attempts"]
    assert reload_attempts[0]["action"] == "click_reload"
    assert reload_attempts[0]["post_reload_condition"] == "fare_rows"
    assert any("recovered after 1 reload attempt" in warning for warning in settlement["warnings"])


def test_live_itinerary_selection_retries_whole_operation_after_transient_return_error(
    tmp_path: Path,
) -> None:
    adapter = ReturnSelectionTransientDisconnectAdapter()

    exit_code, payload = asyncio.run(
        run_live_itinerary_selection(
            search_url="https://www.google.com/travel/flights/search?tfs=encoded&hl=en&curr=DKK",
            project_root=tmp_path,
            adapter=adapter,  # type: ignore[arg-type]
            run_id="gf-test-selection-operation-retry",
            operation_retries=2,
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["booking_url"].startswith("https://www.google.com/travel/flights/booking?")
    diagnostics = payload["diagnostics"]
    assert diagnostics["operation_attempt_count"] == 2
    assert diagnostics["operation_retry_limit"] == 2
    assert diagnostics["transient_retry_exhausted"] is False
    assert diagnostics["operation_attempts"][0]["status"] == "tool_error"
    assert diagnostics["operation_attempts"][0]["transient_retryable"] is True
    assert diagnostics["operation_attempts"][0]["failed_stage"] == "return"
    assert diagnostics["operation_attempts"][1]["status"] == "ok"
    assert (tmp_path / "runs" / "gf-test-selection-operation-retry").is_dir()
    assert (tmp_path / "runs" / "gf-test-selection-operation-retry-attempt-02").is_dir()


def test_live_itinerary_selection_treats_stage_not_ready_timeout_as_retryable() -> None:
    assert _is_transient_operation_failure(
        {
            "status": "unsupported",
            "settlement": {
                "stage": "outbound",
                "ready": False,
                "terminal_condition": "not_ready",
                "terminal_status": "assertion_timeout",
            },
        }
    )
    assert not _is_transient_operation_failure(
        {
            "status": "unsupported",
            "settlement": {
                "stage": "outbound",
                "ready": False,
                "terminal_condition": "no_results",
                "terminal_status": "ok",
            },
        }
    )


def test_live_itinerary_selection_treats_row_click_transition_timeout_as_retryable() -> None:
    assert _is_transient_operation_failure(
        {
            "status": "unsupported",
            "selection": {
                "return": {
                    "found": True,
                    "clicked": True,
                    "transitionMatched": False,
                    "transitionCondition": "assertion_timeout",
                }
            },
        }
    )
    assert not _is_transient_operation_failure(
        {
            "status": "unsupported",
            "selection": {
                "return": {
                    "found": False,
                    "clicked": False,
                    "transitionMatched": False,
                    "transitionCondition": "assertion_timeout",
                }
            },
        }
    )


def test_live_itinerary_selection_treats_target_not_found_as_retryable() -> None:
    assert _is_transient_operation_failure(
        {
            "status": "tool_error",
            "error": 'no target "6BA5EF5BC1C639BC5998476ACF70913C" matched',
            "diagnostics": {"failed_stage": "outbound"},
        }
    )


def test_live_itinerary_selection_does_not_report_search_url_as_booking_url(
    tmp_path: Path,
) -> None:
    adapter = SearchUrlAfterSelectionCdpAdapter()

    exit_code, payload = asyncio.run(
        run_live_itinerary_selection(
            search_url="https://www.google.com/travel/flights/search?tfs=encoded&hl=en&curr=DKK",
            project_root=tmp_path,
            adapter=adapter,  # type: ignore[arg-type]
            run_id="gf-test-selection-not-booking-url",
        )
    )

    assert exit_code == 3
    assert payload["status"] == "unsupported"
    assert payload["booking_url"] == ""
    assert payload["current_url"].endswith("search?tfs=still-search")
    assert payload["selected_outbound"]["parsed"] is True
    assert payload["selected_return"]["parsed"] is True


def test_live_itinerary_selection_uses_coordinate_protocol_click(tmp_path: Path) -> None:
    adapter = FakeSelectionCdpAdapter(selection_point={"x": 392.5, "y": 256.25})

    exit_code, payload = asyncio.run(
        run_live_itinerary_selection(
            search_url="https://www.google.com/travel/flights/search?tfs=encoded&hl=en&curr=DKK",
            project_root=tmp_path,
            adapter=adapter,  # type: ignore[arg-type]
            run_id="gf-test-selection-coordinate-click",
        )
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert not any(call[0][0] == "click" for call in adapter.calls)
    protocol_calls = [
        call[0]
        for call in adapter.calls
        if call[0][:3] == ["protocol", "exec", "Input.dispatchMouseEvent"]
    ]
    assert len(protocol_calls) == 0
    assert (
        tmp_path
        / "runs"
        / "gf-test-selection-coordinate-click"
        / "outbound-click-selected-row-01.json"
    ).is_file()
    assert (
        tmp_path
        / "runs"
        / "gf-test-selection-coordinate-click"
        / "return-click-selected-row-01.json"
    ).is_file()


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
        call[0][1] for call in adapter.calls if call[0][0] == "eval" and "found: true" in call[0][1]
    ]
    assert "7:40 AM" in selection_evals[0]
    assert "Math.max(1, 2)" in selection_evals[0]
    assert "1:00 PM" in selection_evals[1]


class ConsentBlockedSelectionCdpAdapter(FakeSelectionCdpAdapter):
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
            return cdp_result(call_args, {"ok": True, "budget": {"tab_count": 1, "max_tabs": 3}})
        if call_args[0] == "open":
            return cdp_result(call_args, {"ok": True, "page": {"id": "page-1"}})
        if call_args[0] == "eval" and "const requestedStage =" in call_args[1]:
            return cdp_result(
                call_args,
                {
                    "ok": True,
                    "result": {
                        "value": {
                            "requestedStage": "outbound",
                            "terminalCondition": "login_required",
                            "rowCount": 0,
                            "rows": [],
                        }
                    },
                },
            )
        if call_args[:2] == ["page", "close"]:
            return cdp_result(call_args, {"ok": True})
        raise AssertionError(f"unexpected cdp call: {call_args}")


def test_live_itinerary_selection_stops_when_wait_returns_login_required(
    tmp_path: Path,
) -> None:
    adapter = ConsentBlockedSelectionCdpAdapter()

    exit_code, payload = asyncio.run(
        run_live_itinerary_selection(
            search_url="https://www.google.com/travel/flights/search?tfs=encoded&hl=en&curr=DKK",
            project_root=tmp_path,
            adapter=adapter,  # type: ignore[arg-type]
            run_id="gf-test-selection-consent",
            max_tabs=3,
        )
    )

    assert exit_code == 4
    assert payload["status"] == "login_required"
    assert payload["stop_state"] == "login_required"
    assert payload["selection"] is None
    assert not any(call[0][0] == "eval" and "found: true" in call[0][1] for call in adapter.calls)
