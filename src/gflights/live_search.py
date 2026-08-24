"""Default live Google Flights evidence orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlencode

from gflights.accessible_rows import accessible_row_expand_js, accessible_rows_js
from gflights.app_state import (
    DEFAULT_FLIGHT_SEARCH_TAB_BUDGET,
    AppState,
    init_app_state,
    price_cache_for_state,
)
from gflights.browser import (
    BLOCKED_STOP_STATES,
    BrowserMode,
    CdpAdapter,
    CdpResult,
    run_subprocess,
)
from gflights.domain import SearchIntent
from gflights.live_cleanup import close_managed_page
from gflights.live_form import (
    UnsupportedLiveForm,
    plan_live_form_interaction,
    validate_live_form_support,
)
from gflights.live_tabs import (
    capture_tab_budget,
    open_args_for_policy,
    tab_budget_enabled,
    tab_budget_summary,
)
from gflights.live_tasks import AsyncCrawlTaskManager
from gflights.live_trace import (
    TaskTrace,
    new_task_trace,
    open_result_page_id,
    recoverable_open_page_warning,
)
from gflights.query_state import UnsupportedQueryState, build_query_state
from gflights.ranking import rank_observed_results
from gflights.result_extraction import classify_primary_result_absence, extract_primary_results
from gflights.services import load_intents

GOOGLE_FLIGHTS_URL = "https://www.google.com/travel/flights"
GOOGLE_FLIGHTS_SEARCH_URL = "https://www.google.com/travel/flights/search"
MINIMUM_GOOGLE_FLIGHTS_DWELL_SECONDS = 10.0
READY_TERMINAL_CONDITIONS = {"fare_rows", "booking_summary"}
TRANSIENT_TERMINAL_CONDITIONS = {"google_page_error"}
TERMINAL_DOM_CONDITION_JS = r"""
(() => {
  const text = (document.body && (document.body.innerText || document.body.textContent) || "")
    .replace(/\s+/g, " ")
    .trim();
  const lower = text.toLowerCase();
  if (!text) return false;
  if (lower.includes("unusual traffic") || lower.includes("access denied")) return "blocked";
  if (lower.includes("sign in") && lower.includes("google")) return "login_required";
  if (lower.includes("oops, something went wrong") || (lower.includes("no results returned") && /\breload\b/.test(lower))) return "google_page_error";
  if (/no (matching )?flights|no results/.test(lower)) return "no_results";
  if (lower.includes("booking options") && lower.includes("book with")) return "booking_summary";
  const hasPrice = /(?:DKK|EUR|USD|INR|NOK|SEK|GBP|₹|€|\$)\s*[0-9][0-9,.]*(?:\s+round trip)?/i.test(text);
  const hasFlightContext = /(round trip|nonstop|[0-9]+\s+stop|search results|departing flights|returning flights)/i.test(text);
  if (hasPrice && hasFlightContext) return "fare_rows";
  return false;
})()
""".strip()


async def run_live_search(
    *,
    input_json: Path,
    project_root: Path | None = None,
    adapter: CdpAdapter | None = None,
    browser_mode: BrowserMode = "headed",
    run_id: str | None = None,
    timeout_seconds: float = 30.0,
    interact_with_form: bool = False,
    batch_concurrency: int = 3,
    managed_tab_policy: str = "new",
    max_tabs: int | None = None,
    rank_objectives: list[str] | None = None,
    top_k: int = 0,
    allow_transit: list[str] | None = None,
    deny_transit: list[str] | None = None,
    task_trace: TaskTrace | None = None,
) -> tuple[int, dict[str, Any] | list[dict[str, Any]]]:
    adapter = adapter or CdpAdapter(max_tabs=max_tabs)
    intents = load_intents(input_json)
    state = init_app_state(project_root)
    if len(intents) == 1:
        return await _run_one_live_search(
            intent=intents[0],
            state=state,
            adapter=adapter,
            browser_mode=browser_mode,
            run_id=run_id,
            timeout_seconds=timeout_seconds,
            interact_with_form=interact_with_form,
            managed_tab_policy=managed_tab_policy,
            max_tabs=max_tabs,
            rank_objectives=rank_objectives,
            top_k=top_k,
            allow_transit=allow_transit,
            deny_transit=deny_transit,
            task_trace=task_trace,
        )

    concurrency = max(1, min(batch_concurrency, DEFAULT_FLIGHT_SEARCH_TAB_BUDGET))
    batch_trace = task_trace or new_task_trace(
        command="gflights.search", name="search-batch", run_id=run_id or "auto"
    )
    if type(adapter) is CdpAdapter and concurrency > 1:
        manager = AsyncCrawlTaskManager(root_trace=batch_trace, concurrency=concurrency)

        async def run_indexed(
            index: int, intent: SearchIntent, child_trace: TaskTrace
        ) -> tuple[int, dict[str, Any]]:
            item_run_id = _batch_run_id(run_id, intent.query_id, index)
            return await _run_one_live_search(
                intent=intent,
                state=state,
                adapter=adapter,
                browser_mode=browser_mode,
                run_id=item_run_id,
                timeout_seconds=timeout_seconds,
                interact_with_form=interact_with_form,
                managed_tab_policy=managed_tab_policy,
                max_tabs=max_tabs,
                rank_objectives=rank_objectives,
                top_k=top_k,
                allow_transit=allow_transit,
                deny_transit=deny_transit,
                task_trace=child_trace,
            )

        indexed_results = await manager.map_ordered(
            intents,
            task_name="search-intent",
            run_id_for_item=lambda index, intent: _batch_run_id(run_id, intent.query_id, index),
            worker=run_indexed,
        )
        exit_codes = [exit_code for exit_code, _payload in (item.value for item in indexed_results)]
        outputs = [payload for _exit_code, payload in (item.value for item in indexed_results)]
        return _aggregate_exit_code(exit_codes), outputs

    outputs: list[dict[str, Any]] = []
    exit_codes: list[int] = []
    for index, intent in enumerate(intents, start=1):
        item_run_id = _batch_run_id(run_id, intent.query_id, index)
        exit_code, payload = await _run_one_live_search(
            intent=intent,
            state=state,
            adapter=adapter,
            browser_mode=browser_mode,
            run_id=item_run_id,
            timeout_seconds=timeout_seconds,
            interact_with_form=interact_with_form,
            managed_tab_policy=managed_tab_policy,
            max_tabs=max_tabs,
            rank_objectives=rank_objectives,
            top_k=top_k,
            allow_transit=allow_transit,
            deny_transit=deny_transit,
            task_trace=batch_trace.child(f"search-intent-{index:02d}", run_id=item_run_id),
        )
        exit_codes.append(exit_code)
        outputs.append(payload)
    return _aggregate_exit_code(exit_codes), outputs


async def _run_one_live_search(
    *,
    intent: SearchIntent,
    state: AppState,
    adapter: CdpAdapter,
    browser_mode: BrowserMode,
    run_id: str | None,
    timeout_seconds: float,
    interact_with_form: bool,
    managed_tab_policy: str,
    max_tabs: int | None,
    rank_objectives: list[str] | None,
    top_k: int,
    allow_transit: list[str] | None,
    deny_transit: list[str] | None,
    task_trace: TaskTrace | None = None,
) -> tuple[int, dict[str, Any]]:
    run_id = run_id or _new_run_id(intent.query_id)
    task_trace = task_trace or new_task_trace(
        command="gflights.search",
        name="search-intent",
        run_id=run_id,
    )
    managed_tab_trace = task_trace.child("managed-tab", run_id=run_id)
    run_root = state.run_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    _write_json(run_root / "intent.json", intent.model_dump(mode="json"))

    executed: list[dict[str, Any]] = []
    artifacts: list[str] = [str(run_root / "intent.json")]
    source_surfaces: list[str] = []
    query_population = _build_query_population(intent, use_query_state=not interact_with_form)
    target_url = _google_flights_url(
        intent.language,
        intent.currency,
        location=intent.location,
        params=query_population["params"],
        result_surface=query_population["payload"]["status"] == "encoded",
    )
    _write_json(run_root / "query-state.json", query_population["artifact"])
    artifacts.append(str(run_root / "query-state.json"))
    source_surfaces.extend(query_population["source_surfaces"])
    if interact_with_form:
        try:
            validate_live_form_support(intent)
        except UnsupportedLiveForm as exc:
            _write_json(run_root / "command-log.json", executed)
            artifacts.append(str(run_root / "command-log.json"))
            return 3, _unsupported_live_form_payload(
                intent.query_id, run_id, browser_mode, exc, artifacts, source_surfaces
            )

    page_id = ""
    tab_context: dict[str, Any] = {
        "enabled": tab_budget_enabled(
            max_tabs=max_tabs,
            managed_tab_policy=managed_tab_policy,
        ),
        "before": None,
        "after": None,
        "managed_tab_policy": managed_tab_policy,
        "max_tabs": max_tabs,
        "reuse_target": "",
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
        url=target_url,
        managed_tab_policy=managed_tab_policy,
        tab_budget_before=tab_context["before"],
    )
    tab_context["managed_tab_created"] = managed_tab_created
    open_result = await _run_step(
        adapter=adapter,
        args=open_args,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="open.json",
        source_surface="cdp:open",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    page_id = open_result_page_id(open_result)
    if page_id and managed_tab_created:
        managed_tab_trace.record_target(page_id)
    recovered_open_warning = recoverable_open_page_warning(open_result, page_id)
    if recovered_open_warning:
        nonfatal_open_warnings = [recovered_open_warning]
    else:
        nonfatal_open_warnings = []
    if _is_stop_result(open_result):
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            exit_code=open_result.exit_code or 4,
            payload=_stop_payload(
                intent_query_id=intent.query_id,
                run_id=run_id,
                browser_mode=browser_mode,
                result=open_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                target_url=target_url,
                query_population=query_population,
            ),
        )
    if open_result.status == "tool_error" and not page_id:
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            exit_code=6,
            payload=_tool_error_payload(
                intent.query_id,
                run_id,
                browser_mode,
                open_result,
                artifacts,
                source_surfaces,
                target_url,
                query_population,
            ),
        )

    wait_result = await _run_step(
        adapter=adapter,
        args=["wait", "load-state", "domcontentloaded", "--target", page_id],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="wait.json",
        source_surface="cdp:wait",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_recoverable_context_error(wait_result):
        wait_result = await _run_step(
            adapter=adapter,
            args=["wait", "load-state", "domcontentloaded", "--target", page_id],
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            artifact_name="wait-retry-1.json",
            source_surface="cdp:wait:retry",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
        )
    if _wait_matched_about_blank(wait_result):
        navigation_result = await _run_step(
            adapter=adapter,
            args=[
                "wait",
                "eval",
                'location.href !== "about:blank"',
                "--target",
                page_id,
            ],
            browser_mode=browser_mode,
            timeout_seconds=min(timeout_seconds, 10.0),
            run_root=run_root,
            artifact_name="wait-navigation-url.json",
            source_surface="cdp:wait:navigation-url",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
        )
        if _is_stop_result(navigation_result):
            return await _finish_live_search(
                adapter=adapter,
                page_id=page_id,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                executed=executed,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                tab_context=tab_context,
                exit_code=navigation_result.exit_code or 4,
                payload=_stop_payload(
                    intent_query_id=intent.query_id,
                    run_id=run_id,
                    browser_mode=browser_mode,
                    result=navigation_result,
                    artifacts=artifacts,
                    source_surfaces=source_surfaces,
                    target_url=target_url,
                    query_population=query_population,
                ),
            )
        if navigation_result.status != "tool_error":
            wait_result = await _run_step(
                adapter=adapter,
                args=["wait", "load-state", "domcontentloaded", "--target", page_id],
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                artifact_name="wait-after-navigation.json",
                source_surface="cdp:wait:after-navigation",
                executed=executed,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
            )
    if _is_stop_result(wait_result):
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            exit_code=wait_result.exit_code or 4,
            payload=_stop_payload(
                intent_query_id=intent.query_id,
                run_id=run_id,
                browser_mode=browser_mode,
                result=wait_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                target_url=target_url,
                query_population=query_population,
            ),
        )
    if wait_result.status == "tool_error":
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            exit_code=6,
            payload=_tool_error_payload(
                intent.query_id,
                run_id,
                browser_mode,
                wait_result,
                artifacts,
                source_surfaces,
                target_url,
                query_population,
            ),
        )

    if interact_with_form:
        for step in plan_live_form_interaction(intent, page_id=page_id).steps:
            result = await _run_step(
                adapter=adapter,
                args=step.args,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                artifact_name=step.artifact_name,
                source_surface=step.source_surface,
                executed=executed,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
            )
            if _is_stop_result(result):
                return await _finish_live_search(
                    adapter=adapter,
                    page_id=page_id,
                    browser_mode=browser_mode,
                    timeout_seconds=timeout_seconds,
                    run_root=run_root,
                    executed=executed,
                    artifacts=artifacts,
                    source_surfaces=source_surfaces,
                    tab_context=tab_context,
                    exit_code=result.exit_code or 4,
                    payload=_stop_payload(
                        intent_query_id=intent.query_id,
                        run_id=run_id,
                        browser_mode=browser_mode,
                        result=result,
                        artifacts=artifacts,
                        source_surfaces=source_surfaces,
                        target_url=target_url,
                        query_population=query_population,
                    ),
                )
            if result.status == "tool_error":
                return await _finish_live_search(
                    adapter=adapter,
                    page_id=page_id,
                    browser_mode=browser_mode,
                    timeout_seconds=timeout_seconds,
                    run_root=run_root,
                    executed=executed,
                    artifacts=artifacts,
                    source_surfaces=source_surfaces,
                    tab_context=tab_context,
                    exit_code=6,
                    payload=_tool_error_payload(
                        intent.query_id,
                        run_id,
                        browser_mode,
                        result,
                        artifacts,
                        source_surfaces,
                        target_url,
                        query_population,
                    ),
                )

    nonfatal_warnings: list[str] = [*nonfatal_open_warnings]
    settlement = await _settle_google_flights_page(
        adapter=adapter,
        page_id=page_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    nonfatal_warnings.extend(settlement["warnings"])
    if settlement["stop_result"] is not None:
        stop_result = settlement["stop_result"]
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            exit_code=stop_result.exit_code or 4,
            payload=_stop_payload(
                intent_query_id=intent.query_id,
                run_id=run_id,
                browser_mode=browser_mode,
                result=stop_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                target_url=target_url,
                query_population=query_population,
            ),
        )
    snapshot_evidence_artifact = str(run_root / "snapshot.json")
    snapshot_result = await _run_step(
        adapter=adapter,
        args=["snapshot", "--target", page_id, "--limit", "80"],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="snapshot.json",
        source_surface="cdp:snapshot",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_recoverable_context_error(snapshot_result):
        snapshot_evidence_artifact = str(run_root / "snapshot-retry-1.json")
        snapshot_result = await _run_step(
            adapter=adapter,
            args=["snapshot", "--target", page_id, "--limit", "80"],
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            artifact_name="snapshot-retry-1.json",
            source_surface="cdp:snapshot:retry",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
        )
    if _snapshot_needs_result_retry(snapshot_result):
        settle_result = await _run_step(
            adapter=adapter,
            args=["wait", "network-idle", "--target", page_id, "--idle", "2s"],
            browser_mode=browser_mode,
            timeout_seconds=min(timeout_seconds, 10.0),
            run_root=run_root,
            artifact_name="wait-results-network-idle.json",
            source_surface="cdp:wait:results-network-idle",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
        )
        if _is_stop_result(settle_result):
            return await _finish_live_search(
                adapter=adapter,
                page_id=page_id,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_root=run_root,
                executed=executed,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                tab_context=tab_context,
                exit_code=settle_result.exit_code or 4,
                payload=_stop_payload(
                    intent_query_id=intent.query_id,
                    run_id=run_id,
                    browser_mode=browser_mode,
                    result=settle_result,
                    artifacts=artifacts,
                    source_surfaces=source_surfaces,
                    target_url=target_url,
                    query_population=query_population,
                ),
            )
        if settle_result.status == "tool_error":
            nonfatal_warnings.append(
                "result-load network-idle wait failed; retrying snapshot once with bounded evidence"
            )
        snapshot_evidence_artifact = str(run_root / "snapshot-results-retry-1.json")
        snapshot_result = await _run_step(
            adapter=adapter,
            args=["snapshot", "--target", page_id, "--limit", "120"],
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            artifact_name="snapshot-results-retry-1.json",
            source_surface="cdp:snapshot:results-retry",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
        )
    await _expand_considered_accessible_rows(
        adapter=adapter,
        page_id=page_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        considered_limit=_considered_row_limit(top_k),
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        warnings=nonfatal_warnings,
    )
    considered_row_limit = _considered_row_limit(top_k)
    accessible_rows_evidence_artifact = str(run_root / "accessible-rows.json")
    accessible_rows_result = await _run_step(
        adapter=adapter,
        args=[
            "eval",
            accessible_rows_js(stage="auto", limit=max(20, considered_row_limit)),
            "--target",
            page_id,
        ],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 10.0),
        run_root=run_root,
        artifact_name="accessible-rows.json",
        source_surface="cdp:eval:accessible-flight-rows",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    network_result = await _run_step(
        adapter=adapter,
        args=["network", "--target", page_id, "--limit", "50", "--wait", "1s"],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="network.json",
        source_surface="cdp:network",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    _write_command_log(run_root, executed, artifacts)

    if snapshot_result.status == "tool_error":
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            exit_code=6,
            payload=_tool_error_payload(
                intent.query_id,
                run_id,
                browser_mode,
                snapshot_result,
                artifacts,
                source_surfaces,
                target_url,
                query_population,
            ),
        )
    if network_result.status == "tool_error":
        nonfatal_warnings.append(
            "network evidence capture failed after snapshot evidence was collected"
        )

    extracted_results: list[dict[str, Any]] = []
    extracted_confidence = "weak"
    if accessible_rows_result.status != "tool_error":
        extracted_results = extract_primary_results(
            accessible_rows_result.json_payload or {},
            source_surface="primary-results-accessible-rows",
            evidence_artifact=accessible_rows_evidence_artifact,
            confidence="medium",
        )
        extracted_confidence = "medium" if extracted_results else "weak"
    else:
        nonfatal_warnings.append(
            "accessible ARIA row extraction failed; falling back to visible text snapshot parsing"
        )
    if not extracted_results:
        extracted_results = extract_primary_results(
            snapshot_result.json_payload or {},
            source_surface="primary-results-visible-text",
            evidence_artifact=snapshot_evidence_artifact,
            confidence="weak",
        )
        extracted_confidence = "weak"
    price_observations_written = _write_price_observations(
        state=state,
        intent=intent,
        results=extracted_results,
        run_id=run_id,
    )
    if extracted_results:
        ranking_payload = _ranked_result_payload(
            extracted_results,
            rank_objectives=rank_objectives,
            top_k=top_k,
            allow_transit=allow_transit,
            deny_transit=deny_transit,
        )
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            exit_code=0,
            payload={
                "query_id": intent.query_id,
                "status": "ok",
                "confidence": extracted_confidence,
                "live_mode": True,
                "browser_mode": browser_mode,
                "target_url": target_url,
                "query_population": query_population["payload"],
                "results": extracted_results,
                **ranking_payload,
                "unsupported": [],
                "warnings": [
                    *query_population["warnings"],
                    *nonfatal_warnings,
                    (
                        "primary result rows were parsed from ARIA/role row evidence; visible text snapshot remains fallback evidence"
                        if extracted_confidence == "medium"
                        else "primary result rows were parsed from visible text evidence; missing optional fields are left empty or null"
                    ),
                    *(
                        ["live form interaction is experimental and limited to observed controls"]
                        if interact_with_form
                        else []
                    ),
                    "Google Flights URL query/protobuf encoding is not guessed",
                ],
                "evidence": {
                    "run_id": run_id,
                    "artifacts": artifacts,
                    "source_surfaces": source_surfaces,
                },
                "cache": {
                    "database_path": str(state.database_path),
                    "price_observations_written": price_observations_written,
                },
            },
        )

    absence_status = classify_primary_result_absence(snapshot_result.json_payload or {})
    if absence_status == "google_page_error":
        google_page_error = CdpResult(
            argv=[],
            browser_mode=browser_mode,
            returncode=6,
            stdout="",
            stderr="",
            status="tool_error",
            exit_code=6,
            stop_state="google_page_error",
            error="Google Flights page reported a transient error and offered Reload",
        )
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            exit_code=6,
            payload=_stop_payload(
                intent_query_id=intent.query_id,
                run_id=run_id,
                browser_mode=browser_mode,
                result=google_page_error,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                target_url=target_url,
                query_population=query_population,
            ),
        )
    if absence_status == "no_results":
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            exit_code=0,
            payload={
                "query_id": intent.query_id,
                "status": "no_results",
                "confidence": "weak",
                "live_mode": True,
                "browser_mode": browser_mode,
                "target_url": target_url,
                "query_population": query_population["payload"],
                "results": [],
                "unsupported": [],
                "warnings": [
                    *query_population["warnings"],
                    *nonfatal_warnings,
                    "visible Google Flights evidence reported no primary result rows",
                ],
                "evidence": {
                    "run_id": run_id,
                    "artifacts": artifacts,
                    "source_surfaces": source_surfaces,
                },
                "cache": {
                    "database_path": str(state.database_path),
                    "price_observations_written": price_observations_written,
                },
            },
        )
    if absence_status in {"empty_snapshot", "loading_results"}:
        return await _finish_live_search(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            tab_context=tab_context,
            exit_code=3,
            payload={
                "query_id": intent.query_id,
                "status": "unsupported",
                "confidence": "weak",
                "live_mode": True,
                "browser_mode": browser_mode,
                "target_url": target_url,
                "query_population": query_population["payload"],
                "results": [],
                "unsupported": [
                    *query_population["unsupported"],
                    {
                        "field": f"live_result_extraction.{absence_status}",
                        "status": "deferred",
                        "reason": "Google Flights did not expose visible primary result rows within the bounded evidence wait",
                    },
                ],
                "warnings": [
                    *query_population["warnings"],
                    *nonfatal_warnings,
                    "live search captured bounded evidence but primary rows were not visible yet",
                    "Google Flights URL query/protobuf encoding is not guessed",
                ],
                "evidence": {
                    "run_id": run_id,
                    "artifacts": artifacts,
                    "source_surfaces": source_surfaces,
                },
                "cache": {
                    "database_path": str(state.database_path),
                    "price_observations_written": price_observations_written,
                },
            },
        )

    return await _finish_live_search(
        adapter=adapter,
        page_id=page_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        tab_context=tab_context,
        exit_code=0,
        payload={
            "query_id": intent.query_id,
            "status": "experimental",
            "confidence": "weak",
            "live_mode": True,
            "browser_mode": browser_mode,
            "target_url": target_url,
            "query_population": query_population["payload"],
            "results": [],
            "unsupported": [
                *query_population["unsupported"],
                {
                    "field": "live_result_extraction",
                    "status": "deferred",
                    "reason": "this live mode captures cdp evidence; durable Google Flights result extraction still requires focused evidence and parser tests",
                },
            ],
            "warnings": [
                *query_population["warnings"],
                *nonfatal_warnings,
                "live cdp mode is the default search path; no primary result rows were extracted from this snapshot",
                *(
                    ["live form interaction is experimental and limited to observed controls"]
                    if interact_with_form
                    else []
                ),
                "Google Flights URL query/protobuf encoding is not guessed",
            ],
            "evidence": {
                "run_id": run_id,
                "artifacts": artifacts,
                "source_surfaces": source_surfaces,
            },
            "cache": {
                "database_path": str(state.database_path),
                "price_observations_written": price_observations_written,
            },
        },
    )


async def _expand_considered_accessible_rows(
    *,
    adapter: CdpAdapter,
    page_id: str,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    considered_limit: int,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
    warnings: list[str],
) -> None:
    expand_result = await _run_step(
        adapter=adapter,
        args=[
            "eval",
            accessible_row_expand_js(
                stage="auto",
                limit=considered_limit,
            ),
            "--target",
            page_id,
        ],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 10.0),
        run_root=run_root,
        artifact_name="accessible-row-expand.json",
        source_surface="cdp:eval:accessible-flight-row-expand",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if expand_result.status == "tool_error":
        warnings.append("accessible row expansion failed; continuing with row evidence")


def _considered_row_limit(top_k: int) -> int:
    return min(
        max(
            top_k if top_k > 0 else DEFAULT_FLIGHT_SEARCH_TAB_BUDGET,
            DEFAULT_FLIGHT_SEARCH_TAB_BUDGET,
        ),
        50,
    )


def _eval_value(payload: dict[str, Any]) -> Any:
    for key in ("value", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            if "value" in value:
                return value["value"]
            nested_result = value.get("result")
            if isinstance(nested_result, dict) and "value" in nested_result:
                return nested_result["value"]
        if isinstance(value, list | str):
            return value
    return None


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


async def _settle_google_flights_page(
    *,
    adapter: CdpAdapter,
    page_id: str,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
) -> dict[str, Any]:
    started = perf_counter()
    warnings: list[str] = []
    terminal_result = await _run_step(
        adapter=adapter,
        args=["wait", "eval", TERMINAL_DOM_CONDITION_JS, "--target", page_id],
        browser_mode=browser_mode,
        timeout_seconds=max(timeout_seconds, MINIMUM_GOOGLE_FLIGHTS_DWELL_SECONDS),
        run_root=run_root,
        artifact_name="wait-terminal-dom.json",
        source_surface="cdp:wait:terminal-dom",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    terminal_condition = _terminal_condition_from_wait(terminal_result)
    if terminal_condition in TRANSIENT_TERMINAL_CONDITIONS:
        warnings.append(
            f"terminal Google Flights check saw provisional {terminal_condition}; continuing to dwell and snapshot evidence"
        )
    if terminal_condition in BLOCKED_STOP_STATES:
        stop_result = _blocked_terminal_result(terminal_result, terminal_condition)
        _write_settlement_artifact(
            run_root=run_root,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            terminal_condition=terminal_condition,
            terminal_status=stop_result.status,
            body_stability=_body_stability_from_samples(stop_result, None),
            network_status="not_run",
            dwell_seconds=perf_counter() - started,
            dwell_enforced=False,
            warnings=warnings,
        )
        return {"stop_result": stop_result, "warnings": warnings}
    if _is_stop_result(terminal_result):
        _write_settlement_artifact(
            run_root=run_root,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            terminal_condition=terminal_condition,
            terminal_status=terminal_result.status,
            body_stability=_body_stability_from_samples(terminal_result, None),
            network_status="not_run",
            dwell_seconds=perf_counter() - started,
            dwell_enforced=False,
            warnings=warnings,
        )
        return {"stop_result": terminal_result, "warnings": warnings}
    if terminal_result.status == "tool_error":
        warnings.append(
            "terminal Google Flights content did not appear before the bounded settlement wait; capturing snapshot evidence anyway"
        )
    elif terminal_condition in READY_TERMINAL_CONDITIONS:
        _write_settlement_artifact(
            run_root=run_root,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            terminal_condition=terminal_condition,
            terminal_status=terminal_result.status,
            body_stability={"status": "skipped_terminal_ready", "sample_count": 0},
            network_status="skipped_terminal_ready",
            dwell_seconds=perf_counter() - started,
            dwell_enforced=False,
            warnings=warnings,
        )
        return {"stop_result": None, "warnings": warnings}

    dwell_enforced = False
    elapsed = perf_counter() - started
    remaining_dwell = MINIMUM_GOOGLE_FLIGHTS_DWELL_SECONDS - elapsed
    if remaining_dwell > 0 and _should_enforce_live_dwell(adapter):
        dwell_enforced = True
        await asyncio.sleep(remaining_dwell)

    sample_one = await _run_step(
        adapter=adapter,
        args=["text", "body", "--target", page_id, "--limit", "0"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 10.0),
        run_root=run_root,
        artifact_name="settlement-text-1.json",
        source_surface="cdp:text:settlement-sample",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_stop_result(sample_one):
        _write_settlement_artifact(
            run_root=run_root,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            terminal_condition=terminal_condition,
            terminal_status=terminal_result.status,
            body_stability=_body_stability_from_samples(sample_one, None),
            network_status="not_run",
            dwell_seconds=perf_counter() - started,
            dwell_enforced=dwell_enforced,
            warnings=warnings,
        )
        return {"stop_result": sample_one, "warnings": warnings}
    sample_two = await _run_step(
        adapter=adapter,
        args=["text", "body", "--target", page_id, "--limit", "0"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 10.0),
        run_root=run_root,
        artifact_name="settlement-text-2.json",
        source_surface="cdp:text:settlement-sample",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_stop_result(sample_two):
        _write_settlement_artifact(
            run_root=run_root,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            terminal_condition=terminal_condition,
            terminal_status=terminal_result.status,
            body_stability=_body_stability_from_samples(sample_one, sample_two),
            network_status="not_run",
            dwell_seconds=perf_counter() - started,
            dwell_enforced=dwell_enforced,
            warnings=warnings,
        )
        return {"stop_result": sample_two, "warnings": warnings}
    body_stability = _body_stability_from_samples(sample_one, sample_two)
    if body_stability["status"] != "stable":
        warnings.append("rendered body text changed during settlement sampling")

    network_result = await _run_step(
        adapter=adapter,
        args=["wait", "network-idle", "--target", page_id, "--idle", "1s"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 10.0),
        run_root=run_root,
        artifact_name="wait-terminal-network-steady.json",
        source_surface="cdp:wait:terminal-network-steady",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_stop_result(network_result):
        _write_settlement_artifact(
            run_root=run_root,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            terminal_condition=terminal_condition,
            terminal_status=terminal_result.status,
            body_stability=body_stability,
            network_status=network_result.status,
            dwell_seconds=perf_counter() - started,
            dwell_enforced=dwell_enforced,
            warnings=warnings,
        )
        return {"stop_result": network_result, "warnings": warnings}
    if network_result.status == "tool_error":
        warnings.append(
            "network steady wait failed after terminal content check; continuing with visible snapshot evidence"
        )

    _write_settlement_artifact(
        run_root=run_root,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        terminal_condition=terminal_condition,
        terminal_status=terminal_result.status,
        body_stability=body_stability,
        network_status=network_result.status,
        dwell_seconds=perf_counter() - started,
        dwell_enforced=dwell_enforced,
        warnings=warnings,
    )
    return {"stop_result": None, "warnings": warnings}


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


def _body_stability_from_samples(
    first: CdpResult,
    second: CdpResult | None,
) -> dict[str, Any]:
    first_text = _visible_text_from_payload(first.json_payload or {})
    second_text = _visible_text_from_payload((second.json_payload or {}) if second else {})
    first_hash = _text_hash(first_text)
    second_hash = _text_hash(second_text)
    if first.status == "tool_error" or (second and second.status == "tool_error"):
        status = "unavailable"
    elif not first_hash or not second_hash:
        status = "unavailable"
    elif first_hash == second_hash:
        status = "stable"
    else:
        status = "changed"
    return {
        "status": status,
        "sample_count": 2 if second is not None else 1,
        "first_text_length": len(first_text),
        "second_text_length": len(second_text),
        "first_sha256": first_hash,
        "second_sha256": second_hash,
    }


def _visible_text_from_payload(payload: dict[str, Any]) -> str:
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


def _text_hash(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _should_enforce_live_dwell(adapter: CdpAdapter) -> bool:
    return type(adapter) is CdpAdapter and getattr(adapter, "_runner", None) is run_subprocess


def _write_settlement_artifact(
    *,
    run_root: Path,
    artifacts: list[str],
    source_surfaces: list[str],
    terminal_condition: str,
    terminal_status: str,
    body_stability: dict[str, Any],
    network_status: str,
    dwell_seconds: float,
    dwell_enforced: bool,
    warnings: list[str],
) -> None:
    artifact_path = run_root / "settlement.json"
    _write_json(
        artifact_path,
        {
            "policy": "terminal-dom-then-dwell-then-network-steady-v1",
            "policy_reference": "docs/cdp-usage-discipline.md",
            "minimum_dwell_seconds": MINIMUM_GOOGLE_FLIGHTS_DWELL_SECONDS,
            "dwell_seconds": round(dwell_seconds, 3),
            "dwell_enforced": dwell_enforced,
            "terminal_condition": terminal_condition,
            "terminal_status": terminal_status,
            "network_status": network_status,
            "body_stability": body_stability,
            "warnings": warnings,
        },
    )
    artifacts.append(str(artifact_path))
    source_surfaces.append("cdp:settlement")


async def _finish_live_search(
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
    exit_code: int,
    payload: dict[str, Any],
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
            warnings=_payload_warnings(payload),
            task_trace=managed_tab_trace if isinstance(managed_tab_trace, TaskTrace) else None,
        )
        cleanup_status = close_result.status if close_result is not None else "not_run_no_page_id"
    elif tab_context is not None and bool(tab_context.get("managed_tab_created", True)) and page_id:
        cleanup_status = "skipped_unowned_tab"
    elif tab_context is not None:
        cleanup_status = "skipped_reused_tab"

    if tab_context is not None:
        tab_context["cleanup_status"] = cleanup_status
        tab_context["managed_tab_id"] = page_id
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
            payload["managed_tab_id"] = page_id or None
            payload["tab_budget"] = tab_budget_summary(
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
            evidence = payload.get("evidence")
            if isinstance(evidence, dict):
                evidence["task_trace"] = task_trace.as_dict()
    _write_command_log(run_root, executed, artifacts)
    return exit_code, payload


def _payload_warnings(payload: dict[str, Any]) -> list[str] | None:
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        return warnings
    return None


def _ranked_result_payload(
    results: list[dict[str, Any]],
    *,
    rank_objectives: list[str] | None,
    top_k: int,
    allow_transit: list[str] | None,
    deny_transit: list[str] | None,
) -> dict[str, Any]:
    if not rank_objectives and top_k <= 0:
        return {}
    return rank_observed_results(
        results,
        objectives=rank_objectives or ["balanced"],
        top_k=top_k if top_k > 0 else 10,
        allow_transit=allow_transit,
        deny_transit=deny_transit,
    )


def _snapshot_needs_result_retry(result: CdpResult) -> bool:
    if result.status == "tool_error":
        return False
    payload = result.json_payload or {}
    if extract_primary_results(payload):
        return False
    return classify_primary_result_absence(payload) in {
        "empty_snapshot",
        "loading_results",
    }


def _wait_matched_about_blank(result: CdpResult) -> bool:
    payload = result.json_payload or {}
    wait = payload.get("wait")
    return isinstance(wait, dict) and wait.get("url") == "about:blank"


def _write_command_log(
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
) -> None:
    command_log = run_root / "command-log.json"
    _write_json(command_log, executed)
    if str(command_log) not in artifacts:
        artifacts.append(str(command_log))


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


def _stop_payload(
    *,
    intent_query_id: str,
    run_id: str,
    browser_mode: BrowserMode,
    result: CdpResult,
    artifacts: list[str],
    source_surfaces: list[str],
    target_url: str,
    query_population: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query_id": intent_query_id,
        "status": (
            result.status
            if result.status in BLOCKED_STOP_STATES or result.status == "tool_error"
            else "blocked"
        ),
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "target_url": target_url,
        "stop_state": result.stop_state or result.status,
        "query_population": query_population["payload"],
        "results": [],
        "unsupported": [*query_population["unsupported"]],
        "warnings": [
            *query_population["warnings"],
            (
                result.error
                if result.status == "tool_error" and result.error
                else "live search stopped before bypassing a browser safety boundary"
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
    return payload


def _tool_error_payload(
    query_id: str,
    run_id: str,
    browser_mode: BrowserMode,
    result: CdpResult,
    artifacts: list[str],
    source_surfaces: list[str],
    target_url: str,
    query_population: dict[str, Any],
) -> dict[str, Any]:
    return {
        "query_id": query_id,
        "status": "tool_error",
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "target_url": target_url,
        "query_population": query_population["payload"],
        "results": [],
        "unsupported": [*query_population["unsupported"]],
        "warnings": [*query_population["warnings"]],
        "error": result.error,
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }


def _unsupported_live_form_payload(
    query_id: str,
    run_id: str,
    browser_mode: BrowserMode,
    error: UnsupportedLiveForm,
    artifacts: list[str],
    source_surfaces: list[str],
) -> dict[str, Any]:
    return {
        "query_id": query_id,
        "status": "unsupported",
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "results": [],
        "unsupported": [
            {
                "field": error.field,
                "value": error.value,
                "status": "deferred",
                "reason": error.reason,
            }
        ],
        "warnings": [],
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }


def _build_query_population(intent: SearchIntent, *, use_query_state: bool) -> dict[str, Any]:
    if not use_query_state:
        payload = {
            "status": "form",
            "confidence": "weak",
            "params": [],
            "source_surfaces": ["cdp:form"],
        }
        return {
            "params": {},
            "source_surfaces": [],
            "unsupported": [],
            "warnings": [
                "live form interaction will populate query state through observed controls"
            ],
            "payload": payload,
            "artifact": payload,
        }

    try:
        state = build_query_state(intent)
    except UnsupportedQueryState as exc:
        unsupported = [
            {
                "field": exc.field,
                "value": exc.value,
                "status": "deferred",
                "reason": exc.reason,
            }
        ]
        payload = {
            "status": "unsupported",
            "confidence": "unknown",
            "params": [],
            "unsupported": unsupported,
        }
        return {
            "params": {},
            "source_surfaces": ["query-state:unsupported"],
            "unsupported": unsupported,
            "warnings": [
                "live search opened the Google Flights shell because this intent could not be encoded into evidence-backed query state"
            ],
            "payload": payload,
            "artifact": payload,
        }

    payload = {
        "status": "encoded",
        "confidence": state.confidence,
        "params": list(state.params),
        "source_surfaces": state.source_surfaces,
        "evidence_refs": state.evidence_refs,
    }
    return {
        "params": state.params,
        "source_surfaces": state.source_surfaces,
        "unsupported": [],
        "warnings": state.warnings,
        "payload": payload,
        "artifact": payload,
    }


def _is_stop_result(result: CdpResult) -> bool:
    return bool(result.fallback) or result.status in BLOCKED_STOP_STATES


def _is_recoverable_context_error(result: CdpResult) -> bool:
    if result.status != "tool_error":
        return False
    payload = result.json_payload or {}
    message = str(payload.get("message") or result.error or result.stdout)
    return (
        payload.get("code") == "connection_failed"
        and "Cannot find default execution context" in message
    )


def _write_price_observations(
    *,
    state: AppState,
    intent: SearchIntent,
    results: list[dict[str, Any]],
    run_id: str,
) -> int:
    cache = price_cache_for_state(state)
    written = 0
    captured_at = datetime.now(timezone.utc)
    for result in results:
        price = result.get("price")
        if not isinstance(price, dict) or price.get("amount") is None:
            continue
        currency = str(price.get("currency") or result.get("currency") or intent.currency)
        cache.put_price(
            cache_key=_price_cache_key(intent, result, currency),
            query_id=intent.query_id,
            departure_date=intent.departure_window.start,
            return_date=_return_date(intent),
            currency=currency,
            price_amount=price["amount"],
            price_payload=_price_payload(result),
            captured_at=captured_at,
            source_run_id=run_id,
        )
        written += 1
    return written


def _price_cache_key(intent: SearchIntent, result: dict[str, Any], currency: str) -> str:
    route = _route_key(result) or f"{intent.origin.text}-{intent.destination.text}"
    return "|".join(
        [
            intent.query_id,
            route,
            intent.departure_window.start,
            _return_date(intent) or "",
            currency,
            intent.cabin,
            _passenger_key(intent),
            str(result.get("result_id") or ""),
        ]
    )


def _route_key(result: dict[str, Any]) -> str:
    origins = result.get("origin_airports")
    destinations = result.get("destination_airports")
    if isinstance(origins, list) and origins and isinstance(destinations, list) and destinations:
        return f"{origins[0]}-{destinations[0]}"
    return ""


def _return_date(intent: SearchIntent) -> str | None:
    if intent.trip_type != "round_trip" or intent.return_window is None:
        return None
    return intent.return_window.start


def _passenger_key(intent: SearchIntent) -> str:
    passengers = intent.passengers
    return (
        f"adults={passengers.adults};children={passengers.children};"
        f"infants_in_seat={passengers.infants_in_seat};"
        f"infants_on_lap={passengers.infants_on_lap}"
    )


def _price_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "result_id": result.get("result_id"),
        "source_surface": result.get("source_surface"),
        "confidence": result.get("confidence"),
        "origin_airports": result.get("origin_airports", []),
        "destination_airports": result.get("destination_airports", []),
        "departure_times": result.get("departure_times", []),
        "arrival_times": result.get("arrival_times", []),
        "carriers": result.get("carriers", []),
        "duration_text": result.get("duration_text"),
        "duration_minutes": result.get("duration_minutes"),
        "stops": result.get("stops"),
        "layovers": result.get("layovers", []),
        "price": result.get("price"),
        "currency": result.get("currency"),
        "emissions": result.get("emissions"),
        "baggage_summary": result.get("baggage_summary"),
        "evidence": {
            "source_surfaces": (result.get("evidence") or {}).get("source_surfaces", []),
        },
    }


def _batch_run_id(run_id: str | None, query_id: str, index: int) -> str:
    if run_id is None:
        return _new_run_id(query_id)
    safe_query_id = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in query_id.lower())
    return f"{run_id}-{index}-{safe_query_id}"


def _aggregate_exit_code(exit_codes: list[int]) -> int:
    for exit_code in (6, 5, 4, 3, 2):
        if exit_code in exit_codes:
            return exit_code
    return 0


def _google_flights_url(
    language: str,
    currency: str,
    *,
    location: str | None = None,
    params: dict[str, str] | None = None,
    result_surface: bool = False,
) -> str:
    query_params = dict(params or {})
    query_params.setdefault("hl", language)
    query_params.setdefault("curr", currency)
    if location:
        query_params.setdefault("gl", location)
    base_url = GOOGLE_FLIGHTS_SEARCH_URL if result_surface else GOOGLE_FLIGHTS_URL
    return f"{base_url}?{urlencode(query_params)}"


def _new_run_id(query_id: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_query_id = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in query_id.lower())
    return f"gf-{now}-{safe_query_id}"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")
