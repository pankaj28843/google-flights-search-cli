"""Live route-autocomplete evidence orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from gflights.app_state import init_app_state
from gflights.browser import BLOCKED_STOP_STATES, BrowserMode, CdpAdapter, CdpResult
from gflights.route_resolution import extract_route_choices_from_snapshot

GOOGLE_FLIGHTS_URL = "https://www.google.com/travel/flights"


async def run_live_route_resolution(
    *,
    input_text: str,
    project_root: Path | None = None,
    adapter: CdpAdapter | None = None,
    browser_mode: BrowserMode = "headless",
    run_id: str | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    adapter = adapter or CdpAdapter()
    normalized_input = _normalize(input_text)
    if not normalized_input:
        return 2, {
            "status": "ambiguous",
            "input_text": input_text,
            "selected": None,
            "choices": [],
            "unsupported": [],
            "warnings": ["route input text is empty"],
            "evidence": {
                "run_id": "route-resolution-input-validation",
                "source_surfaces": ["input-validation"],
                "artifacts": [],
            },
        }

    state = init_app_state(project_root)
    run_id = run_id or _new_run_id()
    run_root = state.run_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    executed: list[dict[str, Any]] = []
    input_artifact = run_root / "input.json"
    _write_json(
        input_artifact,
        {
            "input_text": input_text,
            "normalized_input": normalized_input,
            "target": "route-autocomplete",
        },
    )
    artifacts: list[str] = [str(input_artifact)]
    source_surfaces: list[str] = []
    target_url = _google_flights_url()

    open_result = await _run_step(
        adapter=adapter,
        args=["open", target_url],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="open.json",
        source_surface="cdp:open",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_stop_result(open_result):
        return _finish_stop(
            input_text=input_text,
            run_id=run_id,
            browser_mode=browser_mode,
            result=open_result,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            target_url=target_url,
            run_root=run_root,
            executed=executed,
        )
    if open_result.status == "tool_error":
        return _finish_tool_error(
            input_text=input_text,
            run_id=run_id,
            browser_mode=browser_mode,
            result=open_result,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            run_root=run_root,
            executed=executed,
        )

    page_id = _page_id(open_result.json_payload)
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
    if _is_stop_result(wait_result):
        return _finish_stop(
            input_text=input_text,
            run_id=run_id,
            browser_mode=browser_mode,
            result=wait_result,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            target_url=target_url,
            run_root=run_root,
            executed=executed,
        )
    if wait_result.status == "tool_error":
        return _finish_tool_error(
            input_text=input_text,
            run_id=run_id,
            browser_mode=browser_mode,
            result=wait_result,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            run_root=run_root,
            executed=executed,
        )

    fill_result = await _run_step(
        adapter=adapter,
        args=[
            "fill",
            "Where to?",
            input_text,
            "--by",
            "label",
            "--exact",
            "--target",
            page_id,
            "--wait-text",
            input_text,
        ],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="route-autocomplete-fill.json",
        source_surface="cdp:route-autocomplete-fill",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_stop_result(fill_result):
        return _finish_stop(
            input_text=input_text,
            run_id=run_id,
            browser_mode=browser_mode,
            result=fill_result,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            target_url=target_url,
            run_root=run_root,
            executed=executed,
        )
    if fill_result.status == "tool_error":
        return _finish_tool_error(
            input_text=input_text,
            run_id=run_id,
            browser_mode=browser_mode,
            result=fill_result,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            run_root=run_root,
            executed=executed,
        )

    snapshot_result = await _run_step(
        adapter=adapter,
        args=["snapshot", "--target", page_id, "--limit", "120"],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="snapshot.json",
        source_surface="cdp:snapshot:route-autocomplete",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_stop_result(snapshot_result):
        return _finish_stop(
            input_text=input_text,
            run_id=run_id,
            browser_mode=browser_mode,
            result=snapshot_result,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            target_url=target_url,
            run_root=run_root,
            executed=executed,
        )
    if snapshot_result.status == "tool_error":
        return _finish_tool_error(
            input_text=input_text,
            run_id=run_id,
            browser_mode=browser_mode,
            result=snapshot_result,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            run_root=run_root,
            executed=executed,
        )

    route_exit_code, route_payload = extract_route_choices_from_snapshot(
        input_text=input_text,
        field="route",
        snapshot=snapshot_result.json_payload or {},
        evidence_artifact=str(run_root / "snapshot.json"),
        source_surface="route-autocomplete-visible-text",
    )
    if route_exit_code in {0, 2}:
        source_surfaces.append("route-autocomplete-visible-text")
        _write_command_log(run_root, executed, artifacts)
        route_payload.update(
            {
                "live_mode": True,
                "browser_mode": browser_mode,
                "target_url": target_url,
                "evidence": {
                    "run_id": run_id,
                    "artifacts": artifacts,
                    "source_surfaces": source_surfaces,
                },
            }
        )
        return route_exit_code, route_payload

    _write_command_log(run_root, executed, artifacts)
    return 3, {
        "status": "unsupported",
        "input_text": input_text,
        "selected": None,
        "choices": [],
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "target_url": target_url,
        "unsupported": [
            {
                "field": "route.resolve.live_autocomplete_extraction",
                "status": "deferred",
                "reason": "live route autocomplete evidence capture is available, but durable choice extraction still requires focused fixture-backed parser tests",
            }
        ],
        "warnings": [
            "live route resolution captured cdp evidence but did not infer route choices from unsupported selectors"
        ],
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
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


def _finish_stop(
    *,
    input_text: str,
    run_id: str,
    browser_mode: BrowserMode,
    result: CdpResult,
    artifacts: list[str],
    source_surfaces: list[str],
    target_url: str,
    run_root: Path,
    executed: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    _write_command_log(run_root, executed, artifacts)
    payload: dict[str, Any] = {
        "status": result.status if result.status in BLOCKED_STOP_STATES else "blocked",
        "input_text": input_text,
        "selected": None,
        "choices": [],
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "target_url": target_url,
        "stop_state": result.stop_state or result.status,
        "unsupported": [],
        "warnings": ["live route resolution stopped before bypassing a browser safety boundary"],
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }
    if result.fallback:
        payload["fallback"] = result.fallback
    return result.exit_code or 4, payload


def _finish_tool_error(
    *,
    input_text: str,
    run_id: str,
    browser_mode: BrowserMode,
    result: CdpResult,
    artifacts: list[str],
    source_surfaces: list[str],
    run_root: Path,
    executed: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    _write_command_log(run_root, executed, artifacts)
    return 6, {
        "status": "tool_error",
        "input_text": input_text,
        "selected": None,
        "choices": [],
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "unsupported": [],
        "warnings": [],
        "error": result.error,
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }


def _write_command_log(
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
) -> None:
    command_log = run_root / "command-log.json"
    _write_json(command_log, executed)
    artifacts.append(str(command_log))


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
    if isinstance(payload.get("page_id"), str):
        return payload["page_id"]
    return ""


def _google_flights_url() -> str:
    return f"{GOOGLE_FLIGHTS_URL}?{urlencode({'hl': 'en'})}"


def _new_run_id() -> str:
    return f"gf-route-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2))


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").casefold().strip().split())
