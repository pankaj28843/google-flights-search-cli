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
from gflights.live_search import run_live_search
from gflights.live_selection import run_live_itinerary_selection

ConsentChoice = Literal["reject-all", "accept-all", "skip"]

BODY_READY_JS = r"""
(() => {
  const text = (document.body && (document.body.innerText || document.body.textContent) || "")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > 0 ? "body_ready" : false;
})()
""".strip()


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


def synthetic_smoke_intent(today: date | None = None) -> dict[str, Any]:
    """Return a public, non-personal round-trip smoke intent.

    Dates are deliberately generated from the current date so the smoke test
    remains useful over time and does not encode a user's trip.
    """

    today = today or datetime.now(UTC).date()
    depart = today + timedelta(days=90)
    return_date = depart + timedelta(days=7)
    return {
        "query_id": f"preflight-jfk-sfo-{depart.isoformat()}-return-{return_date.isoformat()}",
        "origin": {"text": "JFK", "kind": "airport_code"},
        "destination": {"text": "SFO", "kind": "airport_code"},
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
    }


async def run_google_flights_preflight(
    *,
    project_root: Path | None = None,
    browser_mode: BrowserMode = "headless",
    consent_choice: ConsentChoice = "reject-all",
    top_k: int = 5,
    selection_concurrency: int = 3,
    max_tabs: int | None = None,
    timeout_seconds: float = 45.0,
    adapter: CdpAdapter | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run consent seeding plus full public route search/selection smoke test."""

    state = init_app_state(project_root)
    run_id = _new_run_id()
    run_root = state.run_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    adapter = adapter or CdpAdapter(max_tabs=max_tabs)

    executed: list[dict[str, Any]] = []
    artifacts: list[str] = []
    source_surfaces: list[str] = []
    warnings: list[str] = []

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
    )
    if consent_payload["status"] not in {"ok", "skipped"}:
        _write_json(run_root / "command-log.json", executed)
        artifacts.append(str(run_root / "command-log.json"))
        return 4, {
            "status": "blocked",
            "stop_state": consent_payload["status"],
            "browser_mode": browser_mode,
            "preflight_route": "JFK-SFO",
            "consent": consent_payload,
            "search": None,
            "selections": [],
            "warnings": [*warnings, "preflight could not settle Google consent"],
            "evidence": _evidence(run_id, artifacts, source_surfaces),
        }

    intent = synthetic_smoke_intent()
    intent_path = run_root / "preflight-intent.json"
    _write_json(intent_path, intent)
    artifacts.append(str(intent_path))
    source_surfaces.append("gflights:preflight:synthetic-intent")

    search_exit, search_payload, search_attempts = await _run_preflight_search_with_retry(
        input_json=intent_path,
        project_root=state.root,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        max_tabs=max_tabs,
        top_k=top_k,
        base_run_id=f"{run_id}-search",
    )
    search_artifact = run_root / "preflight-search.json"
    if isinstance(search_payload, dict):
        search_payload = dict(search_payload)
        diagnostics = dict(search_payload.get("diagnostics") or {})
        diagnostics["preflight_search_attempts"] = search_attempts
        diagnostics["preflight_search_attempt_count"] = len(search_attempts)
        search_payload["diagnostics"] = diagnostics
    _write_json(search_artifact, search_payload)
    artifacts.append(str(search_artifact))
    source_surfaces.append("gflights:preflight:search")
    if (
        search_exit != 0
        or not isinstance(search_payload, dict)
        or search_payload.get("status") != "ok"
    ):
        _write_json(run_root / "command-log.json", executed)
        artifacts.append(str(run_root / "command-log.json"))
        return search_exit or 4, {
            "status": "blocked",
            "stop_state": (search_payload or {}).get("stop_state")
            if isinstance(search_payload, dict)
            else "search_failed",
            "browser_mode": browser_mode,
            "preflight_route": "JFK-SFO",
            "consent": consent_payload,
            "search": search_payload,
            "selections": [],
            "warnings": [*warnings, "preflight search did not return visible fare rows"],
            "evidence": _evidence(run_id, artifacts, source_surfaces),
        }

    search_url = str(search_payload.get("target_url") or "")
    selection_count = max(1, min(top_k, 5))
    bounded_selection_concurrency = max(1, min(selection_concurrency, selection_count, 7))
    selection_started = perf_counter()
    selection_semaphore = asyncio.Semaphore(bounded_selection_concurrency)

    async def select_rank(rank: int) -> dict[str, Any]:
        async with selection_semaphore:
            return await _select_preflight_rank(
                search_url=search_url,
                state_root=state.root,
                browser_mode=browser_mode,
                timeout_seconds=timeout_seconds,
                run_id=f"{run_id}-selection-rank-{rank}",
                rank=rank,
                max_tabs=max_tabs,
            )

    selections = await asyncio.gather(
        *(select_rank(rank) for rank in range(1, selection_count + 1))
    )
    selection_elapsed_seconds = round(perf_counter() - selection_started, 3)
    for item in selections:
        rank = int(item["rank"])
        selection_payload = item.pop("payload")
        selection_artifact = run_root / f"preflight-selection-rank-{rank}.json"
        _write_json(selection_artifact, selection_payload)
        artifacts.append(str(selection_artifact))
        source_surfaces.append("gflights:preflight:selection")

    _write_json(run_root / "command-log.json", executed)
    artifacts.append(str(run_root / "command-log.json"))
    complete_count = sum(1 for item in selections if _has_complete_booking_selection(item))
    passed = complete_count == selection_count
    if not passed:
        warnings.append(
            f"preflight selected {complete_count}/{selection_count} requested rows through to booking"
        )
    payload = {
        "status": "ok" if passed else "blocked",
        "browser_mode": browser_mode,
        "preflight_route": "JFK-SFO",
        "privacy": "synthetic public route; one adult; no personal itinerary or checkout data",
        "consent": consent_payload,
        "search": {
            "status": search_payload.get("status"),
            "target_url": search_url,
            "result_count": len(search_payload.get("results") or []),
            "top_ranked": search_payload.get("rankings") or {},
        },
        "selection_count": selection_count,
        "selection_concurrency": bounded_selection_concurrency,
        "selection_elapsed_seconds": selection_elapsed_seconds,
        "complete_selection_count": complete_count,
        "selections": selections,
        "warnings": warnings,
        "evidence": _evidence(run_id, artifacts, source_surfaces),
    }
    return (0 if passed else 4), payload


async def _run_preflight_search_with_retry(
    *,
    input_json: Path,
    project_root: Path,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    max_tabs: int | None,
    top_k: int,
    base_run_id: str,
    retry_limit: int = 2,
) -> tuple[int, dict[str, Any] | list[dict[str, Any]], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    last_exit = 6
    last_payload: dict[str, Any] | list[dict[str, Any]] = {
        "status": "tool_error",
        "error": "preflight search did not run",
    }
    for attempt in range(1, retry_limit + 2):
        run_id = base_run_id if attempt == 1 else f"{base_run_id}-attempt-{attempt:02d}"
        search_exit, search_payload = await run_live_search(
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
        )
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
            }
        )
        if search_exit == 0 or not retryable or attempt > retry_limit:
            return last_exit, last_payload, attempts
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


def _has_complete_booking_selection(item: dict[str, Any]) -> bool:
    booking_url = item.get("booking_url")
    return (
        item.get("status") == "ok"
        and isinstance(booking_url, str)
        and "/travel/flights/booking" in booking_url
    )


async def _select_preflight_rank(
    *,
    search_url: str,
    state_root: Path,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_id: str,
    rank: int,
    max_tabs: int | None,
) -> dict[str, Any]:
    selection_exit, selection_payload = await run_live_itinerary_selection(
        search_url=search_url,
        project_root=state_root,
        run_id=run_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        row_rank=rank,
        outbound_row_rank=rank,
        return_row_rank=rank,
        max_tabs=max_tabs,
    )
    return {
        "rank": rank,
        "exit_code": selection_exit,
        "status": selection_payload.get("status"),
        "booking_url": selection_payload.get("booking_url"),
        "selected_outbound": selection_payload.get("selected_outbound"),
        "selected_return": selection_payload.get("selected_return"),
        "booking_options": selection_payload.get("booking_options") or [],
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
) -> dict[str, Any]:
    if consent_choice == "skip":
        return {"status": "skipped", "choice": consent_choice}

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
    )
    page_id = _page_id(open_result.json_payload)
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
    )
    after_text = _visible_text(after_text_result.json_payload or {})
    settled = "Before you continue to Google" not in after_text and "Accept all" not in after_text
    close_status = "not_run"
    if page_id:
        close_result = await _run_step(
            adapter=adapter,
            args=["page", "close", "--target", page_id],
            browser_mode=browser_mode,
            timeout_seconds=min(timeout_seconds, 10.0),
            run_root=run_root,
            artifact_name="preflight-consent-tab-close.json",
            source_surface="cdp:page-close:preflight-consent",
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
        )
        close_status = close_result.status
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


def _page_id(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    page = payload.get("page")
    if isinstance(page, dict) and isinstance(page.get("id"), str):
        return page["id"]
    target = payload.get("target")
    if isinstance(target, dict) and isinstance(target.get("id"), str):
        return target["id"]
    return ""


def _result_artifact(result: CdpResult) -> dict[str, Any]:
    return {
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
    }


def _evidence(run_id: str, artifacts: list[str], source_surfaces: list[str]) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "artifacts": artifacts,
        "source_surfaces": source_surfaces,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _new_run_id() -> str:
    return "gf-preflight-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
