"""Google Flights domain assertions built on generic CDP evidence helpers."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from time import perf_counter
from typing import Any

from gflights.accessible_rows import (
    accessible_row_expand_js,
    accessible_row_prepare_selection_js,
    accessible_selected_row_click_js,
    google_flights_stage_state_js,
)
from gflights.browser import BLOCKED_STOP_STATES, CdpResult
from gflights.cdp_evidence import (
    CdpAssertionTimeout,
    CdpEvidenceHelper,
    eval_dict,
    serializable_poll_outcome,
    write_json,
)

READY_STAGE_CONDITIONS = {"fare_rows", "booking_summary"}
TRANSIENT_STAGE_CONDITIONS = {"google_page_error"}


async def wait_until_google_flights_stage_ready(
    *,
    helper: CdpEvidenceHelper,
    page_id: str,
    stage: str,
    timeout_seconds: float,
    interval_seconds: float = 1.0,
) -> dict[str, Any]:
    """Wait for semantic Google Flights state instead of a blind load/network wait."""

    try:
        outcome = await helper.assert_eval(
            google_flights_stage_state_js(stage=stage, limit=20),
            page_id=page_id,
            assertion_name=f"google-flights-{stage}-stage-ready",
            artifact_prefix=f"{stage}-stage-state",
            source_surface=f"cdp:assert:{stage}:stage-state",
            ready=_stage_state_is_terminal,
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
            per_attempt_timeout_seconds=min(timeout_seconds, 3.0),
        )
    except CdpAssertionTimeout as exc:
        state = exc.outcome.get("value") if isinstance(exc.outcome, dict) else {}
        return {
            "ready": False,
            "terminal_condition": _terminal_condition(state),
            "terminal_status": "assertion_timeout",
            "assertion": exc.outcome,
            "stop_result": None,
        }

    state = outcome.get("value") if isinstance(outcome.get("value"), dict) else {}
    terminal_condition = _terminal_condition(state)
    result = outcome.get("result")
    if terminal_condition in BLOCKED_STOP_STATES and isinstance(result, CdpResult):
        return {
            "ready": False,
            "terminal_condition": terminal_condition,
            "terminal_status": terminal_condition,
            "assertion": serializable_poll_outcome(outcome),
            "stop_result": result,
        }
    if terminal_condition in TRANSIENT_STAGE_CONDITIONS and isinstance(result, CdpResult):
        return {
            "ready": False,
            "terminal_condition": terminal_condition,
            "terminal_status": "tool_error",
            "assertion": serializable_poll_outcome(outcome),
            "stop_result": _transient_stage_result(result, terminal_condition),
        }
    return {
        "ready": _stage_condition_is_ready(stage, terminal_condition),
        "terminal_condition": terminal_condition,
        "terminal_status": result.status if isinstance(result, CdpResult) else "unknown",
        "assertion": serializable_poll_outcome(outcome),
        "stop_result": None,
    }


async def expand_accessible_rows_until_stable(
    *,
    helper: CdpEvidenceHelper,
    page_id: str,
    stage: str,
    considered_limit: int,
    timeout_seconds: float,
    interval_seconds: float = 1.0,
) -> dict[str, Any]:
    """Expand visible top flight rows and wait until expansion evidence is stable."""

    try:
        outcome = await helper.assert_eval(
            accessible_row_expand_js(stage=stage, limit=considered_limit),
            page_id=page_id,
            assertion_name=f"google-flights-{stage}-rows-expanded",
            artifact_prefix=f"{stage}-row-expand",
            source_surface=f"cdp:assert:{stage}:row-expand",
            ready=_expanded_rows_are_stable,
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
            per_attempt_timeout_seconds=min(timeout_seconds, 3.0),
        )
    except CdpAssertionTimeout as exc:
        return {
            "ready": False,
            "status": "assertion_timeout",
            "assertion": exc.outcome,
        }
    return {
        "ready": True,
        "status": "ok",
        "assertion": serializable_poll_outcome(outcome),
    }


async def select_accessible_row_until_next_stage(
    *,
    helper: CdpEvidenceHelper,
    page_id: str,
    stage: str,
    preferred_carrier: str,
    require_nonstop: bool,
    row_rank: int,
    match_text: str,
    timeout_seconds: float,
    interval_seconds: float = 1.0,
) -> dict[str, Any]:
    """Mark one ARIA row, click it repeatedly, and assert the next semantic stage."""

    marker = f"gflights-{stage}-choice"
    prepare_result = await helper.eval(
        accessible_row_prepare_selection_js(
            stage=stage,
            marker=marker,
            preferred_carrier=preferred_carrier,
            require_nonstop=require_nonstop,
            row_rank=row_rank,
            match_text=match_text,
        ),
        page_id=page_id,
        artifact_name=f"{stage}-select-row-prepare.json",
        source_surface=f"cdp:eval:{stage}:select-row-prepare",
        timeout_seconds=min(timeout_seconds, 5.0),
    )
    selection = eval_dict(prepare_result.json_payload or {})
    if prepare_result.status == "tool_error":
        return {
            "status": "tool_error",
            "selected": False,
            "stage": stage,
            "reason": prepare_result.error or "row preparation eval failed",
            "error": prepare_result.error or "row preparation eval failed",
        }
    if not selection.get("found"):
        selection.setdefault("selected", False)
        selection.setdefault("stage", stage)
        return selection

    started = perf_counter()
    attempt = 0
    click_attempts: list[dict[str, Any]] = []
    next_stage = "return" if stage == "outbound" else "booking"
    while perf_counter() - started < timeout_seconds:
        attempt += 1
        click_result = await helper.eval(
            accessible_selected_row_click_js(marker=marker),
            page_id=page_id,
            artifact_name=f"{stage}-click-selected-row-{attempt:02d}.json",
            source_surface=f"cdp:eval:{stage}:click-selected-row:attempt",
            timeout_seconds=min(7.0, timeout_seconds),
        )
        click_payload = eval_dict(click_result.json_payload or {})
        click_attempts.append(
            {
                "attempt": attempt,
                "status": click_result.status,
                "clicked": click_payload.get("clicked"),
                "reason": click_payload.get("reason", ""),
            }
        )
        if click_result.status == "tool_error":
            selection.update(
                {
                    "status": "tool_error",
                    "selected": False,
                    "stage": stage,
                    "reason": click_result.error or f"{stage} row click failed",
                    "error": click_result.error or f"{stage} row click failed",
                    "clickAttemptEvidence": click_attempts,
                }
            )
            return selection
        state_result = await helper.eval(
            google_flights_stage_state_js(stage=next_stage, limit=20),
            page_id=page_id,
            artifact_name=f"{stage}-post-click-stage-{attempt:02d}.json",
            source_surface=f"cdp:eval:{stage}:post-click-stage",
            timeout_seconds=min(3.0, timeout_seconds),
        )
        if state_result.status == "tool_error":
            selection.update(
                {
                    "status": "tool_error",
                    "selected": False,
                    "stage": stage,
                    "reason": state_result.error or f"{stage} row click transition check failed",
                    "error": state_result.error or f"{stage} row click transition check failed",
                    "clickAttemptEvidence": click_attempts,
                }
            )
            return selection
        state = eval_dict(state_result.json_payload or {})
        if _stage_state_is_ready_for(state, next_stage):
            selection.update(
                {
                    "selected": True,
                    "clicked": True,
                    "stage": stage,
                    "clickAttempts": attempt,
                    "clickDispatch": "dom-link-click",
                    "transitionMatched": True,
                    "transitionCondition": _terminal_condition(state),
                    "transitionElapsedMs": round((perf_counter() - started) * 1000),
                    "nextStageState": _compact_stage_state(state),
                    "clickAttemptEvidence": click_attempts,
                }
            )
            return selection
        sleep_for = min(interval_seconds, timeout_seconds - (perf_counter() - started))
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)

    selection.update(
        {
            "selected": False,
            "clicked": any(attempt.get("clicked") for attempt in click_attempts),
            "stage": stage,
            "clickAttempts": attempt,
            "transitionMatched": False,
            "transitionCondition": "assertion_timeout",
            "transitionElapsedMs": round((perf_counter() - started) * 1000),
            "reason": (
                f"row click did not reach expected {next_stage} stage within {timeout_seconds:g}s"
            ),
            "clickAttemptEvidence": click_attempts,
        }
    )
    assertion_path = helper.run_root / f"{stage}-select-row-assertion-timeout.json"
    write_json(
        assertion_path,
        {
            "assertion": f"google-flights-{stage}-row-click-reaches-{next_stage}",
            "status": "assertion_timeout",
            "timeout_seconds": timeout_seconds,
            "click_attempts": click_attempts,
            "selection": selection,
        },
    )
    helper.artifacts.append(str(assertion_path))
    helper.source_surfaces.append(f"cdp:assert:{stage}:row-click-transition")
    return selection


def _stage_state_is_terminal(value: Any, _result: CdpResult) -> bool:
    if not isinstance(value, dict):
        return False
    terminal_condition = _terminal_condition(value)
    requested_stage = str(value.get("requestedStage") or "")
    if terminal_condition in BLOCKED_STOP_STATES or terminal_condition in TRANSIENT_STAGE_CONDITIONS:
        return True
    if terminal_condition == "no_results":
        return True
    return _stage_condition_is_ready(requested_stage, terminal_condition)


def _stage_state_is_ready_for(state: dict[str, Any], stage: str) -> bool:
    terminal_condition = _terminal_condition(state)
    if stage == "booking":
        return terminal_condition in {"booking_summary", "booking_url"}
    return terminal_condition == "fare_rows" and int(state.get("rowCount") or 0) > 0


def _stage_condition_is_ready(stage: str, terminal_condition: str) -> bool:
    if stage == "booking":
        return terminal_condition == "booking_summary"
    return terminal_condition == "fare_rows"


def _expanded_rows_are_stable(value: Any, _result: CdpResult) -> bool:
    if not isinstance(value, dict):
        return False
    records = value.get("records")
    if not isinstance(records, list) or not records:
        return False
    considered = int(value.get("consideredRankCount") or 0)
    if considered <= 0:
        return False
    for record in records:
        if not isinstance(record, dict):
            return False
        if record.get("expandedAfter") == "false":
            return False
    return True


def _terminal_condition(state: Any) -> str:
    if isinstance(state, dict):
        value = state.get("terminalCondition")
        if isinstance(value, str) and value:
            return value
        if value is False:
            return "not_ready"
    return "unknown"


def _transient_stage_result(result: CdpResult, terminal_condition: str) -> CdpResult:
    reason = (
        "Google Flights page reported a transient error and offered Reload"
        if terminal_condition == "google_page_error"
        else f"Google Flights page reported transient condition: {terminal_condition}"
    )
    return replace(
        result,
        status="tool_error",
        exit_code=6,
        stop_state=terminal_condition,
        error=reason,
    )


def _compact_stage_state(state: dict[str, Any]) -> dict[str, Any]:
    rows = state.get("rows")
    return {
        "requestedStage": state.get("requestedStage"),
        "terminalCondition": state.get("terminalCondition"),
        "rowCount": state.get("rowCount"),
        "currentUrl": state.get("currentUrl"),
        "currentTitle": state.get("currentTitle"),
        "rows": rows[:3] if isinstance(rows, list) else [],
    }
