"""Live Google Flights row selection to booking-summary URL."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from gflights.app_state import init_app_state
from gflights.browser import BLOCKED_STOP_STATES, BrowserMode, CdpAdapter, CdpResult, run_subprocess
from gflights.itinerary_extraction import extract_selected_itinerary
from gflights.live_cleanup import close_managed_page
from gflights.live_search import MINIMUM_GOOGLE_FLIGHTS_DWELL_SECONDS
from gflights.live_tabs import (
    capture_tab_budget,
    open_args_for_policy,
    tab_budget_enabled,
    tab_budget_summary,
)
from gflights.result_extraction import extract_primary_results

SEARCH_TERMINAL_JS = r"""
(() => {
  const text = (document.body && (document.body.innerText || document.body.textContent) || "")
    .replace(/\s+/g, " ")
    .trim();
  const lower = text.toLowerCase();
  if (!text) return false;
  if (lower.includes("unusual traffic") || lower.includes("access denied")) return "blocked";
  if (lower.includes("sign in") && lower.includes("google")) return "login_required";
  if (/no (matching )?flights|no results/.test(lower)) return "no_results";
  const hasPrice = /(?:DKK|EUR|USD|INR|NOK|SEK|GBP|₹|€|\$)\s*[0-9][0-9,.]*(?:\s+round trip)?/i.test(text);
  const hasRows = /(search results|departing flights|returning flights|choose return|round trip|nonstop|[0-9]+\s+stop)/i.test(text);
  return hasPrice && hasRows ? "fare_rows" : false;
})()
""".strip()

BOOKING_TERMINAL_JS = r"""
(() => {
  const text = (document.body && (document.body.innerText || document.body.textContent) || "")
    .replace(/\s+/g, " ")
    .trim();
  const lower = text.toLowerCase();
  if (!text) return false;
  if (lower.includes("unusual traffic") || lower.includes("access denied")) return "blocked";
  if (lower.includes("sign in") && lower.includes("google")) return "login_required";
  if (/no (matching )?flights|no results/.test(lower)) return "no_results";
  if (lower.includes("booking options") || lower.includes("book with")) return "booking_summary";
  return location.href.includes("/travel/flights/booking") ? "booking_url" : false;
})()
""".strip()


async def run_live_itinerary_selection(
    *,
    search_url: str,
    project_root: Path | None = None,
    adapter: CdpAdapter | None = None,
    browser_mode: BrowserMode = "headless",
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
) -> tuple[int, dict[str, Any]]:
    """Select visible Google Flights outbound/return rows and return booking URL."""

    adapter = adapter or CdpAdapter(max_tabs=max_tabs)
    state = init_app_state(project_root)
    run_id = run_id or _new_run_id()
    run_root = state.run_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    executed: list[dict[str, Any]] = []
    artifacts: list[str] = []
    source_surfaces: list[str] = []
    warnings: list[str] = [
        "row selection is limited to visible Google Flights rows and stops at Google booking summary"
    ]
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

    open_result = await _run_step(
        adapter=adapter,
        args=open_args,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="open.json",
        source_surface="cdp:open:itinerary-selection",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    page_id = _page_id(open_result.json_payload)
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
    if open_result.status == "tool_error":
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

    search_settle = await _settle(
        adapter=adapter,
        page_id=page_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        stage="outbound",
        condition_js=SEARCH_TERMINAL_JS,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
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

    outbound_selection = await _select_row(
        adapter=adapter,
        page_id=page_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        stage="outbound",
        preferred_carrier=preferred_carrier,
        require_nonstop=require_nonstop,
        row_rank=outbound_row_rank or row_rank,
        match_text=outbound_match_text,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if not outbound_selection.get("selected"):
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

    return_settle = await _settle(
        adapter=adapter,
        page_id=page_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        stage="return",
        condition_js=SEARCH_TERMINAL_JS,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
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

    return_selection = await _select_row(
        adapter=adapter,
        page_id=page_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        stage="return",
        preferred_carrier=preferred_carrier,
        require_nonstop=require_nonstop,
        row_rank=return_row_rank or row_rank,
        match_text=return_match_text,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if not return_selection.get("selected"):
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

    booking_settle = await _settle(
        adapter=adapter,
        page_id=page_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        stage="booking",
        condition_js=BOOKING_TERMINAL_JS,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
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

    location_result = await _run_step(
        adapter=adapter,
        args=["eval", "window.location.href", "--target", page_id],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 10.0),
        run_root=run_root,
        artifact_name="booking-location.json",
        source_surface="cdp:eval:booking-location",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
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
    booking_url = _string_value(location_result.json_payload or {})
    if "/travel/flights/booking" not in booking_url:
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
                    "booking_url": booking_url,
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

    booking_snapshot = await _run_step(
        adapter=adapter,
        args=["snapshot", "--target", page_id, "--limit", "160"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 20.0),
        run_root=run_root,
        artifact_name="booking-snapshot.json",
        source_surface="cdp:snapshot:booking-summary",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
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
    adapter: CdpAdapter,
    page_id: str,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    stage: str,
    condition_js: str,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    started = perf_counter()
    terminal_result = await _run_step(
        adapter=adapter,
        args=["wait", "eval", condition_js, "--target", page_id],
        browser_mode=browser_mode,
        timeout_seconds=max(timeout_seconds, MINIMUM_GOOGLE_FLIGHTS_DWELL_SECONDS),
        run_root=run_root,
        artifact_name=f"{stage}-wait-terminal-dom.json",
        source_surface=f"cdp:wait:{stage}:terminal-dom",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_stop_result(terminal_result):
        _write_settlement_json(
            run_root=run_root,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            stage=stage,
            terminal_condition=_string_value(terminal_result.json_payload or {}) or "unknown",
            terminal_status=terminal_result.status,
            dwell_seconds=perf_counter() - started,
            dwell_enforced=False,
            network_status="not_run",
            body_stability={"status": "not_run"},
            warnings=warnings,
        )
        return {"stop_result": terminal_result}
    if terminal_result.status == "tool_error":
        warnings.append(f"{stage} terminal DOM condition did not appear before timeout")

    dwell_enforced = False
    remaining_dwell = MINIMUM_GOOGLE_FLIGHTS_DWELL_SECONDS - (perf_counter() - started)
    if remaining_dwell > 0 and _should_enforce_live_dwell(adapter):
        dwell_enforced = True
        await asyncio.sleep(remaining_dwell)

    first_text = await _run_step(
        adapter=adapter,
        args=["text", "body", "--target", page_id, "--limit", "0"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 10.0),
        run_root=run_root,
        artifact_name=f"{stage}-settlement-text-1.json",
        source_surface=f"cdp:text:{stage}:settlement-sample",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    second_text = await _run_step(
        adapter=adapter,
        args=["text", "body", "--target", page_id, "--limit", "0"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 10.0),
        run_root=run_root,
        artifact_name=f"{stage}-settlement-text-2.json",
        source_surface=f"cdp:text:{stage}:settlement-sample",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    body_stability = _body_stability(first_text, second_text)
    if body_stability["status"] == "changed":
        warnings.append(f"{stage} rendered body text changed during settlement sampling")

    network_result = await _run_step(
        adapter=adapter,
        args=["wait", "network-idle", "--target", page_id, "--idle", "1s"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 10.0),
        run_root=run_root,
        artifact_name=f"{stage}-wait-network-steady.json",
        source_surface=f"cdp:wait:{stage}:network-steady",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_stop_result(network_result):
        _write_settlement_json(
            run_root=run_root,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            stage=stage,
            terminal_condition=_string_value(terminal_result.json_payload or {}) or "unknown",
            terminal_status=terminal_result.status,
            dwell_seconds=perf_counter() - started,
            dwell_enforced=dwell_enforced,
            network_status=network_result.status,
            body_stability=body_stability,
            warnings=warnings,
        )
        return {"stop_result": network_result}
    if network_result.status == "tool_error":
        warnings.append(f"{stage} network steady wait failed after terminal DOM check")

    _write_settlement_json(
        run_root=run_root,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        stage=stage,
        terminal_condition=_string_value(terminal_result.json_payload or {}) or "unknown",
        terminal_status=terminal_result.status,
        dwell_seconds=perf_counter() - started,
        dwell_enforced=dwell_enforced,
        network_status=network_result.status,
        body_stability=body_stability,
        warnings=warnings,
    )
    return {"stop_result": None}


async def _select_row(
    *,
    adapter: CdpAdapter,
    page_id: str,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    stage: str,
    preferred_carrier: str,
    require_nonstop: bool,
    row_rank: int,
    match_text: str,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
) -> dict[str, Any]:
    marker = f"gflights-{stage}-choice"
    selection_result = await _run_step(
        adapter=adapter,
        args=[
            "eval",
            _selection_js(
                marker=marker,
                preferred_carrier=preferred_carrier,
                require_nonstop=require_nonstop,
                row_rank=row_rank,
                match_text=match_text,
            ),
            "--target",
            page_id,
        ],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 15.0),
        run_root=run_root,
        artifact_name=f"{stage}-select-row.json",
        source_surface=f"cdp:eval:{stage}:select-row",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    selection = _dict_value(selection_result.json_payload or {})
    if selection_result.status == "tool_error":
        return {
            "selected": False,
            "stage": stage,
            "reason": selection_result.error or "row selection eval failed",
        }
    if not selection.get("selected"):
        selection.setdefault("stage", stage)
        return selection

    click_result = await _run_step(
        adapter=adapter,
        args=[
            "click",
            f'[data-gflights-selection="{marker}"]',
            "--target",
            page_id,
            "--strategy",
            "raw-input",
            "--activate",
        ],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 20.0),
        run_root=run_root,
        artifact_name=f"{stage}-click-row.json",
        source_surface=f"cdp:click:{stage}:selected-row",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if click_result.status == "tool_error":
        selection["selected"] = False
        selection["reason"] = click_result.error or "selected row click failed"
    selection["stage"] = stage
    return selection


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
    return_selection: dict[str, Any],
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
    selection: dict[str, Any],
    itinerary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not selection.get("selected"):
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
    parsed = extract_primary_results(
        {"items": [{"text": text}]},
        source_surface=f"itinerary-selection-{stage}-visible-row",
        confidence="weak",
    )
    summary: dict[str, Any] = {
        "stage": stage,
        "text": text,
        "confidence": "weak",
        "source_surface": f"itinerary-selection-{stage}-visible-row",
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
        }
    )
    return summary


def _selection_js(
    *,
    marker: str,
    preferred_carrier: str,
    require_nonstop: bool,
    row_rank: int,
    match_text: str,
) -> str:
    return f"""
(() => {{
  const preferredCarrier = {json.dumps(preferred_carrier)};
  const requireNonstop = {str(require_nonstop).lower()};
  const rowRank = Math.max(1, {row_rank});
  const matchText = {json.dumps(match_text)};
  const marker = {json.dumps(marker)};
  const priceRe = /(?:DKK|EUR|USD|INR|NOK|SEK|GBP|₹|€|\\$)\\s*[0-9][0-9,.]*(?:\\s+round trip)?/i;
  const rowSelector = 'li.pIav2d, div[role="listitem"], div[role="button"], li';
  const rows = Array.from(document.querySelectorAll(rowSelector))
    .map((el) => {{
      const rect = el.getBoundingClientRect();
      const text = (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
      return {{el, rect, text}};
    }})
    .filter((row) =>
      row.rect.width > 250 &&
      row.rect.height > 35 &&
      priceRe.test(row.text) &&
      /(Nonstop|\\d+\\s+stop|round trip|hr)/i.test(row.text)
    );
  for (const row of rows) {{
    row.el.removeAttribute("data-gflights-selection");
  }}
  const matches = rows.filter((row) => {{
    if (preferredCarrier && !row.text.toLowerCase().includes(preferredCarrier.toLowerCase())) return false;
    if (requireNonstop && !/\\bNonstop\\b/i.test(row.text)) return false;
    if (matchText && !row.text.toLowerCase().includes(matchText.toLowerCase())) return false;
    return true;
  }});
  const selected = matches[rowRank - 1] || matches[0] || null;
  if (!selected) {{
    return {{
      selected: false,
      preferredCarrier,
      requireNonstop,
      rowRank,
      matchText,
      candidateCount: rows.length,
      candidates: rows.slice(0, 8).map((row) => row.text.slice(0, 320))
    }};
  }}
  selected.el.setAttribute("data-gflights-selection", marker);
  selected.el.scrollIntoView({{block: "center", inline: "center"}});
  return {{
    selected: true,
    preferredCarrier,
    requireNonstop,
    rowRank,
    matchText,
    candidateCount: rows.length,
    matchCount: matches.length,
    text: selected.text.slice(0, 700)
  }};
}})()
""".strip()


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
    should_close = tab_context is None or bool(tab_context.get("managed_tab_created", True))
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
        )
        cleanup_status = close_result.status if close_result is not None else "not_run_no_page_id"
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
        "status": result.status if result.status in BLOCKED_STOP_STATES else "blocked",
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
            "live itinerary selection stopped before crossing a provider, login, payment, personal-data, or access-control boundary",
        ],
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }
    if result.fallback:
        payload["fallback"] = result.fallback
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
                "reason": "no visible Google Flights row matched the requested selection criteria",
            }
        ],
        "warnings": warnings,
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }


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


def _is_stop_result(result: CdpResult) -> bool:
    return bool(result.fallback) or result.status in BLOCKED_STOP_STATES


def _page_id(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    page = payload.get("page")
    if isinstance(page, dict) and isinstance(page.get("id"), str):
        return page["id"]
    target = payload.get("target")
    if isinstance(target, dict) and isinstance(target.get("id"), str):
        return target["id"]
    if isinstance(payload.get("id"), str):
        return payload["id"]
    return ""


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


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"gf-itinerary-select-{timestamp}"
