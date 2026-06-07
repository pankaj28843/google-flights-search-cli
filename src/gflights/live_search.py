"""Opt-in live Google Flights evidence orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from gflights.browser import BLOCKED_STOP_STATES, BrowserMode, CdpAdapter, CdpResult
from gflights.services import init_project, load_intents

GOOGLE_FLIGHTS_URL = "https://www.google.com/travel/flights"


async def run_live_search(
    *,
    input_json: Path,
    project_root: Path,
    adapter: CdpAdapter | None = None,
    browser_mode: BrowserMode = "headless",
    run_id: str | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    adapter = adapter or CdpAdapter()
    intents = load_intents(input_json)
    intent = intents[0]
    project = init_project(project_root)
    run_id = run_id or _new_run_id(intent.query_id)
    run_root = Path(project["run_root"]) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    target_url = _google_flights_url(intent.language, intent.currency)
    _write_json(run_root / "intent.json", intent.model_dump(mode="json"))

    executed: list[dict[str, Any]] = []
    artifacts: list[str] = [str(run_root / "intent.json")]
    source_surfaces: list[str] = []

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
        _write_json(run_root / "command-log.json", executed)
        artifacts.append(str(run_root / "command-log.json"))
        return open_result.exit_code or 4, _stop_payload(
            intent_query_id=intent.query_id,
            run_id=run_id,
            browser_mode=browser_mode,
            result=open_result,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            target_url=target_url,
        )
    if open_result.status == "tool_error":
        _write_json(run_root / "command-log.json", executed)
        artifacts.append(str(run_root / "command-log.json"))
        return 6, _tool_error_payload(
            intent.query_id, run_id, browser_mode, open_result, artifacts, source_surfaces
        )

    page_id = _page_id(open_result.json_payload)
    await _run_step(
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
    _write_json(run_root / "command-log.json", executed)
    artifacts.append(str(run_root / "command-log.json"))

    for result in (snapshot_result, network_result):
        if result.status == "tool_error":
            return 6, _tool_error_payload(
                intent.query_id, run_id, browser_mode, result, artifacts, source_surfaces
            )

    return 0, {
        "query_id": intent.query_id,
        "status": "experimental",
        "confidence": "weak",
        "live_mode": True,
        "browser_mode": browser_mode,
        "target_url": target_url,
        "results": [],
        "unsupported": [
            {
                "field": "live_result_extraction",
                "status": "deferred",
                "reason": "this live mode captures cdp evidence; durable Google Flights result extraction still requires focused evidence and parser tests",
            }
        ],
        "warnings": [
            "live cdp mode is opt-in and evidence-capture only in this slice",
            "Google Flights URL query/protobuf encoding is not guessed",
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


def _stop_payload(
    *,
    intent_query_id: str,
    run_id: str,
    browser_mode: BrowserMode,
    result: CdpResult,
    artifacts: list[str],
    source_surfaces: list[str],
    target_url: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query_id": intent_query_id,
        "status": result.status if result.status in BLOCKED_STOP_STATES else "blocked",
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "target_url": target_url,
        "stop_state": result.stop_state or result.status,
        "results": [],
        "unsupported": [],
        "warnings": ["live search stopped before bypassing a browser safety boundary"],
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }
    if result.fallback:
        payload["fallback"] = result.fallback
    return payload


def _tool_error_payload(
    query_id: str,
    run_id: str,
    browser_mode: BrowserMode,
    result: CdpResult,
    artifacts: list[str],
    source_surfaces: list[str],
) -> dict[str, Any]:
    return {
        "query_id": query_id,
        "status": "tool_error",
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "results": [],
        "unsupported": [],
        "warnings": [],
        "error": result.error,
        "evidence": {
            "run_id": run_id,
            "artifacts": artifacts,
            "source_surfaces": source_surfaces,
        },
    }


def _is_stop_result(result: CdpResult) -> bool:
    return bool(result.fallback) or result.status in BLOCKED_STOP_STATES


def _google_flights_url(language: str, currency: str) -> str:
    return f"{GOOGLE_FLIGHTS_URL}?{urlencode({'hl': language, 'curr': currency})}"


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


def _new_run_id(query_id: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_query_id = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in query_id.lower())
    return f"gf-{now}-{safe_query_id}"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")
