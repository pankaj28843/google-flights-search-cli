"""Cleanup helpers for CLI-managed live cdp pages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gflights.browser import BrowserMode, CdpAdapter, CdpResult


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
) -> CdpResult | None:
    """Close a page opened by this CLI without changing the domain result."""
    if not page_id:
        return None

    artifact_path = run_root / "managed-tab-close.json"
    args = ["page", "close", "--target", page_id]
    cleanup_timeout = min(timeout_seconds, 5.0)
    result = await adapter.run_json(
        args,
        browser_mode=browser_mode,
        timeout_seconds=cleanup_timeout,
    )
    _write_json(artifact_path, _result_artifact(result))
    if str(artifact_path) not in artifacts:
        artifacts.append(str(artifact_path))
    source_surfaces.append("cdp:page-close")
    if warnings is not None and _cleanup_failed(result):
        warnings.append(_cleanup_warning(result))
    executed.append(
        {
            "args": args,
            "browser_mode": browser_mode,
            "timeout_seconds": cleanup_timeout,
            "artifact": str(artifact_path),
            "status": result.status,
            "exit_code": result.exit_code,
            "cleanup": True,
        }
    )
    return result


def _cleanup_failed(result: CdpResult) -> bool:
    return result.status != "ok" or result.exit_code != 0


def _cleanup_warning(result: CdpResult) -> str:
    if result.timeout:
        return "managed cdp page cleanup timed out; see managed-tab-close.json"
    return f"managed cdp page cleanup returned {result.status}; see managed-tab-close.json"


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


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")
