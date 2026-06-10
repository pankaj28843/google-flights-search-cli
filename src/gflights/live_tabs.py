"""Helpers for live CDP tab budgets and explicit managed-tab reuse."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gflights.browser import BrowserMode, CdpAdapter, CdpResult

GOOGLE_FLIGHTS_URL_FRAGMENT = "google.com/travel/flights"


def tab_budget_enabled(
    *,
    max_tabs: int | None = None,
    managed_tab_policy: str = "new",
    reuse_target: str = "",
) -> bool:
    return bool(
        (max_tabs is not None and max_tabs > 0)
        or managed_tab_policy == "reuse"
        or reuse_target.strip()
    )


async def capture_tab_budget(
    *,
    adapter: CdpAdapter,
    browser_mode: BrowserMode,
    timeout_seconds: float,
    run_root: Path,
    stage: str,
    executed: list[dict[str, Any]],
    artifacts: list[str],
    source_surfaces: list[str],
) -> dict[str, Any]:
    args = ["pages"]
    result = await adapter.run_json(
        args,
        browser_mode=browser_mode,
        timeout_seconds=min(timeout_seconds, 10.0),
    )
    artifact_path = run_root / f"tab-budget-{stage}.json"
    _write_json(artifact_path, _result_artifact(result))
    artifacts.append(str(artifact_path))
    source_surfaces.append(f"cdp:pages:tab-budget-{stage}")
    executed.append(
        {
            "args": args,
            "browser_mode": browser_mode,
            "timeout_seconds": min(timeout_seconds, 10.0),
            "artifact": str(artifact_path),
            "status": result.status,
            "exit_code": result.exit_code,
        }
    )
    return {
        "status": result.status,
        "budget": _budget(result.json_payload or {}),
        "pages": _pages(result.json_payload or {}),
        "artifact": str(artifact_path),
    }


def open_args_for_policy(
    *,
    url: str,
    managed_tab_policy: str = "new",
    reuse_target: str = "",
    tab_budget_before: dict[str, Any] | None = None,
) -> tuple[list[str], bool]:
    target = reuse_target.strip()
    if target and target != "google-flights":
        return ["open", url, "--new-tab=false", "--target", target], False

    reusable_page_id = ""
    if target == "google-flights" or managed_tab_policy == "reuse":
        reusable_page_id = _first_google_flights_page_id(tab_budget_before or {})

    if reusable_page_id:
        return ["open", url, "--new-tab=false", "--target", reusable_page_id], False
    return ["open", url], True


def tab_budget_summary(
    *,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    managed_tab_policy: str,
    max_tabs: int | None,
    reuse_target: str,
    managed_tab_id: str,
    managed_tab_created: bool,
    cleanup_status: str,
) -> dict[str, Any]:
    return {
        "policy": managed_tab_policy,
        "max_tabs": max_tabs if max_tabs and max_tabs > 0 else None,
        "reuse_target": reuse_target or None,
        "managed_tab_id": managed_tab_id or None,
        "managed_tab_created": managed_tab_created,
        "cleanup_status": cleanup_status,
        "before": (before or {}).get("budget"),
        "after": (after or {}).get("budget"),
        "artifacts": [
            artifact
            for artifact in [
                (before or {}).get("artifact"),
                (after or {}).get("artifact"),
            ]
            if isinstance(artifact, str) and artifact
        ],
    }


def _first_google_flights_page_id(tab_budget: dict[str, Any]) -> str:
    pages = tab_budget.get("pages")
    if not isinstance(pages, list):
        return ""
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_id = page.get("id")
        url = str(page.get("url") or "")
        if isinstance(page_id, str) and GOOGLE_FLIGHTS_URL_FRAGMENT in url:
            return page_id
    return ""


def _budget(payload: dict[str, Any]) -> dict[str, Any] | None:
    budget = payload.get("budget") or payload.get("resource_budget")
    return budget if isinstance(budget, dict) else None


def _pages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return []
    return [
        {
            "id": page.get("id"),
            "type": page.get("type"),
            "title": page.get("title"),
            "url": page.get("url"),
            "attached": page.get("attached"),
        }
        for page in pages
        if isinstance(page, dict)
    ]


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
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
