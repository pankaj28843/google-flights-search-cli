"""Preflight ceremony for live Google Flights crawl reliability."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from gflights.app_state import init_app_state
from gflights.browser import BrowserMode, CdpAdapter, CdpResult
from gflights.live_cleanup import close_managed_page
from gflights.live_search import run_live_search
from gflights.live_selection import run_live_itinerary_selection
from gflights.live_tasks import AsyncCrawlTaskManager
from gflights.live_trace import (
    TaskTrace,
    new_task_trace,
    open_result_page_id,
    recoverable_open_page_warning,
)

ConsentChoice = Literal["reject-all", "accept-all", "skip"]

SYNTHETIC_SMOKE_ROUTES: tuple[tuple[str, str, int], ...] = (
    ("JFK", "SFO", 90),
    ("LAX", "LAS", 91),
    ("ORD", "LAX", 92),
    ("BOS", "MIA", 93),
)

BODY_READY_JS = r"""
(() => {
  const text = (document.body && (document.body.innerText || document.body.textContent) || "")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > 0 ? "body_ready" : false;
})()
""".strip()

HEADLESS_KEEPALIVE_URL = "chrome://newtab/"


def consent_click_js(label: str) -> str:
    return f"""
(() => {{
  const label = {json.dumps(label)};
  const candidates = Array.from(document.querySelectorAll('button, [role="button"]'))
    .map((el) => {{
      const rect = el.getBoundingClientRect();
      const text = (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
      return {{el, rect, text}};
    }})
    .filter((item) =>
      item.text === label &&
      item.rect.width > 0 &&
      item.rect.height > 0 &&
      getComputedStyle(item.el).visibility !== "hidden" &&
      getComputedStyle(item.el).display !== "none"
    );
  if (candidates.length !== 1) {{
    return {{
      clicked: false,
      label,
      candidateCount: candidates.length,
      candidates: candidates.map((item) => item.text)
    }};
  }}
  candidates[0].el.click();
  return {{clicked: true, label, candidateCount: 1}};
}})()
""".strip()


def synthetic_smoke_intent(
    today: date | None = None,
    route_index: int = 0,
    date_range_index: int = 0,
) -> dict[str, Any]:
    """Return a public, non-personal round-trip smoke intent.

    Dates are deliberately generated from the current date so the smoke test
    remains useful over time and does not encode a user's trip.
    """

    today = today or datetime.now(UTC).date()
    origin, destination, depart_offset_days = SYNTHETIC_SMOKE_ROUTES[
        route_index % len(SYNTHETIC_SMOKE_ROUTES)
    ]
    depart = today + timedelta(days=depart_offset_days + (date_range_index * 14))
    return_date = depart + timedelta(days=7)
    route_slug = f"{origin.lower()}-{destination.lower()}"
    return {
        "query_id": f"preflight-{route_slug}-{depart.isoformat()}-return-{return_date.isoformat()}",
        "origin": {"text": origin, "kind": "airport_code"},
        "destination": {"text": destination, "kind": "airport_code"},
        "trip_type": "round_trip",
        "departure_window": {"start": depart.isoformat(), "end": depart.isoformat()},
        "return_window": {"start": return_date.isoformat(), "end": return_date.isoformat()},
        "passengers": {
            "adults": 1,
            "children": 0,
            "infants_in_seat": 0,
            "infants_on_lap": 0,
        },
        "cabin": "economy",
        "currency": "USD",
        "language": "en",
        "location": "US",
        "sort": "price",
        "google_filters": None,
        "preflight_date_range_index": date_range_index + 1,
    }


def synthetic_smoke_intents(
    today: date | None = None,
    date_range_count: int = 1,
) -> list[dict[str, Any]]:
    """Return route-fallback candidates for public synthetic preflight."""

    bounded_date_range_count = max(1, min(date_range_count, 6))
    return [
        synthetic_smoke_intent(
            today=today,
            route_index=route_index,
            date_range_index=date_range_index,
        )
        for date_range_index in range(bounded_date_range_count)
        for route_index in range(len(SYNTHETIC_SMOKE_ROUTES))
    ]


def _intent_route_label(intent: dict[str, Any]) -> str:
    origin = ((intent.get("origin") or {}).get("text") or "").upper()
    destination = ((intent.get("destination") or {}).get("text") or "").upper()
    return f"{origin}-{destination}" if origin and destination else "unknown"


async def run_google_flights_preflight(
    *,
    project_root: Path | None = None,
    browser_mode: BrowserMode = "headed",
    consent_choice: ConsentChoice = "reject-all",
    top_k: int = 5,
    return_top_k: int = 3,
    min_complete_selections: int | None = None,
    selection_concurrency: int = 3,
    date_range_count: int = 1,
    max_tabs: int | None = None,
    timeout_seconds: float = 45.0,
    search_deadline_seconds: float | None = None,
    adapter: CdpAdapter | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run consent seeding plus full public route search/selection smoke test."""

    state = init_app_state(project_root)
    run_id = _new_run_id()
    run_root = state.run_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    adapter = adapter or CdpAdapter(max_tabs=max_tabs)
    task_manager = AsyncCrawlTaskManager.root(
        command="gflights.preflight.google-flights",
        name="preflight-workflow",
        run_id=run_id,
        concurrency=max(1, selection_concurrency),
    )
    root_trace = task_manager.root_trace

    executed: list[dict[str, Any]] = []
    artifacts: list[str] = []
    source_surfaces: list[str] = []
    warnings: list[str] = []
    outbound_selection_count = max(1, min(top_k, 5))
    return_selection_count = max(1, min(return_top_k, 5))
    selection_pairs = _preflight_selection_pairs(
        outbound_selection_count=outbound_selection_count,
        return_selection_count=return_selection_count,
    )
    selection_count = len(selection_pairs)
    minimum_complete = (
        selection_count
        if min_complete_selections is None
        else max(1, min(min_complete_selections, selection_count))
    )
    bounded_date_range_count = max(1, min(date_range_count, 6))
    smoke_intents = synthetic_smoke_intents(date_range_count=bounded_date_range_count)
    closed_diagnostic_tabs = await _close_stale_cdp_health_tabs(
        adapter=adapter,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        task_trace=task_manager.child("diagnostic-tab-cleanup", run_id=run_id),
        artifact_prefix="preflight-cdp-health-tab",
        source_surface="cdp:page-close:preflight-cdp-health-tab",
    )
    if closed_diagnostic_tabs:
        warnings.append(f"closed {len(closed_diagnostic_tabs)} stale cdp health diagnostic tab(s)")

    consent_payload = await _seed_google_consent(
        adapter=adapter,
        browser_mode=browser_mode,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        consent_choice=consent_choice,
        timeout_seconds=timeout_seconds,
        max_tabs=max_tabs,
        task_trace=task_manager.child("consent", run_id=run_id),
    )
    if consent_payload["status"] not in {"ok", "skipped"}:
        payload = {
            "status": "blocked",
            "stop_state": consent_payload["status"],
            "browser_mode": browser_mode,
            "preflight_route": "JFK-SFO",
            "consent": consent_payload,
            "date_range_count": bounded_date_range_count,
            "candidate_count": len(smoke_intents),
            "closed_diagnostic_tabs": closed_diagnostic_tabs,
            "closed_diagnostic_tab_count": len(closed_diagnostic_tabs),
            "search": None,
            "selections": [],
            "warnings": [*warnings, "preflight could not settle Google consent"],
        }
        return await _finalize_preflight_payload(
            adapter=adapter,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            run_id=run_id,
            task_trace=root_trace,
            exit_code=4,
            payload=payload,
        )

    route_attempts: list[dict[str, Any]] = []
    date_range_results: list[dict[str, Any]] = []
    completed_date_ranges: set[int] = set()
    date_range_attempt_counts: dict[int, int] = {}
    last_blocked_payload: dict[str, Any] | None = None
    last_exit = 4

    for route_number, intent in enumerate(smoke_intents, start=1):
        date_range_index = int(intent.get("preflight_date_range_index") or 1)
        if date_range_index in completed_date_ranges:
            continue
        date_range_attempt_counts[date_range_index] = (
            date_range_attempt_counts.get(date_range_index, 0) + 1
        )
        date_range_attempt_number = date_range_attempt_counts[date_range_index]
        route_label = _intent_route_label(intent)
        route_slug = route_label.lower().replace("-", "_")
        route_trace = task_manager.child(
            f"smoke-candidate-{route_number:02d}",
            run_id=f"{run_id}-route-{route_number:02d}",
        )
        intent_path = run_root / f"preflight-intent-route-{route_number:02d}-{route_slug}.json"
        _write_json(intent_path, intent)
        artifacts.append(str(intent_path))
        source_surfaces.append("gflights:preflight:synthetic-intent")

        search_exit, search_payload, search_attempts = await _run_preflight_search_with_retry(
            input_json=intent_path,
            project_root=state.root,
            browser_mode=browser_mode,
            consent_choice=consent_choice,
            timeout_seconds=timeout_seconds,
            search_deadline_seconds=search_deadline_seconds,
            max_tabs=max_tabs,
            top_k=top_k,
            base_run_id=f"{run_id}-route-{route_number:02d}-search",
            adapter=adapter,
            task_trace=route_trace.child(
                "search", run_id=f"{run_id}-route-{route_number:02d}-search"
            ),
        )
        last_exit = search_exit or 4
        search_artifact = run_root / f"preflight-search-route-{route_number:02d}-{route_slug}.json"
        if isinstance(search_payload, dict):
            search_payload = dict(search_payload)
            diagnostics = dict(search_payload.get("diagnostics") or {})
            diagnostics["preflight_search_attempts"] = search_attempts
            diagnostics["preflight_search_attempt_count"] = len(search_attempts)
            search_payload["diagnostics"] = diagnostics
        _write_json(search_artifact, search_payload)
        artifacts.append(str(search_artifact))
        source_surfaces.append("gflights:preflight:search")

        route_attempt: dict[str, Any] = {
            "route": route_label,
            "query_id": intent.get("query_id"),
            "departure_date": (intent.get("departure_window") or {}).get("start"),
            "return_date": (intent.get("return_window") or {}).get("start"),
            "date_range_index": date_range_index,
            "search_exit_code": search_exit,
            "search_status": search_payload.get("status")
            if isinstance(search_payload, dict)
            else "unknown",
            "search_stop_state": search_payload.get("stop_state")
            if isinstance(search_payload, dict)
            else "search_failed",
            "search_attempt_count": len(search_attempts),
        }
        if search_attempts:
            route_attempt["search_retryable"] = bool(search_attempts[-1].get("retryable"))
            route_attempt["search_deadline_seconds"] = search_attempts[-1].get(
                "search_deadline_seconds"
            )

        if (
            search_exit != 0
            or not isinstance(search_payload, dict)
            or search_payload.get("status") != "ok"
        ):
            route_attempt["status"] = "search_blocked"
            route_attempts.append(route_attempt)
            last_blocked_payload = {
                "status": "blocked",
                "stop_state": search_payload.get("stop_state")
                if isinstance(search_payload, dict)
                else "search_failed",
                "browser_mode": browser_mode,
                "preflight_route": route_label,
                "consent": consent_payload,
                "date_range_count": bounded_date_range_count,
                "complete_date_range_count": len(completed_date_ranges),
                "date_range_results": date_range_results,
                "candidate_count": len(smoke_intents),
                "closed_diagnostic_tabs": closed_diagnostic_tabs,
                "closed_diagnostic_tab_count": len(closed_diagnostic_tabs),
                "search": search_payload,
                "selections": [],
                "warnings": [*warnings, "preflight search did not return visible fare rows"],
                "diagnostics": {"route_attempts": route_attempts},
                "evidence": _evidence(run_id, artifacts, source_surfaces),
            }
            if not _preflight_route_fallback_allowed(search_exit, search_payload):
                break
            continue

        search_url = str(search_payload.get("target_url") or "")
        bounded_selection_concurrency = max(1, min(selection_concurrency, selection_count, 7))
        selection_started = perf_counter()
        selection_manager = AsyncCrawlTaskManager(
            root_trace=route_trace.child("selection-fanout", run_id=run_id),
            concurrency=bounded_selection_concurrency,
        )

        async def select_combination(
            index: int,
            pair: tuple[int, int],
            child_trace: TaskTrace,
        ) -> dict[str, Any]:
            outbound_rank, return_rank = pair
            return await _select_preflight_combination(
                search_url=search_url,
                state_root=state.root,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_id=(
                    f"{run_id}-route-{route_number:02d}"
                    f"-selection-o{outbound_rank:02d}-r{return_rank:02d}"
                ),
                combination_index=index,
                outbound_rank=outbound_rank,
                return_rank=return_rank,
                max_tabs=max_tabs,
                task_trace=child_trace,
            )

        selection_results = await selection_manager.map_ordered(
            selection_pairs,
            task_name="selection-combination",
            run_id_for_item=lambda _index, pair: (
                f"{run_id}-route-{route_number:02d}-selection-o{pair[0]:02d}-r{pair[1]:02d}"
            ),
            worker=select_combination,
        )
        selections = [item.value for item in selection_results]
        selection_elapsed_seconds = round(perf_counter() - selection_started, 3)
        for item in selections:
            outbound_rank = int(item["outbound_rank"])
            return_rank = int(item["return_rank"])
            selection_payload = item.pop("payload")
            selection_artifact = run_root / (
                f"preflight-route-{route_number:02d}-{route_slug}"
                f"-selection-o{outbound_rank:02d}-r{return_rank:02d}.json"
            )
            _write_json(selection_artifact, selection_payload)
            artifacts.append(str(selection_artifact))
            source_surfaces.append("gflights:preflight:selection")

        complete_count = sum(1 for item in selections if _has_complete_booking_selection(item))
        passed = complete_count >= minimum_complete
        route_attempt.update(
            {
                "status": "ok" if passed else "selection_blocked",
                "outbound_selection_count": outbound_selection_count,
                "return_selection_count": return_selection_count,
                "selection_count": selection_count,
                "minimum_complete_selection_count": minimum_complete,
                "complete_selection_count": complete_count,
            }
        )
        route_attempts.append(route_attempt)

        route_warnings = list(warnings)
        if date_range_attempt_number > 1:
            date_range_context = (
                f" for date range {date_range_index}" if bounded_date_range_count > 1 else ""
            )
            route_warnings.append(
                f"preflight used fallback synthetic route {route_label}{date_range_context} "
                f"after {date_range_attempt_number - 1} earlier route(s)"
            )
        if complete_count < selection_count:
            route_warnings.append(
                f"preflight selected {complete_count}/{selection_count} requested outbound/return combinations through to bookable options; "
                f"minimum required is {minimum_complete}"
            )
        if bounded_date_range_count > 1:
            route_warnings.append(
                f"preflight completed {len(completed_date_ranges) + int(passed)}/{bounded_date_range_count} requested synthetic date ranges"
            )

        payload = {
            "status": "ok" if passed else "blocked",
            "browser_mode": browser_mode,
            "preflight_route": route_label,
            "privacy": "synthetic public route; one adult; no personal itinerary or checkout data",
            "consent": consent_payload,
            "search": {
                "status": search_payload.get("status"),
                "target_url": search_url,
                "result_count": len(search_payload.get("results") or []),
                "top_ranked": search_payload.get("rankings") or {},
                "attempts": search_attempts,
            },
            "outbound_selection_count": outbound_selection_count,
            "return_selection_count": return_selection_count,
            "selection_count": selection_count,
            "date_range_count": bounded_date_range_count,
            "complete_date_range_count": len(completed_date_ranges) + int(passed),
            "date_range_results": date_range_results,
            "candidate_count": len(smoke_intents),
            "closed_diagnostic_tabs": closed_diagnostic_tabs,
            "closed_diagnostic_tab_count": len(closed_diagnostic_tabs),
            "minimum_complete_selection_count": minimum_complete,
            "selection_concurrency": bounded_selection_concurrency,
            "selection_elapsed_seconds": selection_elapsed_seconds,
            "complete_selection_count": complete_count,
            "selections": selections,
            "warnings": route_warnings,
            "diagnostics": {"route_attempts": route_attempts},
            "evidence": _evidence(run_id, artifacts, source_surfaces),
        }
        if passed:
            date_range_results.append(
                {
                    "date_range_index": date_range_index,
                    "route": route_label,
                    "departure_date": route_attempt.get("departure_date"),
                    "return_date": route_attempt.get("return_date"),
                    "outbound_selection_count": outbound_selection_count,
                    "return_selection_count": return_selection_count,
                    "selection_count": selection_count,
                    "minimum_complete_selection_count": minimum_complete,
                    "complete_selection_count": complete_count,
                    "status": "ok",
                }
            )
            completed_date_ranges.add(date_range_index)
            payload["complete_date_range_count"] = len(completed_date_ranges)
            payload["date_range_results"] = list(date_range_results)
            if len(completed_date_ranges) >= bounded_date_range_count:
                return await _finalize_preflight_payload(
                    adapter=adapter,
                    browser_mode=browser_mode,
                    timeout_seconds=timeout_seconds,
                    run_root=run_root,
                    executed=executed,
                    artifacts=artifacts,
                    source_surfaces=source_surfaces,
                    run_id=run_id,
                    task_trace=root_trace,
                    exit_code=0,
                    payload=payload,
                )
            last_blocked_payload = payload
            continue
        last_blocked_payload = payload

    if last_blocked_payload is None:
        last_blocked_payload = {
            "status": "blocked",
            "stop_state": "preflight_route_candidates_exhausted",
            "browser_mode": browser_mode,
            "preflight_route": "",
            "consent": consent_payload,
            "date_range_count": bounded_date_range_count,
            "complete_date_range_count": len(completed_date_ranges),
            "date_range_results": date_range_results,
            "candidate_count": len(smoke_intents),
            "closed_diagnostic_tabs": closed_diagnostic_tabs,
            "closed_diagnostic_tab_count": len(closed_diagnostic_tabs),
            "search": None,
            "selections": [],
            "warnings": [*warnings, "preflight found no usable synthetic route"],
            "diagnostics": {"route_attempts": route_attempts},
        }
    elif 0 < len(completed_date_ranges) < bounded_date_range_count:
        last_blocked_payload["status"] = "blocked"
        last_blocked_payload["stop_state"] = "preflight_date_ranges_incomplete"
        last_blocked_payload["complete_date_range_count"] = len(completed_date_ranges)
        last_blocked_payload["date_range_results"] = date_range_results
        last_exit = 4
    return await _finalize_preflight_payload(
        adapter=adapter,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        run_id=run_id,
        task_trace=root_trace,
        exit_code=last_exit or 4,
        payload=last_blocked_payload,
    )


async def run_headless_heal(
    *,
    project_root: Path | None = None,
    browser_mode: BrowserMode = "headless",
    consent_choice: ConsentChoice = "accept-all",
    close_google_flights_tabs: bool = True,
    repair: bool = True,
    restart_daemon: bool = False,
    max_tabs: int | None = None,
    timeout_seconds: float = 45.0,
    adapter: CdpAdapter | None = None,
    task_trace: TaskTrace | None = None,
) -> tuple[int, dict[str, Any]]:
    """Repair the managed browser runtime and settle Google consent.

    This is a lightweight ceremony intended for long crawlers before starting a
    large fanout, or after a burst of transient Google page errors.
    """

    state = init_app_state(project_root)
    run_id = _new_run_id().replace("gf-preflight-", "gf-headless-heal-")
    run_root = state.run_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    adapter = adapter or CdpAdapter(max_tabs=max_tabs, allow_over_budget=True)
    task_trace = task_trace or new_task_trace(
        command="gflights.preflight.headless-heal",
        name="headless-heal",
        run_id=run_id,
    )

    executed: list[dict[str, Any]] = []
    artifacts: list[str] = []
    source_surfaces: list[str] = []
    warnings: list[str] = []
    closed_targets: list[dict[str, str]] = []
    closed_diagnostic_targets: list[dict[str, str]] = []
    restart_payload: dict[str, Any] | None = None

    health_before = await _run_step(
        adapter=adapter,
        args=["daemon", "health"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 30.0),
        run_root=run_root,
        artifact_name="headless-heal-health-before.json",
        source_surface="cdp:daemon-health:headless-heal-before",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )

    if restart_daemon:
        restart_result = await _run_step(
            adapter=adapter,
            args=["daemon", "restart", "--reconnect", "30s"],
            browser_mode=browser_mode,
            timeout_seconds=max(timeout_seconds, 60.0),
            run_root=run_root,
            artifact_name="headless-heal-daemon-restart.json",
            source_surface="cdp:daemon-restart:headless-heal",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
        )
        restart_payload = {
            "status": restart_result.status,
            "state": _health_state(restart_result.json_payload or {}),
        }

    pages_result = await _run_step(
        adapter=adapter,
        args=["pages"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 30.0),
        run_root=run_root,
        artifact_name="headless-heal-pages-before.json",
        source_surface="cdp:pages:headless-heal-before",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    targets_to_close = (
        _google_flights_page_targets(pages_result.json_payload or {})
        if close_google_flights_tabs
        else []
    )
    closed_targets.extend(
        await _close_page_targets(
            adapter=adapter,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            targets=targets_to_close,
            pages_payload=pages_result.json_payload or {},
            task_trace=task_trace.child("stale-google-flights-tab-cleanup", run_id=run_id),
            artifact_prefix="headless-heal-close",
            source_surface="cdp:page-close:headless-heal-google-flights",
        )
    )
    closed_diagnostic_targets.extend(
        await _close_page_targets(
            adapter=adapter,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            targets=_cdp_health_page_targets(
                pages_result.json_payload or {},
                browser_mode=browser_mode,
            ),
            pages_payload=pages_result.json_payload or {},
            task_trace=task_trace.child("stale-cdp-health-tab-cleanup", run_id=run_id),
            artifact_prefix="headless-heal-cdp-health-tab-before",
            source_surface="cdp:page-close:headless-heal-cdp-health-tab",
        )
    )

    health_check_args = ["daemon", "health-check"]
    if repair:
        health_check_args.append("--repair")
    health_check_args.extend(["--out-dir", str(run_root / "cdp-health-check")])
    health_check = await _run_step(
        adapter=adapter,
        args=health_check_args,
        browser_mode=browser_mode,
        timeout_seconds=max(timeout_seconds, 60.0),
        run_root=run_root,
        artifact_name="headless-heal-health-check.json",
        source_surface="cdp:daemon-health-check:headless-heal",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    pages_after_health_check = await _run_step(
        adapter=adapter,
        args=["pages"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 30.0),
        run_root=run_root,
        artifact_name="headless-heal-pages-after-health-check.json",
        source_surface="cdp:pages:headless-heal-after-health-check",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    closed_diagnostic_targets.extend(
        await _close_page_targets(
            adapter=adapter,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            targets=_cdp_health_page_targets(
                pages_after_health_check.json_payload or {},
                browser_mode=browser_mode,
            ),
            pages_payload=pages_after_health_check.json_payload or {},
            task_trace=task_trace.child("post-health-check-tab-cleanup", run_id=run_id),
            artifact_prefix="headless-heal-cdp-health-tab-after",
            source_surface="cdp:page-close:headless-heal-cdp-health-tab",
        )
    )

    consent_payload = await _seed_google_consent(
        adapter=adapter,
        browser_mode=browser_mode,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        consent_choice=consent_choice,
        timeout_seconds=timeout_seconds,
        max_tabs=max_tabs,
        task_trace=task_trace.child("consent", run_id=run_id),
    )
    if consent_payload["status"] not in {"ok", "skipped"}:
        warnings.append("headless heal could not settle Google consent")

    cookies_result = await _run_step(
        adapter=adapter,
        args=["storage", "cookies", "list", "--url", "https://www.google.com"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 20.0),
        run_root=run_root,
        artifact_name="headless-heal-google-cookies.json",
        source_surface="cdp:storage-cookies:list-google",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    google_cookie_names = _cookie_names(cookies_result.json_payload or {})
    if consent_choice != "skip" and not {"SOCS", "CONSENT"}.intersection(google_cookie_names):
        warnings.append("Google consent cookie was not visible after consent seeding")

    health_after = await _run_step(
        adapter=adapter,
        args=["daemon", "health"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 30.0),
        run_root=run_root,
        artifact_name="headless-heal-health-after.json",
        source_surface="cdp:daemon-health:headless-heal-after",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )

    _write_json(run_root / "command-log.json", executed)
    artifacts.append(str(run_root / "command-log.json"))
    _write_task_trace(run_root=run_root, artifacts=artifacts, task_trace=task_trace)
    health_after_state = _health_state(health_after.json_payload or {})
    health_check_state = _health_state(health_check.json_payload or {})
    health_after_ok = health_after_state in {"healthy", "ok", "running", ""}
    health_check_ok = health_check.status == "ok" or health_check_state in {
        "healthy",
        "ok",
        "running",
    }
    if not health_check_ok and health_after_ok:
        warnings.append(
            "cdp daemon health-check returned "
            f"{health_check.status}"
            + (f" ({health_check_state})" if health_check_state else "")
            + "; final daemon health is healthy"
        )
    passed = consent_payload["status"] in {"ok", "skipped"} and health_after_ok
    payload = {
        "status": "ok" if passed else "blocked",
        "browser_mode": browser_mode,
        "repair_requested": repair,
        "restart_daemon_requested": restart_daemon,
        "restart_daemon": restart_payload,
        "closed_google_flights_tabs": closed_targets,
        "closed_google_flights_tab_count": len(closed_targets),
        "closed_diagnostic_tabs": closed_diagnostic_targets,
        "closed_diagnostic_tab_count": len(closed_diagnostic_targets),
        "consent": consent_payload,
        "google_cookie_names": sorted(google_cookie_names),
        "health_before": {
            "status": health_before.status,
            "state": _health_state(health_before.json_payload or {}),
        },
        "health_check": {
            "status": health_check.status,
            "state": health_check_state,
        },
        "health_after": {
            "status": health_after.status,
            "state": health_after_state,
        },
        "warnings": warnings,
        "evidence": _evidence(run_id, artifacts, source_surfaces, task_trace=task_trace),
    }
    return (0 if passed else 4), payload


async def _run_preflight_search_with_retry(
    *,
    input_json: Path,
    project_root: Path,
    browser_mode: BrowserMode,
    consent_choice: ConsentChoice,
    timeout_seconds: float,
    search_deadline_seconds: float | None,
    max_tabs: int | None,
    top_k: int,
    base_run_id: str,
    adapter: CdpAdapter,
    task_trace: TaskTrace,
    retry_limit: int = 2,
) -> tuple[int, dict[str, Any] | list[dict[str, Any]], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    last_exit = 6
    last_payload: dict[str, Any] | list[dict[str, Any]] = {
        "status": "tool_error",
        "error": "preflight search did not run",
    }
    per_attempt_deadline = (
        max(0.001, search_deadline_seconds)
        if search_deadline_seconds is not None
        else max(60.0, min(timeout_seconds * 2.5, 105.0))
    )
    for attempt in range(1, retry_limit + 2):
        run_id = base_run_id if attempt == 1 else f"{base_run_id}-attempt-{attempt:02d}"
        attempt_trace = task_trace.child(f"search-attempt-{attempt:02d}", run_id=run_id)
        try:
            search_exit, search_payload = await asyncio.wait_for(
                run_live_search(
                    input_json=input_json,
                    project_root=project_root,
                    browser_mode=browser_mode,
                    run_id=run_id,
                    timeout_seconds=timeout_seconds,
                    batch_concurrency=1,
                    managed_tab_policy="new",
                    max_tabs=max_tabs,
                    rank_objectives=["balanced", "cheapest", "fastest", "least_layover"],
                    top_k=top_k,
                    task_trace=attempt_trace,
                ),
                timeout=per_attempt_deadline,
            )
        except asyncio.TimeoutError:
            search_exit = 6
            search_payload = {
                "status": "tool_error",
                "stop_state": "preflight_search_timeout",
                "error": (
                    "synthetic Google Flights search exceeded "
                    f"{per_attempt_deadline:g}s preflight search deadline"
                ),
                "results": [],
                "warnings": [
                    "preflight search timed out before row selection; run headless-heal or retry later"
                ],
            }
        last_exit = search_exit
        last_payload = search_payload
        retryable = _preflight_search_retryable(search_exit, search_payload)
        attempts.append(
            {
                "attempt": attempt,
                "run_id": run_id,
                "exit_code": search_exit,
                "status": search_payload.get("status")
                if isinstance(search_payload, dict)
                else "unknown",
                "stop_state": search_payload.get("stop_state")
                if isinstance(search_payload, dict)
                else "",
                "retryable": retryable,
                "search_deadline_seconds": per_attempt_deadline,
            }
        )
        if search_exit == 0 or not retryable or attempt > retry_limit:
            return last_exit, last_payload, attempts
        heal_exit, heal_payload = await run_headless_heal(
            project_root=project_root,
            browser_mode=browser_mode,
            consent_choice=consent_choice,
            close_google_flights_tabs=True,
            repair=True,
            max_tabs=max_tabs,
            timeout_seconds=timeout_seconds,
            adapter=adapter,
            task_trace=attempt_trace.child("headless-heal", run_id=f"{run_id}-heal"),
        )
        attempts[-1]["heal_before_next_attempt"] = {
            "exit_code": heal_exit,
            "status": heal_payload.get("status") if isinstance(heal_payload, dict) else "unknown",
            "closed_google_flights_tab_count": heal_payload.get("closed_google_flights_tab_count")
            if isinstance(heal_payload, dict)
            else None,
            "evidence": heal_payload.get("evidence") if isinstance(heal_payload, dict) else None,
        }
        await asyncio.sleep(min(3.0, 0.75 * attempt))
    return last_exit, last_payload, attempts


def _preflight_search_retryable(
    search_exit: int,
    search_payload: dict[str, Any] | list[dict[str, Any]],
) -> bool:
    if search_exit == 0 or not isinstance(search_payload, dict):
        return False
    if search_payload.get("stop_state") == "google_page_error":
        return True
    if search_payload.get("stop_state") == "preflight_search_timeout":
        return False
    text = " ".join(
        str(value)
        for value in [
            search_payload.get("status"),
            search_payload.get("error"),
            json.dumps(search_payload.get("warnings") or []),
            json.dumps(search_payload.get("unsupported") or []),
        ]
        if value
    ).casefold()
    return "oops, something went wrong" in text or "google_page_error" in text


def _preflight_route_fallback_allowed(
    search_exit: int,
    search_payload: dict[str, Any] | list[dict[str, Any]],
) -> bool:
    if search_exit == 0:
        return False
    if not isinstance(search_payload, dict):
        return True
    stop_state = str(search_payload.get("stop_state") or "")
    status = str(search_payload.get("status") or "")
    if stop_state in {"blocked", "login_required", "preflight_search_timeout"}:
        return False
    if status in {"blocked", "login_required"}:
        return False
    return True


def _has_complete_booking_selection(item: dict[str, Any]) -> bool:
    booking_url = item.get("booking_url")
    booking_options = item.get("booking_options")
    return (
        item.get("status") == "ok"
        and isinstance(booking_url, str)
        and "/travel/flights/booking" in booking_url
        and isinstance(booking_options, list)
        and len(booking_options) > 0
    )


def _preflight_selection_pairs(
    *,
    outbound_selection_count: int,
    return_selection_count: int,
) -> list[tuple[int, int]]:
    return [
        (outbound_rank, return_rank)
        for outbound_rank in range(1, outbound_selection_count + 1)
        for return_rank in range(1, return_selection_count + 1)
    ]


async def _select_preflight_combination(
    *,
    search_url: str,
    state_root: Path,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_id: str,
    combination_index: int,
    outbound_rank: int,
    return_rank: int,
    max_tabs: int | None,
    task_trace: TaskTrace,
) -> dict[str, Any]:
    selection_exit, selection_payload = await run_live_itinerary_selection(
        search_url=search_url,
        project_root=state_root,
        run_id=run_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        row_rank=outbound_rank,
        outbound_row_rank=outbound_rank,
        return_row_rank=return_rank,
        max_tabs=max_tabs,
        task_trace=task_trace,
    )
    booking_options = selection_payload.get("booking_options") or []
    booking_options_status = (
        "available"
        if booking_options
        else "missing"
        if selection_payload.get("status") == "ok"
        else "not_reached"
    )
    return {
        "combination_index": combination_index,
        "outbound_rank": outbound_rank,
        "return_rank": return_rank,
        "exit_code": selection_exit,
        "status": selection_payload.get("status"),
        "booking_url": selection_payload.get("booking_url"),
        "selected_outbound": selection_payload.get("selected_outbound"),
        "selected_return": selection_payload.get("selected_return"),
        "booking_options_status": booking_options_status,
        "booking_options": booking_options,
        "warnings": selection_payload.get("warnings") or [],
        "evidence": selection_payload.get("evidence"),
        "payload": selection_payload,
    }


async def _seed_google_consent(
    *,
    adapter: CdpAdapter,
    browser_mode: BrowserMode,
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
    consent_choice: ConsentChoice,
    timeout_seconds: float,
    max_tabs: int | None,
    task_trace: TaskTrace,
) -> dict[str, Any]:
    if consent_choice == "skip":
        return {"status": "skipped", "choice": consent_choice}

    managed_tab_trace = task_trace.child("managed-tab", run_id=task_trace.run_id)
    url = "https://www.google.com/travel/flights?hl=en&curr=USD"
    open_result = await _run_step(
        adapter=adapter,
        args=["open", url],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="preflight-open-google-flights.json",
        source_surface="cdp:open:preflight-google-flights",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        task_trace=managed_tab_trace,
    )
    page_id = open_result_page_id(open_result)
    if page_id:
        managed_tab_trace.record_target(page_id)
    warnings: list[str] = []
    recovered_warning = recoverable_open_page_warning(open_result, page_id)
    if recovered_warning:
        warnings.append(recovered_warning)
    if open_result.status != "ok" and not page_id:
        return {
            "status": open_result.stop_state or "consent_open_failed",
            "choice": consent_choice,
            "needed": False,
            "clicked": False,
            "locator": {
                "primary": {"by": "role", "role": "button", "exact": True},
                "fallback": "visible button/[role=button] exact text",
                "label": "Reject all" if consent_choice == "reject-all" else "Accept all",
                "used": "open_failed",
            },
            "before_text_length": 0,
            "after_text_length": 0,
            "close_status": "not_run_no_page_id",
            "max_tabs": max_tabs,
            "warnings": warnings,
        }
    await _run_step(
        adapter=adapter,
        args=["wait", "load-state", "domcontentloaded", "--target", page_id],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 20.0),
        run_root=run_root,
        artifact_name="preflight-consent-load-state.json",
        source_surface="cdp:wait:preflight-consent-load-state",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        task_trace=task_trace,
    )
    await _run_step(
        adapter=adapter,
        args=["wait", "eval", BODY_READY_JS, "--target", page_id],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 20.0),
        run_root=run_root,
        artifact_name="preflight-consent-body-ready.json",
        source_surface="cdp:wait:preflight-consent-body-ready",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        task_trace=task_trace,
    )
    text_result = await _run_step(
        adapter=adapter,
        args=["text", "body", "--target", page_id, "--limit", "0"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 15.0),
        run_root=run_root,
        artifact_name="preflight-consent-text-before.json",
        source_surface="cdp:text:preflight-consent-before",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        task_trace=task_trace,
    )
    before_text = _visible_text(text_result.json_payload or {})
    needs_consent = "Before you continue to Google" in before_text or "Accept all" in before_text
    clicked = False
    click_strategy = "not_needed"
    if needs_consent:
        label = "Reject all" if consent_choice == "reject-all" else "Accept all"
        click_result = await _run_step(
            adapter=adapter,
            args=[
                "click",
                label,
                "--by",
                "role",
                "--role",
                "button",
                "--exact",
                "--target",
                page_id,
                "--wait-url-contains",
                "google.com/travel/flights",
            ],
            browser_mode=browser_mode,
            timeout_seconds=min(timeout_seconds, 30.0),
            run_root=run_root,
            artifact_name="preflight-consent-click.json",
            source_surface="cdp:click:preflight-consent",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            task_trace=task_trace,
        )
        clicked = click_result.status == "ok"
        click_strategy = "role:button"
        if not clicked:
            eval_click_result = await _run_step(
                adapter=adapter,
                args=["eval", consent_click_js(label), "--target", page_id],
                browser_mode=browser_mode,
                timeout_seconds=min(timeout_seconds, 15.0),
                run_root=run_root,
                artifact_name="preflight-consent-click-dom-fallback.json",
                source_surface="cdp:eval:preflight-consent-dom-fallback",
                executed=executed,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
                task_trace=task_trace,
            )
            value = _result_value(eval_click_result.json_payload or {})
            clicked = bool(isinstance(value, dict) and value.get("clicked"))
            click_strategy = "dom-visible-button-exact" if clicked else "failed"
        await _run_step(
            adapter=adapter,
            args=["wait", "load-state", "domcontentloaded", "--target", page_id],
            browser_mode=browser_mode,
            timeout_seconds=min(timeout_seconds, 20.0),
            run_root=run_root,
            artifact_name="preflight-consent-post-click-load-state.json",
            source_surface="cdp:wait:preflight-consent-post-click-load-state",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            task_trace=task_trace,
        )
        await _run_step(
            adapter=adapter,
            args=["wait", "eval", BODY_READY_JS, "--target", page_id],
            browser_mode=browser_mode,
            timeout_seconds=min(timeout_seconds, 20.0),
            run_root=run_root,
            artifact_name="preflight-consent-post-click-body-ready.json",
            source_surface="cdp:wait:preflight-consent-post-click-body-ready",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            task_trace=task_trace,
        )

    after_text_result = await _run_step(
        adapter=adapter,
        args=["text", "body", "--target", page_id, "--limit", "0"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 15.0),
        run_root=run_root,
        artifact_name="preflight-consent-text-after.json",
        source_surface="cdp:text:preflight-consent-after",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        task_trace=task_trace,
    )
    after_text = _visible_text(after_text_result.json_payload or {})
    settled = "Before you continue to Google" not in after_text and "Accept all" not in after_text
    close_status = "not_run"
    if page_id and managed_tab_trace.owns_target(page_id):
        close_result = await close_managed_page(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=min(timeout_seconds, 10.0),
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            warnings=warnings,
            task_trace=managed_tab_trace,
            artifact_name="preflight-consent-tab-close.json",
            source_surface="cdp:page-close:preflight-consent",
        )
        close_status = close_result.status if close_result is not None else "not_run_no_page_id"
    elif page_id:
        close_status = "skipped_unowned_tab"
    return {
        "status": "ok" if settled else "consent_required",
        "choice": consent_choice,
        "needed": needs_consent,
        "clicked": clicked,
        "locator": {
            "primary": {"by": "role", "role": "button", "exact": True},
            "fallback": "visible button/[role=button] exact text",
            "label": "Reject all" if consent_choice == "reject-all" else "Accept all",
            "used": click_strategy,
        },
        "before_text_length": len(before_text),
        "after_text_length": len(after_text),
        "close_status": close_status,
        "max_tabs": max_tabs,
        "task_trace": task_trace.as_dict(),
        "managed_tab_task": managed_tab_trace.as_dict(),
        "target_task_ids": task_trace.ownership_map(),
        "warnings": warnings,
    }


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
    task_trace: TaskTrace | None = None,
) -> CdpResult:
    result = await adapter.run_json(
        args,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
    )
    artifact_path = run_root / artifact_name
    _write_json(artifact_path, _result_artifact(result, task_trace=task_trace))
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
            "task_trace": task_trace.as_dict() if task_trace is not None else None,
        }
    )
    return result


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


def _result_value(payload: dict[str, Any]) -> Any:
    result = payload.get("result")
    if isinstance(result, dict) and "value" in result:
        return result["value"]
    if "value" in payload:
        return payload["value"]
    return None


def _google_flights_page_targets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return []
    targets: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        url = str(page.get("url") or "")
        if "google.com/travel/flights" not in url:
            continue
        target_id = page.get("id") or page.get("targetId")
        if not isinstance(target_id, str) or not target_id:
            continue
        targets.append(
            {
                "id": target_id,
                "url": url,
                "title": str(page.get("title") or ""),
                "attached": page.get("attached"),
            }
        )
    return targets


def _cdp_health_page_targets(
    payload: dict[str, Any],
    *,
    browser_mode: BrowserMode,
) -> list[dict[str, Any]]:
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return []
    payload_browser_mode = _pages_browser_mode(payload) or browser_mode
    targets: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        url = str(page.get("url") or "")
        title = str(page.get("title") or "")
        is_named_diagnostic = "data-cdp-health" in url or "cdp-headless-health" in title
        is_stale_blank_diagnostic = (
            payload_browser_mode == "headless"
            and url == "about:blank"
            and title in {"", "about:blank"}
            and page.get("attached") is False
        )
        if not is_named_diagnostic and not is_stale_blank_diagnostic:
            continue
        target_id = page.get("id") or page.get("targetId")
        if not isinstance(target_id, str) or not target_id:
            continue
        targets.append(
            {
                "id": target_id,
                "url": url,
                "title": title,
                "attached": page.get("attached"),
            }
        )
    return targets


def _pages_browser_mode(payload: dict[str, Any]) -> str:
    budget = payload.get("budget")
    if isinstance(budget, dict) and isinstance(budget.get("browser_mode"), str):
        return budget["browser_mode"]
    return ""


async def _close_stale_cdp_health_tabs(
    *,
    adapter: CdpAdapter,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
    task_trace: TaskTrace,
    artifact_prefix: str,
    source_surface: str,
) -> list[dict[str, str]]:
    pages_result = await _run_step(
        adapter=adapter,
        args=["pages"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 30.0),
        run_root=run_root,
        artifact_name=f"{artifact_prefix}-pages-before.json",
        source_surface="cdp:pages:preflight-cdp-health-tab-cleanup",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        task_trace=task_trace,
    )
    return await _close_page_targets(
        adapter=adapter,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        targets=_cdp_health_page_targets(
            pages_result.json_payload or {},
            browser_mode=browser_mode,
        ),
        pages_payload=pages_result.json_payload or {},
        task_trace=task_trace,
        artifact_prefix=artifact_prefix,
        source_surface=source_surface,
    )


async def _close_stale_google_flights_tabs(
    *,
    adapter: CdpAdapter,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
    task_trace: TaskTrace,
    artifact_prefix: str,
    source_surface: str,
) -> list[dict[str, str]]:
    pages_result = await _run_step(
        adapter=adapter,
        args=["pages"],
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 30.0),
        run_root=run_root,
        artifact_name=f"{artifact_prefix}-pages-before.json",
        source_surface="cdp:pages:preflight-google-flights-tab-cleanup",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        task_trace=task_trace,
    )
    return await _close_page_targets(
        adapter=adapter,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        targets=_google_flights_page_targets(pages_result.json_payload or {}),
        pages_payload=pages_result.json_payload or {},
        task_trace=task_trace,
        artifact_prefix=artifact_prefix,
        source_surface=source_surface,
    )


async def _close_page_targets(
    *,
    adapter: CdpAdapter,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
    targets: list[dict[str, Any]],
    pages_payload: dict[str, Any],
    task_trace: TaskTrace,
    artifact_prefix: str,
    source_surface: str,
) -> list[dict[str, str]]:
    closed_targets: list[dict[str, str]] = []
    targets_to_close = list(targets)
    preserved_target = _preserve_headless_keepalive_target(
        targets_to_close,
        pages_payload=pages_payload,
        browser_mode=browser_mode,
    )
    if preserved_target is not None:
        targets_to_close = [
            target
            for target in targets_to_close
            if str(target.get("id") or "") != str(preserved_target.get("id") or "")
        ]
    if _would_close_all_pages(
        pages_payload,
        targets_to_close,
        browser_mode=browser_mode,
    ):
        open_result = await _run_step(
            adapter=adapter,
            args=["open", HEADLESS_KEEPALIVE_URL],
            browser_mode=browser_mode,
            timeout_seconds=min(timeout_seconds, 30.0),
            run_root=run_root,
            artifact_name=f"{artifact_prefix}-headless-keepalive-tab.json",
            source_surface="cdp:open:headless-keepalive-tab",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            task_trace=task_trace.child("headless-keepalive-tab", run_id=task_trace.run_id),
        )
        if open_result.status != "ok" and not open_result_page_id(open_result):
            fallback_preserved = _first_target(targets_to_close)
            if fallback_preserved is not None:
                targets_to_close = [
                    target
                    for target in targets_to_close
                    if str(target.get("id") or "") != str(fallback_preserved.get("id") or "")
                ]
    for index, target in enumerate(targets_to_close, start=1):
        target_id = str(target.get("id") or "")
        if not target_id:
            continue
        target_trace = task_trace.child(f"target-{index:02d}", run_id=task_trace.run_id)
        target_trace.record_target(target_id)
        close_result = await close_managed_page(
            adapter=adapter,
            page_id=target_id,
            browser_mode=browser_mode,
            timeout_seconds=min(timeout_seconds, 10.0),
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            task_trace=target_trace,
            artifact_name=f"{artifact_prefix}-{target_id}.json",
            source_surface=source_surface,
        )
        closed_targets.append(
            {
                "id": target_id,
                "url": str(target.get("url") or ""),
                "status": close_result.status if close_result is not None else "not_run",
            }
        )
    return closed_targets


def _preserve_headless_keepalive_target(
    targets: list[dict[str, Any]],
    *,
    pages_payload: dict[str, Any],
    browser_mode: BrowserMode,
) -> dict[str, Any] | None:
    if not _would_close_all_pages(pages_payload, targets, browser_mode=browser_mode):
        return None
    return next((target for target in targets if _is_inert_keepalive_page(target)), None)


def _would_close_all_pages(
    payload: dict[str, Any],
    targets: list[dict[str, Any]],
    *,
    browser_mode: BrowserMode,
) -> bool:
    if browser_mode != "headless":
        return False
    page_ids = _page_ids(payload)
    if not page_ids:
        return False
    target_ids = {str(target.get("id") or "") for target in targets if target.get("id")}
    return page_ids.issubset(target_ids)


def _page_ids(payload: dict[str, Any]) -> set[str]:
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return set()
    ids: set[str] = set()
    for page in pages:
        if not isinstance(page, dict):
            continue
        if page.get("type") not in {None, "page"}:
            continue
        target_id = page.get("id") or page.get("targetId")
        if isinstance(target_id, str) and target_id:
            ids.add(target_id)
    return ids


def _is_inert_keepalive_page(target: dict[str, Any]) -> bool:
    url = str(target.get("url") or "")
    title = str(target.get("title") or "")
    return (
        url in {HEADLESS_KEEPALIVE_URL, "about:blank"}
        and title in {"", "New Tab", "about:blank"}
        and target.get("attached") is False
    )


def _first_target(targets: list[dict[str, Any]]) -> dict[str, Any] | None:
    return targets[0] if targets else None


def _cookie_names(payload: dict[str, Any]) -> set[str]:
    cookies = payload.get("cookies")
    if isinstance(cookies, dict):
        cookies = cookies.get("cookies")
    if not isinstance(cookies, list):
        return set()
    return {
        str(cookie.get("name"))
        for cookie in cookies
        if isinstance(cookie, dict) and isinstance(cookie.get("name"), str)
    }


def _health_state(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("state"), str):
        return payload["state"]
    health = payload.get("health")
    if isinstance(health, dict) and isinstance(health.get("state"), str):
        return health["state"]
    daemon = payload.get("daemon")
    if isinstance(daemon, dict):
        daemon_health = daemon.get("health")
        if isinstance(daemon_health, dict) and isinstance(daemon_health.get("state"), str):
            return daemon_health["state"]
        if isinstance(daemon.get("state"), str):
            return daemon["state"]
    if payload.get("ok") is True:
        return "ok"
    return ""


def _result_artifact(
    result: CdpResult,
    *,
    task_trace: TaskTrace | None = None,
) -> dict[str, Any]:
    artifact = {
        "argv": result.argv,
        "browser_mode": result.browser_mode,
        "returncode": result.returncode,
        "status": result.status,
        "exit_code": result.exit_code,
        "stop_state": result.stop_state,
        "fallback": result.fallback,
        "error": result.error,
        "timeout": result.timeout,
        "json_payload": result.json_payload,
        "attempt_count": result.attempt_count,
        "max_attempts": result.max_attempts,
        "attempts": result.attempts or [],
    }
    if task_trace is not None:
        artifact["task_trace"] = task_trace.as_dict()
        artifact["target_task_ids"] = task_trace.ownership_map()
    return artifact


def _evidence(
    run_id: str,
    artifacts: list[str],
    source_surfaces: list[str],
    *,
    task_trace: TaskTrace | None = None,
) -> dict[str, Any]:
    evidence = {
        "run_id": run_id,
        "artifacts": artifacts,
        "source_surfaces": source_surfaces,
    }
    if task_trace is not None:
        evidence["task_trace"] = task_trace.as_dict()
        evidence["target_task_ids"] = task_trace.ownership_map()
    return evidence


def _write_task_trace(
    *,
    run_root: Path,
    artifacts: list[str],
    task_trace: TaskTrace,
) -> None:
    artifact_path = run_root / "task-trace.json"
    _write_json(
        artifact_path,
        {
            "task": task_trace.as_dict(),
            "target_task_ids": task_trace.ownership_map(),
        },
    )
    if str(artifact_path) not in artifacts:
        artifacts.append(str(artifact_path))


async def _finalize_preflight_payload(
    *,
    adapter: CdpAdapter,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
    run_id: str,
    task_trace: TaskTrace,
    exit_code: int,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    final_closed_google_tabs = await _close_stale_google_flights_tabs(
        adapter=adapter,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        task_trace=task_trace.child("final-google-flights-tab-cleanup", run_id=run_id),
        artifact_prefix="preflight-final-google-flights-tab",
        source_surface="cdp:page-close:preflight-final-google-flights-tab",
    )
    payload["final_closed_google_flights_tabs"] = final_closed_google_tabs
    payload["final_closed_google_flights_tab_count"] = len(final_closed_google_tabs)
    if final_closed_google_tabs:
        warnings = list(payload.get("warnings") or [])
        warnings.append(
            f"preflight final cleanup closed {len(final_closed_google_tabs)} Google Flights tab(s)"
        )
        payload["warnings"] = warnings
    _write_task_trace(run_root=run_root, artifacts=artifacts, task_trace=task_trace)
    _write_json(run_root / "command-log.json", executed)
    if str(run_root / "command-log.json") not in artifacts:
        artifacts.append(str(run_root / "command-log.json"))
    payload["evidence"] = _evidence(
        run_id,
        artifacts,
        source_surfaces,
        task_trace=task_trace,
    )
    return exit_code, payload


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _new_run_id() -> str:
    return "gf-preflight-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
