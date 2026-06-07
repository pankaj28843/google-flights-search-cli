"""Live selected-itinerary inspection orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gflights.app_state import init_app_state
from gflights.browser import BLOCKED_STOP_STATES, BrowserMode, CdpAdapter, CdpResult
from gflights.itinerary_extraction import extract_selected_itinerary
from gflights.live_cleanup import close_managed_page


async def run_live_itinerary_inspection(
    *,
    booking_url: str,
    project_root: Path | None = None,
    adapter: CdpAdapter | None = None,
    browser_mode: BrowserMode = "headless",
    run_id: str | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    adapter = adapter or CdpAdapter()
    state = init_app_state(project_root)
    run_id = run_id or _new_run_id()
    run_root = state.run_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    executed: list[dict[str, Any]] = []
    artifacts: list[str] = []
    source_surfaces: list[str] = []

    _write_json(run_root / "booking-url.json", {"booking_url": booking_url})
    artifacts.append(str(run_root / "booking-url.json"))
    page_id = ""

    open_result = await _run_step(
        adapter=adapter,
        args=["open", booking_url],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="open.json",
        source_surface="cdp:open:selected-itinerary",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    page_id = _page_id(open_result.json_payload)
    if _is_stop_result(open_result):
        return await _finish_itinerary_inspection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            result=_finish_stop(
                run_root=run_root,
                executed=executed,
                booking_url=booking_url,
                run_id=run_id,
                browser_mode=browser_mode,
                result=open_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
            ),
        )
    if open_result.status == "tool_error":
        return await _finish_itinerary_inspection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            result=_finish_tool_error(
                run_root=run_root,
                executed=executed,
                booking_url=booking_url,
                run_id=run_id,
                browser_mode=browser_mode,
                result=open_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
            ),
        )

    wait_result = await _run_step(
        adapter=adapter,
        args=["wait", "load-state", "domcontentloaded", "--target", page_id],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="wait.json",
        source_surface="cdp:wait:selected-itinerary",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_stop_result(wait_result):
        return await _finish_itinerary_inspection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            result=_finish_stop(
                run_root=run_root,
                executed=executed,
                booking_url=booking_url,
                run_id=run_id,
                browser_mode=browser_mode,
                result=wait_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
            ),
        )
    if wait_result.status == "tool_error":
        return await _finish_itinerary_inspection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            result=_finish_tool_error(
                run_root=run_root,
                executed=executed,
                booking_url=booking_url,
                run_id=run_id,
                browser_mode=browser_mode,
                result=wait_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
            ),
        )

    snapshot_result = await _run_step(
        adapter=adapter,
        args=["snapshot", "--target", page_id, "--limit", "120"],
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        artifact_name="snapshot.json",
        source_surface="cdp:snapshot:selected-itinerary",
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
    )
    if _is_stop_result(snapshot_result):
        return await _finish_itinerary_inspection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            result=_finish_stop(
                run_root=run_root,
                executed=executed,
                booking_url=booking_url,
                run_id=run_id,
                browser_mode=browser_mode,
                result=snapshot_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
            ),
        )
    if snapshot_result.status == "tool_error":
        return await _finish_itinerary_inspection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            result=_finish_tool_error(
                run_root=run_root,
                executed=executed,
                booking_url=booking_url,
                run_id=run_id,
                browser_mode=browser_mode,
                result=snapshot_result,
                artifacts=artifacts,
                source_surfaces=source_surfaces,
            ),
        )

    itinerary = extract_selected_itinerary(
        {"snapshot": snapshot_result.json_payload or {}},
        source_surface="selected-itinerary-visible-text",
        confidence="weak",
    )
    if itinerary["segments"]:
        return await _finish_itinerary_inspection(
            adapter=adapter,
            page_id=page_id,
            browser_mode=browser_mode,
            timeout_seconds=timeout_seconds,
            run_root=run_root,
            executed=executed,
            artifacts=artifacts,
            source_surfaces=source_surfaces,
            result=(
                0,
                {
                    "status": "ok",
                    "confidence": "weak",
                    "live_mode": True,
                    "browser_mode": browser_mode,
                    "booking_url": booking_url,
                    "itinerary": itinerary,
                    "unsupported": [],
                    "warnings": [
                        "live itinerary inspection captured visible selected-itinerary evidence and did not click provider booking controls"
                    ],
                    "evidence": {
                        "run_id": run_id,
                        "artifacts": artifacts,
                        "source_surfaces": source_surfaces,
                    },
                },
            ),
        )

    return await _finish_itinerary_inspection(
        adapter=adapter,
        page_id=page_id,
        browser_mode=browser_mode,
        timeout_seconds=timeout_seconds,
        run_root=run_root,
        executed=executed,
        artifacts=artifacts,
        source_surfaces=source_surfaces,
        result=(
            0,
            {
                "status": "experimental",
                "confidence": "weak",
                "live_mode": True,
                "browser_mode": browser_mode,
                "booking_url": booking_url,
                "itinerary": None,
                "unsupported": [
                    {
                        "field": "live_itinerary_extraction",
                        "status": "deferred",
                        "reason": "no selected-itinerary detail segments were parsed from the visible text snapshot",
                    }
                ],
                "warnings": [
                    "live itinerary inspection did not click provider booking controls",
                ],
                "evidence": {
                    "run_id": run_id,
                    "artifacts": artifacts,
                    "source_surfaces": source_surfaces,
                },
            },
        ),
    )


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
    run_root: Path,
    executed: list[dict[str, Any]],
    booking_url: str,
    run_id: str,
    browser_mode: BrowserMode,
    result: CdpResult,
    artifacts: list[str],
    source_surfaces: list[str],
) -> tuple[int, dict[str, Any]]:
    _write_command_log(run_root, executed, artifacts)
    payload: dict[str, Any] = {
        "status": result.status if result.status in BLOCKED_STOP_STATES else "blocked",
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "booking_url": booking_url,
        "stop_state": result.stop_state or result.status,
        "itinerary": None,
        "unsupported": [],
        "warnings": [
            "live itinerary inspection stopped before crossing a provider, login, payment, personal-data, or access-control boundary"
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


def _finish_tool_error(
    *,
    run_root: Path,
    executed: list[dict[str, Any]],
    booking_url: str,
    run_id: str,
    browser_mode: BrowserMode,
    result: CdpResult,
    artifacts: list[str],
    source_surfaces: list[str],
) -> tuple[int, dict[str, Any]]:
    _write_command_log(run_root, executed, artifacts)
    return 6, {
        "status": "tool_error",
        "confidence": "unknown",
        "live_mode": True,
        "browser_mode": browser_mode,
        "booking_url": booking_url,
        "itinerary": None,
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


def _write_command_log(
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
) -> None:
    path = run_root / "command-log.json"
    _write_json(path, executed)
    if str(path) not in artifacts:
        artifacts.append(str(path))


async def _finish_itinerary_inspection(
    *,
    adapter: CdpAdapter,
    page_id: str,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
    result: tuple[int, dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    await close_managed_page(
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
    _write_command_log(run_root, executed, artifacts)
    return result


def _payload_warnings(payload: dict[str, Any]) -> list[str] | None:
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        return warnings
    return None


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"gf-itinerary-{timestamp}"
