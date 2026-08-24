"""Live Google Flights row selection to booking-summary URL."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from gflights.app_state import init_app_state
from gflights.browser import BLOCKED_STOP_STATES, BrowserMode, CdpAdapter, CdpResult, run_subprocess
from gflights.cdp_evidence import CdpEvidenceHelper
from gflights.google_flights_flow import (
    TRANSIENT_STAGE_CONDITIONS,
    expand_accessible_rows_until_stable,
    select_accessible_row_until_next_stage,
    wait_until_google_flights_stage_ready,
)
from gflights.itinerary_extraction import extract_selected_itinerary
from gflights.live_cleanup import close_managed_page
from gflights.live_search import MINIMUM_GOOGLE_FLIGHTS_DWELL_SECONDS
from gflights.live_tabs import (
    capture_tab_budget,
    open_args_for_policy,
    tab_budget_enabled,
    tab_budget_summary,
)
from gflights.live_trace import (
    TaskTrace,
    new_task_trace,
    open_result_page_id,
    recoverable_open_page_warning,
)
from gflights.result_extraction import extract_primary_results

GOOGLE_PAGE_ERROR_RELOAD_ATTEMPTS = 2
GOOGLE_PAGE_ERROR_RELOAD_JS = r"""
(() => {
  const marker = "gflights-google-page-error-recovery";
  const textOf = (el) => [
    el.getAttribute && el.getAttribute("aria-label"),
    el.innerText,
    el.textContent,
  ].filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  const candidates = Array.from(document.querySelectorAll('button,[role="button"],a'));
  const target = candidates.find((el) => {
    const disabled = el.disabled || el.getAttribute("aria-disabled") === "true";
    const text = textOf(el).toLowerCase();
    return !disabled && /\b(reload|try again)\b/.test(text);
  });
  if (target) {
    const label = textOf(target);
    target.setAttribute("data-gflights-recovery", marker);
    target.scrollIntoView({block: "center", inline: "center"});
    setTimeout(() => target.click(), 0);
    return {action: "click_reload", label, marker};
  }
  setTimeout(() => window.location.reload(), 0);
  return {action: "location_reload", marker};
})()
""".strip()


async def run_live_itinerary_selection(
    *,
    search_url: str,
    project_root: Path | None = None,
    adapter: CdpAdapter | None = None,
    browser_mode: BrowserMode = "headed",
    run_id: str | None = None,
    timeout_seconds: float = 45.0,
    preferred_carrier: str = "",
    require_nonstop: bool = False,
    row_rank: int = 1,
    outbound_row_rank: int | None = None,
    return_row_rank: int | None = None,
    outbound_match_text: str = "",
    return_match_text: str = "",
    reuse_target: str = "",
    max_tabs: int | None = None,
    allow_over_budget: bool = False,
    operation_retries: int = 2,
    task_trace: TaskTrace | None = None,
) -> tuple[int, dict[str, Any]]:
    """Select visible Google Flights rows and retry transient whole-operation failures."""

    if operation_retries <= 0:
        return await _run_live_itinerary_selection_once(
            search_url=search_url,
            project_root=project_root,
            adapter=adapter,
            browser_mode=browser_mode,
            run_id=run_id,
            timeout_seconds=timeout_seconds,
            preferred_carrier=preferred_carrier,
            require_nonstop=require_nonstop,
            row_rank=row_rank,
            outbound_row_rank=outbound_row_rank,
            return_row_rank=return_row_rank,
            outbound_match_text=outbound_match_text,
            return_match_text=return_match_text,
            reuse_target=reuse_target,
            max_tabs=max_tabs,
            allow_over_budget=allow_over_budget,
            task_trace=task_trace,
        )

    adapter = adapter or CdpAdapter(max_tabs=max_tabs, allow_over_budget=allow_over_budget)
    base_run_id = run_id or _new_run_id()
    root_trace = task_trace or new_task_trace(
        command="gflights.itinerary.select",
        name="selection-operation",
        run_id=base_run_id,
    )
    max_attempts = operation_retries + 1
    attempt_summaries: list[dict[str, Any]] = []
    last_result: tuple[int, dict[str, Any]] | None = None

    for attempt in range(1, max_attempts + 1):
        attempt_run_id = base_run_id if attempt == 1 else f"{base_run_id}-attempt-{attempt:02d}"
        exit_code, payload = await _run_live_itinerary_selection_once(
            search_url=search_url,
            project_root=project_root,
            adapter=adapter,
            browser_mode=browser_mode,
            run_id=attempt_run_id,
            timeout_seconds=timeout_seconds,
            preferred_carrier=preferred_carrier,
            require_nonstop=require_nonstop,
            row_rank=row_rank,
            outbound_row_rank=outbound_row_rank,
            return_row_rank=return_row_rank,
            outbound_match_text=outbound_match_text,
            return_match_text=return_match_text,
            reuse_target=reuse_target,
            max_tabs=max_tabs,
            allow_over_budget=allow_over_budget,
            task_trace=root_trace.child(
                f"selection-attempt-{attempt:02d}",
                run_id=attempt_run_id,
            ),
        )
        last_result = (exit_code, payload)
        transient = _is_transient_operation_failure(payload)
        attempt_summaries.append(
            {
                "attempt": attempt,
                "run_id": attempt_run_id,
                "status": payload.get("status"),
                "exit_code": exit_code,
                "transient_retryable": transient,
                "error": payload.get("error") or "",
                "failed_stage": _payload_failed_stage(payload),
            }
        )
        if exit_code == 0 or not transient or attempt == max_attempts:
            diagnostics = dict(payload.get("diagnostics") or {})
            diagnostics.update(
                {
                    "operation_attempts": attempt_summaries,
                    "operation_attempt_count": len(attempt_summaries),
                    "operation_retry_limit": operation_retries,
                    "transient_retry_exhausted": bool(
                        transient and attempt == max_attempts and exit_code != 0
                    ),
                    "failed_stage": _payload_failed_stage(payload),
                }
            )
            payload["diagnostics"] = diagnostics
            return exit_code, payload
        await asyncio.sleep(min(3.0, 0.75 * attempt))

    assert last_result is not None
    return last_result


async def _run_live_itinerary_selection_once(
    *,
    search_url: str,
    project_root: Path | None = None,
    adapter: CdpAdapter | None = None,
    browser_mode: BrowserMode = "headed",
    run_id: str | None = None,
    timeout_seconds: float = 45.0,
    preferred_carrier: str = "",
    require_nonstop: bool = False,
    row_rank: int = 1,
    outbound_row_rank: int | None = None,
    return_row_rank: int | None = None,
    outbound_match_text: str = "",
    return_match_text: str = "",
    reuse_target: str = "",
    max_tabs: int | None = None,
    allow_over_budget: bool = False,
    task_trace: TaskTrace | None = None,
) -> tuple[int, dict[str, Any]]:
    """Select visible Google Flights rows and return a Google booking URL."""

    adapter = adapter or CdpAdapter(max_tabs=max_tabs, allow_over_budget=allow_over_budget)
    state = init_app_state(project_root)
    run_id = run_id or _new_run_id()
    task_trace = task_trace or new_task_trace(
        command="gflights.itinerary.select",
        name="selection-attempt",
        run_id=run_id,
    )
    managed_tab_trace = task_trace.child("managed-tab", run_id=run_id)
    run_root = state.run_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    executed: list[dict[str, Any]] = []
    artifacts: list[str] = []
    source_surfaces: list[str] = []
    warnings: list[str] = [
        "row selection is limited to visible Google Flights rows and stops at Google booking summary"
    ]
    helper = CdpEvidenceHelper(
        adapter=adapter,
        browser_mode=browser_mode,
        default_timeout_seconds=timeout_seconds,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    _write_json(
        run_root / "selection-input.json",
        {
            "search_url": search_url,
            "preferred_carrier": preferred_carrier,
            "require_nonstop": require_nonstop,
            "row_rank": row_rank,
            "outbound_row_rank": outbound_row_rank,
            "return_row_rank": return_row_rank,
            "outbound_match_text": outbound_match_text,
            "return_match_text": return_match_text,
            "reuse_target": reuse_target,
            "max_tabs": max_tabs,
            "allow_over_budget": allow_over_budget,
        },
    )
    artifacts.append(str(run_root / "selection-input.json"))
    page_id = ""
    tab_context: dict[str, Any] = {
        "enabled": tab_budget_enabled(max_tabs=max_tabs, reuse_target=reuse_target),
        "before": None,
        "after": None,
        "managed_tab_policy": "reuse" if reuse_target else "new",
        "max_tabs": max_tabs,
        "reuse_target": reuse_target,
        "managed_tab_created": True,
        "cleanup_status": "not_run",
        "task_trace": task_trace,
        "managed_tab_trace": managed_tab_trace,
    }
    if tab_context["enabled"]:
        tab_context["before"] = await capture_tab_budget(
            adapter=adapter,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            stage="before",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
        )
    open_args, managed_tab_created = open_args_for_policy(
        url=search_url,
        managed_tab_policy="reuse" if reuse_target else "new",
        reuse_target=reuse_target,
        tab_budget_before=tab_context["before"],
    )
    tab_context["managed_tab_created"] = managed_tab_created

    async with helper.stage("open itinerary selection"):
        open_result = await helper.run(
            open_args,
            timeout_seconds=timeout_seconds,
            artifact_name="open.json",
            source_surface="cdp:open:itinerary-selection",
        )
    page_id = open_result_page_id(open_result)
    if page_id and managed_tab_created:
        managed_tab_trace.record_target(page_id)
    recovered_open_warning = recoverable_open_page_warning(open_result, page_id)
    if recovered_open_warning:
        warnings.append(recovered_open_warning)
    if _is_stop_result(open_result):
        return await _finish_selection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            result=_stop_payload(
                run_id=run_id,
                search_url=search_url,
                browser_mode=browser_mode,
                result=open_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                warnings=warnings,
            ),
        )
    if open_result.status == "tool_error" and not page_id:
        return await _finish_selection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            result=_tool_error_payload(
                run_id=run_id,
                search_url=search_url,
                browser_mode=browser_mode,
                result=open_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
            ),
        )

    async with helper.stage("outbound rows ready"):
        search_settle = await _settle(
            helper=helper,
            page_id=page_id,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            stage="outbound",
            warnings=warnings,
        )
    if search_settle["stop_result"] is not None:
        return await _finish_selection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            result=_stop_payload(
                run_id=run_id,
                search_url=search_url,
                browser_mode=browser_mode,
                result=search_settle["stop_result"],
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                warnings=warnings,
            ),
        )
    if not search_settle["ready"]:
        return await _finish_selection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            result=_stage_not_ready_payload(
                run_id=run_id,
                search_url=search_url,
                browser_mode=browser_mode,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                warnings=warnings,
                stage="outbound",
                settlement=search_settle,
                selection=None,
            ),
        )

    async with helper.stage("select outbound row"):
        outbound_selection = await _select_row(
            helper=helper,
            page_id=page_id,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            stage="outbound",
            preferred_carrier=preferred_carrier,
            require_nonstop=require_nonstop,
            row_rank=outbound_row_rank or row_rank,
            match_text=outbound_match_text,
        )
    if not outbound_selection.get("selected"):
        if outbound_selection.get("status") == "tool_error":
            return await _finish_selection(
                adapter=adapter,
                page_id=page_id,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                executed=executed,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                tab_context=tab_context,
                result=_selection_tool_error_payload(
                    run_id=run_id,
                    search_url=search_url,
                    browser_mode=browser_mode,
                    artifacts=artifacts,
                    source_surfaces=source_surfaces,
                    stage="outbound",
                    selection=outbound_selection,
                ),
            )
        return await _finish_selection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            result=_selection_unavailable_payload(
                run_id=run_id,
                search_url=search_url,
                browser_mode=browser_mode,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                warnings=warnings,
                stage="outbound",
                selection=outbound_selection,
            ),
        )

    return_selection: dict[str, Any] | None = None
    if _selection_next_stage(outbound_selection) != "booking":
        async with helper.stage("return rows ready"):
            return_settle = await _settle(
                helper=helper,
                page_id=page_id,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                stage="return",
                warnings=warnings,
            )
        if return_settle["stop_result"] is not None:
            return await _finish_selection(
                adapter=adapter,
                page_id=page_id,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                executed=executed,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                tab_context=tab_context,
                result=_stop_payload(
                    run_id=run_id,
                    search_url=search_url,
                    browser_mode=browser_mode,
                    result=return_settle["stop_result"],
                    artifacts=artifacts,
                    source_surfaces=source_surfaces,
                    warnings=warnings,
                ),
            )
        if not return_settle["ready"]:
            return await _finish_selection(
                adapter=adapter,
                page_id=page_id,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                executed=executed,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                tab_context=tab_context,
                result=_stage_not_ready_payload(
                    run_id=run_id,
                    search_url=search_url,
                    browser_mode=browser_mode,
                    artifacts=artifacts,
                    source_surfaces=source_surfaces,
                    warnings=warnings,
                    stage="return",
                    settlement=return_settle,
                    selection={"outbound": outbound_selection},
                ),
            )

        async with helper.stage("select return row"):
            return_selection = await _select_row(
                helper=helper,
                page_id=page_id,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                stage="return",
                preferred_carrier=preferred_carrier,
                require_nonstop=require_nonstop,
                row_rank=return_row_rank or row_rank,
                match_text=return_match_text,
            )
        if not return_selection.get("selected"):
            if return_selection.get("status") == "tool_error":
                return await _finish_selection(
                    adapter=adapter,
                    page_id=page_id,
                    browser_mode=browser_mode,
                    timeout_seconds=timeout_seconds,
                    run_root=run_root,
                    executed=executed,
                    artifacts=artifacts,
                    source_surfaces=source_surfaces,
                    tab_context=tab_context,
                    result=_selection_tool_error_payload(
                        run_id=run_id,
                        search_url=search_url,
                        browser_mode=browser_mode,
                        artifacts=artifacts,
                        source_surfaces=source_surfaces,
                        stage="return",
                        selection=return_selection,
                    ),
                )
            return await _finish_selection(
                adapter=adapter,
                page_id=page_id,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                executed=executed,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                tab_context=tab_context,
                result=_selection_unavailable_payload(
                    run_id=run_id,
                    search_url=search_url,
                    browser_mode=browser_mode,
                    artifacts=artifacts,
                    source_surfaces=source_surfaces,
                    warnings=warnings,
                    stage="return",
                    selection=return_selection,
                ),
            )

    async with helper.stage("booking summary ready"):
        booking_settle = await _settle(
            helper=helper,
            page_id=page_id,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            stage="booking",
            warnings=warnings,
        )
    if booking_settle["stop_result"] is not None:
        return await _finish_selection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            result=_stop_payload(
                run_id=run_id,
                search_url=search_url,
                browser_mode=browser_mode,
                result=booking_settle["stop_result"],
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                warnings=warnings,
            ),
        )
    if not booking_settle["ready"]:
        return await _finish_selection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            result=_stage_not_ready_payload(
                run_id=run_id,
                search_url=search_url,
                browser_mode=browser_mode,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                warnings=warnings,
                stage="booking",
                settlement=booking_settle,
                selection={
                    "outbound": outbound_selection,
                    "return": return_selection,
                },
                selection_details=_selection_details(
                    outbound_selection=outbound_selection,
                    return_selection=return_selection,
                    itinerary=None,
                ),
            ),
        )

    location_result = await helper.run(
        ["eval", "window.location.href", "--target", page_id],
        timeout_seconds=min(timeout_seconds, 10.0),
        artifact_name="booking-location.json",
        source_surface="cdp:eval:booking-location",
    )
    if _is_stop_result(location_result):
        return await _finish_selection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            result=_stop_payload(
                run_id=run_id,
                search_url=search_url,
                browser_mode=browser_mode,
                result=location_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                warnings=warnings,
            ),
        )
    current_url = _string_value(location_result.json_payload or {})
    booking_url = current_url if "/travel/flights/booking" in current_url else ""
    if not booking_url:
        selection_details = _selection_details(
            outbound_selection=outbound_selection,
            return_selection=return_selection,
            itinerary=None,
        )
        return await _finish_selection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            result=(
                3,
                {
                    "status": "unsupported",
                    "confidence": "weak",
                    "live_mode": True,
                    "browser_mode": browser_mode,
                    "search_url": search_url,
                    "booking_url": "",
                    "current_url": current_url,
                    "selection": {
                        "outbound": outbound_selection,
                        "return": return_selection,
                    },
                    **selection_details,
                    "unsupported": [
                        {
                            "field": "google_flights_booking_url",
                            "status": "deferred",
                            "reason": "selected rows did not lead to a Google Flights booking-summary URL",
                        }
                    ],
                    "warnings": warnings,
                    "evidence": {
                        "run_id": run_id,
                        "artifacts": artifacts,
                        "source_surfaces": source_surfaces,
                    },
                },
            ),
        )

    booking_snapshot = await helper.run(
        ["snapshot", "--target", page_id, "--limit", "160"],
        timeout_seconds=min(timeout_seconds, 20.0),
        artifact_name="booking-snapshot.json",
        source_surface="cdp:snapshot:booking-summary",
    )
    if _is_stop_result(booking_snapshot):
        return await _finish_selection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            result=_stop_payload(
                run_id=run_id,
                search_url=search_url,
                browser_mode=browser_mode,
                result=booking_snapshot,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                warnings=warnings,
            ),
        )
    itinerary = _itinerary_from_booking_snapshot(booking_snapshot)
    if booking_snapshot.status == "tool_error":
        warnings.append("booking-summary snapshot failed; returning row-selection details only")
    elif not itinerary:
        warnings.append(
            "booking-summary snapshot was captured but no structured itinerary fields parsed"
        )
    selection_details = _selection_details(
        outbound_selection=outbound_selection,
        return_selection=return_selection,
        itinerary=itinerary,
    )

    return await _finish_selection(
        adapter=adapter,
        page_id=page_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        tab_context=tab_context,
        result=(
            0,
            {
                "status": "ok",
                "confidence": "weak",
                "live_mode": True,
                "browser_mode": browser_mode,
                "search_url": search_url,
                "booking_url": booking_url,
                "selection": {
                    "outbound": outbound_selection,
                    "return": return_selection,
                },
                **selection_details,
                "unsupported": [],
                "warnings": warnings,
                "evidence": {
                    "run_id": run_id,
                    "artifacts": artifacts,
                    "source_surfaces": source_surfaces,
                },
            },
        ),
    )


async def _settle(
    *,
    helper: CdpEvidenceHelper,
    page_id: str,
    timeout_seconds: float,
    run_root: Path,
    stage: str,
    warnings: list[str],
) -> dict[str, Any]:
    started = perf_counter()
    stage_ready = await wait_until_google_flights_stage_ready(
        helper=helper,
        page_id=page_id,
        stage=stage,
        timeout_seconds=max(timeout_seconds, MINIMUM_GOOGLE_FLIGHTS_DWELL_SECONDS),
        interval_seconds=1.0,
    )
    terminal_condition = str(stage_ready.get("terminal_condition") or "unknown")
    terminal_status = str(stage_ready.get("terminal_status") or "unknown")
    terminal_result = stage_ready.get("stop_result")
    google_page_error_reload_attempts: list[dict[str, Any]] = []
    if terminal_condition == "google_page_error":
        recovery = await _recover_google_page_error(
            helper=helper,
            page_id=page_id,
            stage=stage,
            timeout_seconds=timeout_seconds,
            original_stage_ready=stage_ready,
        )
        stage_ready = recovery["stage_ready"]
        google_page_error_reload_attempts = recovery["attempts"]
        terminal_condition = str(stage_ready.get("terminal_condition") or "unknown")
        terminal_status = str(stage_ready.get("terminal_status") or "unknown")
        terminal_result = stage_ready.get("stop_result")
        if terminal_condition == "google_page_error":
            warnings.append(
                f"{stage} Google Flights page error persisted after "
                f"{len(google_page_error_reload_attempts)} reload attempt(s)"
            )
        elif google_page_error_reload_attempts:
            warnings.append(
                f"{stage} Google Flights page error recovered after "
                f"{len(google_page_error_reload_attempts)} reload attempt(s)"
            )
    if terminal_condition in TRANSIENT_STAGE_CONDITIONS:
        _write_settlement_json(
            run_root=run_root,
            artifacts=helper.artifacts,
            source_surfaces=helper.source_surfaces,
            stage=stage,
            terminal_condition=terminal_condition,
            terminal_status=terminal_status,
            dwell_seconds=perf_counter() - started,
            dwell_enforced=False,
            network_status="not_run",
            body_stability={
                "status": "google_page_error_reload_exhausted"
                if google_page_error_reload_attempts
                else "not_run",
                "google_page_error_reload_attempts": google_page_error_reload_attempts,
            },
            warnings=warnings,
        )
        return {
            "stop_result": terminal_result if isinstance(terminal_result, CdpResult) else None,
            "ready": False,
            "terminal_condition": terminal_condition,
            "terminal_status": terminal_status,
        }
    if terminal_condition in BLOCKED_STOP_STATES:
        if isinstance(terminal_result, CdpResult):
            terminal_result = _blocked_terminal_result(terminal_result, terminal_condition)
        else:
            terminal_result = None
    if isinstance(terminal_result, CdpResult) and _is_stop_result(terminal_result):
        _write_settlement_json(
            run_root=run_root,
            artifacts=helper.artifacts,
            source_surfaces=helper.source_surfaces,
            stage=stage,
            terminal_condition=terminal_condition,
            terminal_status=terminal_result.status,
            dwell_seconds=perf_counter() - started,
            dwell_enforced=False,
            network_status="not_run",
            body_stability={"status": "not_run"},
            warnings=warnings,
        )
        return {
            "stop_result": terminal_result,
            "ready": False,
            "terminal_condition": terminal_condition,
            "terminal_status": terminal_result.status,
        }

    expansion: dict[str, Any] | None = None
    if stage in {"outbound", "return"} and stage_ready.get("ready"):
        expansion = await expand_accessible_rows_until_stable(
            helper=helper,
            page_id=page_id,
            stage=stage,
            considered_limit=5,
            timeout_seconds=min(timeout_seconds, 10.0),
            interval_seconds=1.0,
        )
        if not expansion.get("ready"):
            warnings.append(f"{stage} accessible row expansion assertion timed out")

    _write_settlement_json(
        run_root=run_root,
        artifacts=helper.artifacts,
        source_surfaces=helper.source_surfaces,
        stage=stage,
        terminal_condition=terminal_condition,
        terminal_status=terminal_status,
        dwell_seconds=perf_counter() - started,
        dwell_enforced=False,
        network_status="not_used_async_stage_assertion",
        body_stability={
            "status": "stage_assertion",
            "assertion": stage_ready.get("assertion", {}),
            "expansion": expansion,
            "google_page_error_reload_attempts": google_page_error_reload_attempts,
        },
        warnings=warnings,
    )
    return {
        "stop_result": None,
        "ready": bool(stage_ready.get("ready")),
        "terminal_condition": terminal_condition,
        "terminal_status": terminal_status,
        "assertion": stage_ready.get("assertion", {}),
        "expansion": expansion,
    }


async def _recover_google_page_error(
    *,
    helper: CdpEvidenceHelper,
    page_id: str,
    stage: str,
    timeout_seconds: float,
    original_stage_ready: dict[str, Any],
) -> dict[str, Any]:
    stage_ready = original_stage_ready
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, GOOGLE_PAGE_ERROR_RELOAD_ATTEMPTS + 1):
        reload_result = await helper.eval(
            GOOGLE_PAGE_ERROR_RELOAD_JS,
            page_id=page_id,
            artifact_name=f"{stage}-google-page-error-reload-{attempt:02d}.json",
            source_surface=f"cdp:eval:{stage}:google-page-error-reload",
            timeout_seconds=min(max(timeout_seconds, 1.0), 5.0),
        )
        reload_payload = _dict_value(reload_result.json_payload or {})
        attempt_summary = {
            "attempt": attempt,
            "status": reload_result.status,
            "exit_code": reload_result.exit_code,
            "action": reload_payload.get("action", ""),
            "label": reload_payload.get("label", ""),
            "post_reload_condition": "not_checked",
            "post_reload_status": "not_checked",
        }
        attempts.append(attempt_summary)
        if reload_result.status == "tool_error":
            attempt_summary["error"] = reload_result.error
            break

        await asyncio.sleep(1.0)
        stage_ready = await wait_until_google_flights_stage_ready(
            helper=helper,
            page_id=page_id,
            stage=stage,
            timeout_seconds=min(max(timeout_seconds, MINIMUM_GOOGLE_FLIGHTS_DWELL_SECONDS), 20.0),
            interval_seconds=1.0,
            artifact_prefix=f"{stage}-post-reload-{attempt:02d}-stage-state",
            source_surface=f"cdp:assert:{stage}:post-reload-stage-state",
        )
        terminal_condition = str(stage_ready.get("terminal_condition") or "unknown")
        attempt_summary["post_reload_condition"] = terminal_condition
        attempt_summary["post_reload_status"] = str(stage_ready.get("terminal_status") or "unknown")
        if terminal_condition not in TRANSIENT_STAGE_CONDITIONS:
            break
    return {
        "stage_ready": stage_ready,
        "attempts": attempts,
    }


async def _select_row(
    *,
    helper: CdpEvidenceHelper,
    page_id: str,
    timeout_seconds: float,
    run_root: Path,
    stage: str,
    preferred_carrier: str,
    require_nonstop: bool,
    row_rank: int,
    match_text: str,
) -> dict[str, Any]:
    selection = await select_accessible_row_until_next_stage(
        helper=helper,
        page_id=page_id,
        stage=stage,
        preferred_carrier=preferred_carrier,
        require_nonstop=require_nonstop,
        row_rank=row_rank,
        match_text=match_text,
        timeout_seconds=min(timeout_seconds, 15.0),
        interval_seconds=1.0,
    )
    selection["stage"] = stage
    return selection


async def _click_selected_row(
    *,
    adapter: CdpAdapter,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    stage: str,
    page_id: str,
    marker: str,
    selection: dict[str, Any],
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
) -> CdpResult:
    point = selection.get("point")
    if isinstance(point, dict):
        x = point.get("x")
        y = point.get("y")
        if isinstance(x, int | float) and isinstance(y, int | float):
            return await _dispatch_mouse_click(
                adapter=adapter,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                stage=stage,
                page_id=page_id,
                x=float(x),
                y=float(y),
                executed=executed,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
            )

    selector = f'[data-gflights-selection="{marker}"]'
    verification_args = _click_verification_args(stage)
    click_result = await _run_step(
        adapter=adapter,
        args=[
            "click",
            selector,
            "--target",
            page_id,
            "--strategy",
            "raw-input",
            "--activate",
            *verification_args,
        ],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name=f"{stage}-click-row.json",
        source_surface=f"cdp:click:{stage}:selected-row",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if click_result.status == "tool_error" and not _should_force_retry_click(click_result):
        await asyncio.sleep(0.8)
        return await _run_step(
            adapter=adapter,
            args=[
                "click",
                selector,
                "--target",
                page_id,
                "--strategy",
                "raw-input",
                "--activate",
                *verification_args,
            ],
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            artifact_name=f"{stage}-click-row-retry-verified.json",
            source_surface=f"cdp:click:{stage}:selected-row:retry-verified",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
        )
    if not _should_force_retry_click(click_result):
        return click_result
    return await _run_step(
        adapter=adapter,
        args=[
            "click",
            selector,
            "--target",
            page_id,
            "--strategy",
            "raw-input",
            "--activate",
            "--force",
            *verification_args,
        ],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name=f"{stage}-click-row-retry-force.json",
        source_surface=f"cdp:click:{stage}:selected-row:retry-force",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )


def _click_verification_args(stage: str) -> list[str]:
    if stage == "outbound":
        return ["--wait-text", "Returning flights"]
    if stage == "return":
        return ["--wait-url-contains", "/travel/flights/booking"]
    return []


async def _dispatch_mouse_click(
    *,
    adapter: CdpAdapter,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    stage: str,
    page_id: str,
    x: float,
    y: float,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
) -> CdpResult:
    common = {"x": x, "y": y, "button": "left", "clickCount": 1}
    await _run_step(
        adapter=adapter,
        args=[
            "protocol",
            "exec",
            "Input.dispatchMouseEvent",
            "--target",
            page_id,
            "--params",
            json.dumps({"type": "mouseMoved", "x": x, "y": y}),
        ],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name=f"{stage}-mouse-moved.json",
        source_surface=f"cdp:protocol:{stage}:mouse-moved",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    press_result = await _run_step(
        adapter=adapter,
        args=[
            "protocol",
            "exec",
            "Input.dispatchMouseEvent",
            "--target",
            page_id,
            "--params",
            json.dumps({"type": "mousePressed", "buttons": 1, **common}),
        ],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name=f"{stage}-mouse-pressed.json",
        source_surface=f"cdp:protocol:{stage}:mouse-pressed",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if press_result.status == "tool_error":
        return press_result
    return await _run_step(
        adapter=adapter,
        args=[
            "protocol",
            "exec",
            "Input.dispatchMouseEvent",
            "--target",
            page_id,
            "--params",
            json.dumps({"type": "mouseReleased", "buttons": 0, **common}),
        ],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name=f"{stage}-mouse-released.json",
        source_surface=f"cdp:protocol:{stage}:mouse-released",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )


def _itinerary_from_booking_snapshot(result: CdpResult) -> dict[str, Any] | None:
    if result.status == "tool_error":
        return None
    itinerary = extract_selected_itinerary(
        {"snapshot": result.json_payload or {}},
        source_surface="selected-itinerary-visible-text",
        confidence="weak",
    )
    if itinerary.get("segments") or itinerary.get("booking_options"):
        return itinerary
    return None


def _selection_details(
    *,
    outbound_selection: dict[str, Any],
    return_selection: dict[str, Any] | None,
    itinerary: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "selected_outbound": _selected_leg_summary(
            stage="outbound",
            selection=outbound_selection,
            itinerary=itinerary,
        ),
        "selected_return": _selected_leg_summary(
            stage="return",
            selection=return_selection,
            itinerary=itinerary,
        ),
        "booking_options": (itinerary or {}).get("booking_options") if itinerary else [],
        "itinerary": itinerary,
    }


def _selected_leg_summary(
    *,
    stage: str,
    selection: dict[str, Any] | None,
    itinerary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not selection or not selection.get("selected"):
        return None

    summary = _row_selection_summary(stage, selection)
    if itinerary:
        segments = [
            segment
            for segment in itinerary.get("segments", [])
            if isinstance(segment, dict) and segment.get("direction") == stage
        ]
        layovers = [
            layover
            for layover in itinerary.get("layovers", [])
            if isinstance(layover, dict) and layover.get("direction") == stage
        ]
        if segments:
            summary["segments"] = segments
            summary["layovers"] = layovers
            summary["flight_numbers"] = [
                segment["flight_number"]
                for segment in segments
                if isinstance(segment.get("flight_number"), str)
            ]
            summary["carriers"] = sorted(
                {
                    segment["airline"]
                    for segment in segments
                    if isinstance(segment.get("airline"), str)
                }
            )
    return summary


def _row_selection_summary(stage: str, selection: dict[str, Any]) -> dict[str, Any]:
    text = str(selection.get("text") or "")
    summary_confidence = "medium"
    summary_source_surface = f"itinerary-selection-{stage}-accessible-row"
    parsed = extract_primary_results(
        {
            "accessible_rows": [
                {
                    "rank": selection.get("rowRank"),
                    "text": text,
                    "ariaLabel": selection.get("ariaLabel") or "",
                    "combinedText": selection.get("combinedText") or text,
                    "locatorStrategy": selection.get("locatorStrategy") or "",
                }
            ]
        },
        source_surface=f"itinerary-selection-{stage}-accessible-row",
        confidence="medium",
    )
    if not parsed or not parsed[0].get("carriers"):
        summary_confidence = "weak"
        summary_source_surface = f"itinerary-selection-{stage}-visible-row"
        parsed = extract_primary_results(
            {"items": [{"text": text}]},
            source_surface=summary_source_surface,
            confidence="weak",
        )
    summary: dict[str, Any] = {
        "stage": stage,
        "text": text,
        "aria_label": selection.get("ariaLabel") or "",
        "locator_strategy": selection.get("locatorStrategy") or "",
        "confidence": summary_confidence,
        "source_surface": summary_source_surface,
        "row_rank": selection.get("rowRank"),
        "match_text": selection.get("matchText") or "",
    }
    if not parsed:
        summary["parsed"] = False
        return summary

    row = parsed[0]
    summary.update(
        {
            "parsed": True,
            "carrier": row["carriers"][0] if row.get("carriers") else None,
            "carriers": row.get("carriers", []),
            "depart": row["departure_times"][0] if row.get("departure_times") else None,
            "arrive": row["arrival_times"][0] if row.get("arrival_times") else None,
            "origin_airports": row.get("origin_airports", []),
            "destination_airports": row.get("destination_airports", []),
            "duration_minutes": row.get("duration_minutes"),
            "duration_text": row.get("duration_text"),
            "stops": row.get("stops"),
            "layovers": row.get("layovers", []),
            "price": row.get("price"),
            "baggage_summary": row.get("baggage_summary"),
            "cabin": row.get("cabin"),
            "cabin_facilities": row.get("cabin_facilities", []),
        }
    )
    return summary


async def _run_step(
    *,
    adapter: CdpAdapter,
    args: list[str],
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    artifact_name: str,
    source_surface: str,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
) -> CdpResult:
    result = await adapter.run_json(
        args,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
    )
    artifact_path = run_root / artifact_name
    _write_json(artifact_path, _result_artifact(result))
    artifacts.append(str(artifact_path))
    source_surfaces.append(source_surface)
    executed.append(
        {
            "args": args,
            "browser_mode": browser_mode,
            "timeout_seconds": timeout_seconds,
            "artifact": str(artifact_path),
            "status": result.status,
            "exit_code": result.exit_code,
            "attempt_count": result.attempt_count,
            "max_attempts": result.max_attempts,
        }
    )
    return result


async def _finish_selection(
    *,
    adapter: CdpAdapter,
    page_id: str,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
    tab_context: dict[str, Any] | None = None,
    result: tuple[int, dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    cleanup_status = "not_run"
    managed_tab_trace = tab_context.get("managed_tab_trace") if tab_context is not None else None
    task_trace = tab_context.get("task_trace") if tab_context is not None else None
    should_close = tab_context is None or bool(tab_context.get("managed_tab_created", True))
    if should_close and isinstance(managed_tab_trace, TaskTrace) and page_id:
        should_close = managed_tab_trace.owns_target(page_id)
    if should_close:
        close_result = await close_managed_page(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            warnings=_payload_warnings(result[1]),
            task_trace=managed_tab_trace if isinstance(managed_tab_trace, TaskTrace) else None,
        )
        cleanup_status = close_result.status if close_result is not None else "not_run_no_page_id"
    elif tab_context is not None and bool(tab_context.get("managed_tab_created", True)) and page_id:
        cleanup_status = "skipped_unowned_tab"
    elif tab_context is not None:
        cleanup_status = "skipped_reused_tab"

    if tab_context is not None:
        tab_context["cleanup_status"] = cleanup_status
        if tab_context.get("enabled"):
            tab_context["after"] = await capture_tab_budget(
                adapter=adapter,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                stage="after",
                executed=executed,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
            )
            result[1]["managed_tab_id"] = page_id or None
            result[1]["tab_budget"] = tab_budget_summary(
                before=tab_context.get("before"),
                after=tab_context.get("after"),
                managed_tab_policy=str(tab_context.get("managed_tab_policy") or "new"),
                max_tabs=tab_context.get("max_tabs"),
                reuse_target=str(tab_context.get("reuse_target") or ""),
                managed_tab_id=page_id,
                managed_tab_created=bool(tab_context.get("managed_tab_created", True)),
                cleanup_status=cleanup_status,
            )
        if isinstance(task_trace, TaskTrace):
            _write_task_trace(
                run_root=run_root,
                artifacts=artifacts,
                task_trace=task_trace,
                managed_tab_trace=managed_tab_trace
                if isinstance(managed_tab_trace, TaskTrace)
                else None,
                page_id=page_id,
                managed_tab_created=bool(tab_context.get("managed_tab_created", True)),
                cleanup_status=cleanup_status,
            )
            evidence = result[1].get("evidence")
            if isinstance(evidence, dict):
                evidence["task_trace"] = task_trace.as_dict()
    _write_command_log(run_root, executed, artifacts)
    return result


def _stop_payload(
    *,
    run_id: str,
    search_url: str,
    browser_mode: BrowserMode,
    result: CdpResult,
    artifacts: list[str],
    source_surfaces: list[str],
    warnings: list[str],
) -> tuple[int, dict[str, Any]]:
    payload: dict[str, Any] = {
        "status": (
            result.status
            if result.status in BLOCKED_STOP_STATES or result.status == "tool_error"
            else "blocked"
        ),
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "search_url": search_url,
        "booking_url": "",
        "stop_state": result.stop_state or result.status,
        "selection": None,
        "unsupported": [],
        "warnings": [
            *warnings,
            (
                result.error
                if result.status == "tool_error" and result.error
                else "live itinerary selection stopped before crossing a provider, login, payment, personal-data, or access-control boundary"
            ),
        ],
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }
    if result.fallback:
        payload["fallback"] = result.fallback
    if result.status == "tool_error" and result.error:
        payload["error"] = result.error
    return result.exit_code or 4, payload


def _tool_error_payload(
    *,
    run_id: str,
    search_url: str,
    browser_mode: BrowserMode,
    result: CdpResult,
    artifacts: list[str],
    source_surfaces: list[str],
) -> tuple[int, dict[str, Any]]:
    return 6, {
        "status": "tool_error",
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "search_url": search_url,
        "booking_url": "",
        "selection": None,
        "unsupported": [],
        "warnings": [],
        "error": result.error,
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }


def _selection_tool_error_payload(
    *,
    run_id: str,
    search_url: str,
    browser_mode: BrowserMode,
    artifacts: list[str],
    source_surfaces: list[str],
    stage: str,
    selection: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    error = str(
        selection.get("error") or selection.get("reason") or f"{stage} row selection failed"
    )
    return 6, {
        "status": "tool_error",
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "search_url": search_url,
        "booking_url": "",
        "selection": {stage: selection},
        "unsupported": [],
        "warnings": [],
        "error": error,
        "diagnostics": {
            "failed_stage": stage,
        },
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }


def _is_transient_operation_failure(payload: dict[str, Any]) -> bool:
    settlement = payload.get("settlement")
    if (
        payload.get("status") in {"tool_error", "unsupported"}
        and isinstance(settlement, dict)
        and settlement.get("terminal_condition") == "not_ready"
        and settlement.get("terminal_status") == "assertion_timeout"
    ):
        return True
    if payload.get("status") == "unsupported" and _payload_has_row_click_transition_timeout(
        payload
    ):
        return True
    if payload.get("status") != "tool_error":
        return False
    text = " ".join(
        str(value)
        for value in [
            payload.get("error"),
            payload.get("message"),
            payload.get("stop_state"),
            json.dumps(payload.get("warnings") or []),
            json.dumps(payload.get("diagnostics") or {}),
            json.dumps(payload.get("evidence") or {}),
        ]
        if value
    ).casefold()
    return any(
        marker in text
        for marker in [
            "failed to read json message",
            "failed to get reader",
            "cdp command timed out",
            "use of closed network connection",
            "connection refused",
            "browser_dial_failed",
            "browser commands require a running",
            "keepalive repair is locked",
            "starting_daemon",
            "target_not_found",
            "no target",
            "google_page_error",
            "google flights page reported a transient error",
        ]
    )


def _payload_has_row_click_transition_timeout(payload: dict[str, Any]) -> bool:
    selection = payload.get("selection")
    if not isinstance(selection, dict):
        return False
    for value in selection.values():
        if isinstance(value, dict) and _row_click_transition_timed_out(value):
            return True
    return False


def _payload_failed_stage(payload: dict[str, Any]) -> str:
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, dict) and diagnostics.get("failed_stage"):
        return str(diagnostics["failed_stage"])

    settlement = payload.get("settlement")
    if isinstance(settlement, dict) and settlement.get("stage"):
        return str(settlement["stage"])

    evidence = payload.get("evidence")
    source_surfaces = evidence.get("source_surfaces") if isinstance(evidence, dict) else None
    if isinstance(source_surfaces, list):
        for surface in reversed(source_surfaces):
            surface_text = str(surface).casefold()
            for stage in ["booking", "return", "outbound", "open"]:
                if stage in surface_text:
                    return stage
    return "unknown"


def _selection_unavailable_payload(
    *,
    run_id: str,
    search_url: str,
    browser_mode: BrowserMode,
    artifacts: list[str],
    source_surfaces: list[str],
    warnings: list[str],
    stage: str,
    selection: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    reason = _selection_unavailable_reason(stage, selection)
    diagnostics: dict[str, Any] = {
        "failed_stage": stage,
        "row_found": bool(selection.get("found")),
        "row_clicked": bool(selection.get("clicked")),
    }
    transition_condition = selection.get("transitionCondition")
    if transition_condition:
        diagnostics["transition_condition"] = transition_condition
        diagnostics["transition_matched"] = bool(selection.get("transitionMatched"))
        diagnostics["transition_elapsed_ms"] = selection.get("transitionElapsedMs")
    if _row_click_transition_timed_out(selection):
        diagnostics["transient_retryable"] = True
    return 3, {
        "status": "unsupported",
        "confidence": "weak",
        "live_mode": True,
        "browser_mode": browser_mode,
        "search_url": search_url,
        "booking_url": "",
        "selection": {stage: selection},
        "unsupported": [
            {
                "field": f"google_flights_row_selection.{stage}",
                "status": "deferred",
                "reason": reason,
            }
        ],
        "diagnostics": diagnostics,
        "warnings": warnings,
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }


def _selection_unavailable_reason(stage: str, selection: dict[str, Any]) -> str:
    if _row_click_transition_timed_out(selection):
        expected_stage = "booking" if stage == "return" else "return or booking"
        return (
            "Google Flights accepted the row click but did not reach the expected "
            f"{expected_stage} stage before the bounded semantic wait timed out"
        )
    reason = selection.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    if selection.get("found") is False:
        return "no visible Google Flights row matched the requested selection criteria"
    return "Google Flights row selection did not produce a supported next-stage transition"


def _row_click_transition_timed_out(selection: dict[str, Any]) -> bool:
    return (
        bool(selection.get("found"))
        and bool(selection.get("clicked"))
        and selection.get("transitionMatched") is False
        and selection.get("transitionCondition") == "assertion_timeout"
    )


def _selection_next_stage(selection: dict[str, Any]) -> str:
    value = selection.get("nextStage")
    return value if isinstance(value, str) else ""


def _stage_not_ready_payload(
    *,
    run_id: str,
    search_url: str,
    browser_mode: BrowserMode,
    artifacts: list[str],
    source_surfaces: list[str],
    warnings: list[str],
    stage: str,
    settlement: dict[str, Any],
    selection: dict[str, Any] | None,
    selection_details: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    terminal_condition = str(settlement.get("terminal_condition") or "unknown")
    payload: dict[str, Any] = {
        "status": "unsupported",
        "confidence": "weak",
        "live_mode": True,
        "browser_mode": browser_mode,
        "search_url": search_url,
        "booking_url": "",
        "selection": selection,
        "settlement": {
            "stage": stage,
            "ready": False,
            "terminal_condition": terminal_condition,
            "terminal_status": str(settlement.get("terminal_status") or "unknown"),
        },
        "unsupported": [
            {
                "field": f"google_flights_stage.{stage}",
                "status": "deferred",
                "reason": (
                    "Google Flights did not reach the expected stage before row selection "
                    f"(terminal condition: {terminal_condition})"
                ),
            }
        ],
        "warnings": warnings,
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }
    if selection_details:
        payload.update(selection_details)
    return 3, payload


def _write_settlement_json(
    *,
    run_root: Path,
    artifacts: list[str],
    source_surfaces: list[str],
    stage: str,
    terminal_condition: str,
    terminal_status: str,
    dwell_seconds: float,
    dwell_enforced: bool,
    network_status: str,
    body_stability: dict[str, Any],
    warnings: list[str],
) -> None:
    path = run_root / f"{stage}-settlement.json"
    _write_json(
        path,
        {
            "stage": stage,
            "terminal_condition": terminal_condition,
            "terminal_status": terminal_status,
            "minimum_dwell_seconds": MINIMUM_GOOGLE_FLIGHTS_DWELL_SECONDS,
            "dwell_seconds": round(dwell_seconds, 3),
            "dwell_enforced": dwell_enforced,
            "body_stability": body_stability,
            "network_status": network_status,
            "warnings": warnings,
        },
    )
    artifacts.append(str(path))
    source_surfaces.append(f"gflights:settlement:{stage}")


def _body_stability(first: CdpResult, second: CdpResult) -> dict[str, Any]:
    first_text = _visible_text(first.json_payload or {})
    second_text = _visible_text(second.json_payload or {})
    if first.status == "tool_error" or second.status == "tool_error":
        status = "unavailable"
    elif first_text == second_text:
        status = "stable"
    else:
        status = "changed"
    return {
        "status": status,
        "sample_count": 2,
        "first_text_length": len(first_text),
        "second_text_length": len(second_text),
    }


def _visible_text(payload: dict[str, Any]) -> str:
    items = payload.get("items")
    if not isinstance(items, list):
        text_payload = payload.get("text")
        if isinstance(text_payload, dict):
            items = text_payload.get("items")
    if not isinstance(items, list):
        return ""
    return " ".join(
        str(item.get("text") or "") for item in items if isinstance(item, dict) and item.get("text")
    )


def _string_value(payload: dict[str, Any]) -> str:
    for key in ("value", "result"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and isinstance(value.get("value"), str):
            return value["value"]
        if isinstance(value, dict) and isinstance(value.get("result"), dict):
            nested = value["result"].get("value")
            if isinstance(nested, str):
                return nested
    wait = payload.get("wait")
    if isinstance(wait, dict):
        value = wait.get("value") or wait.get("result")
        if isinstance(value, str):
            return value
    return ""


def _dict_value(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("value", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            if isinstance(value.get("value"), dict):
                return value["value"]
            if isinstance(value.get("result"), dict):
                nested = value["result"].get("value")
                if isinstance(nested, dict):
                    return nested
                return value["result"]
            return value
    return {}


def _terminal_condition_from_wait(result: CdpResult) -> str:
    payload = result.json_payload or {}
    for key in ("value", "result"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and isinstance(value.get("value"), str):
            return value["value"]
        if isinstance(value, dict) and isinstance(value.get("result"), dict):
            nested = value["result"].get("value")
            if isinstance(nested, str):
                return nested
    wait = payload.get("wait")
    if isinstance(wait, dict):
        value = wait.get("value") or wait.get("result")
        if isinstance(value, str):
            return value
        evidence = wait.get("evidence")
        if isinstance(evidence, dict) and isinstance(evidence.get("value"), str):
            return evidence["value"]
    return "unknown"


def _blocked_terminal_result(result: CdpResult, terminal_condition: str) -> CdpResult:
    return replace(
        result,
        status=terminal_condition,
        exit_code=4,
        stop_state=terminal_condition,
        fallback={
            "recommended_browser_mode": "headed",
            "reason": "headless blocked or human confirmation required",
        }
        if result.browser_mode == "headless"
        else None,
    )


def _is_stop_result(result: CdpResult) -> bool:
    return bool(result.fallback) or result.status in BLOCKED_STOP_STATES


def _should_force_retry_click(result: CdpResult) -> bool:
    if result.status != "tool_error":
        return False
    haystack = " ".join(
        str(part or "")
        for part in [
            result.error,
            (result.json_payload or {}).get("code"),
            (result.json_payload or {}).get("message"),
            json.dumps((result.json_payload or {}).get("data") or {}),
        ]
    ).casefold()
    return "actionability" in haystack and ("stable" in haystack or "receives_events" in haystack)


def _should_enforce_live_dwell(adapter: CdpAdapter) -> bool:
    return type(adapter) is CdpAdapter and getattr(adapter, "_runner", None) is run_subprocess


def _payload_warnings(payload: dict[str, Any]) -> list[str] | None:
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        return warnings
    return None


def _result_artifact(result: CdpResult) -> dict[str, Any]:
    return {
        "argv": result.argv,
        "browser_mode": result.browser_mode,
        "returncode": result.returncode,
        "status": result.status,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "json_payload": result.json_payload,
        "stop_state": result.stop_state,
        "fallback": result.fallback,
        "error": result.error,
        "timeout": result.timeout,
        "attempt_count": result.attempt_count,
        "max_attempts": result.max_attempts,
        "attempts": result.attempts or [],
    }


def _write_command_log(
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
) -> None:
    path = run_root / "command-log.json"
    _write_json(path, executed)
    if str(path) not in artifacts:
        artifacts.append(str(path))


def _write_task_trace(
    *,
    run_root: Path,
    artifacts: list[str],
    task_trace: TaskTrace,
    managed_tab_trace: TaskTrace | None,
    page_id: str,
    managed_tab_created: bool,
    cleanup_status: str,
) -> None:
    artifact_path = run_root / "task-trace.json"
    payload: dict[str, Any] = {
        "task": task_trace.as_dict(),
        "managed_tab_task": managed_tab_trace.as_dict() if managed_tab_trace is not None else None,
        "managed_tab_id": page_id or None,
        "managed_tab_created": managed_tab_created,
        "cleanup_status": cleanup_status,
        "target_task_ids": task_trace.ownership_map(),
    }
    _write_json(artifact_path, payload)
    if str(artifact_path) not in artifacts:
        artifacts.append(str(artifact_path))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    return f"gf-itinerary-select-{timestamp}"
