"""Cleanup helpers for CLI-managed live cdp pages."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from gflights.browser import BrowserMode, CdpAdapter, CdpResult
from gflights.live_trace import TaskTrace

REAL_CDP_CLOSE_SETTLE_SECONDS = 0.4
CDP_CLOSE_ATTEMPT_TIMEOUT_SECONDS = 60.0
CDP_CLOSE_MAX_ATTEMPTS = 3
CDP_CLOSE_RETRY_SLEEP_SECONDS = 1.0


async def close_managed_page(
    *,
    adapter: CdpAdapter,
    page_id: str,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
    warnings: list[str] | None = None,
    task_trace: TaskTrace | None = None,
    artifact_name: str = "managed-tab-close.json",
    source_surface: str = "cdp:page-close",
) -> CdpResult | None:
    """Close a page opened by this CLI without changing the domain result."""
    if not page_id:
        return None

    artifact_path = run_root / artifact_name
    args = ["page", "close", "--target", page_id]
    cleanup_timeout = CDP_CLOSE_ATTEMPT_TIMEOUT_SECONDS
    attempts: list[dict[str, Any]] = []
    result: CdpResult | None = None
    for attempt in range(1, CDP_CLOSE_MAX_ATTEMPTS + 1):
        result = await adapter.run_json(
            args,
            browser_mode=browser_mode,
            timeout_seconds=cleanup_timeout,
        )
        attempts.append(
            {
                "attempt": attempt,
                "timeout_seconds": cleanup_timeout,
                **_result_artifact(result, task_trace=task_trace),
            }
        )
        executed.append(
            {
                "args": args,
                "browser_mode": browser_mode,
                "timeout_seconds": cleanup_timeout,
                "artifact": str(artifact_path),
                "status": result.status,
                "exit_code": result.exit_code,
                "cleanup": True,
                "cleanup_attempt": attempt,
                "attempt_count": result.attempt_count,
                "max_attempts": result.max_attempts,
                "task_trace": task_trace.as_dict() if task_trace is not None else None,
            }
        )
        if not _cleanup_failed(result):
            break
        if attempt < CDP_CLOSE_MAX_ATTEMPTS and type(adapter) is CdpAdapter:
            await asyncio.sleep(CDP_CLOSE_RETRY_SLEEP_SECONDS)

    assert result is not None
    artifact = _result_artifact(result, task_trace=task_trace)
    artifact["attempts"] = attempts
    artifact["attempt_count"] = len(attempts)
    artifact["max_attempts"] = CDP_CLOSE_MAX_ATTEMPTS
    _write_json(artifact_path, artifact)
    if str(artifact_path) not in artifacts:
        artifacts.append(str(artifact_path))
    source_surfaces.append(source_surface)
    if warnings is not None and _cleanup_failed(result):
        warnings.append(_cleanup_warning(result, attempts=len(attempts)))
    if result.status == "ok" and type(adapter) is CdpAdapter:
        await asyncio.sleep(REAL_CDP_CLOSE_SETTLE_SECONDS)
    return result


def _cleanup_failed(result: CdpResult) -> bool:
    return result.status != "ok" or result.exit_code != 0


def _cleanup_warning(result: CdpResult, *, attempts: int) -> str:
    if result.timeout:
        return (
            "managed cdp page cleanup timed out "
            f"after {attempts} attempt(s); see managed-tab-close.json"
        )
    return (
        f"managed cdp page cleanup returned {result.status} "
        f"after {attempts} attempt(s); see managed-tab-close.json"
    )


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
    if task_trace is not None:
        artifact["task_trace"] = task_trace.as_dict()
        artifact["target_task_ids"] = task_trace.ownership_map()
    return artifact


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")
