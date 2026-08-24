"""Live date-pair probe adapter for date-window scans."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
from pathlib import Path
from typing import Any

from gflights.app_state import init_app_state
from gflights.browser import BrowserMode
from gflights.domain import SearchIntent
from gflights.live_search import run_live_search

LiveSearchRunner = Callable[..., Awaitable[tuple[int, dict[str, Any] | list[dict[str, Any]]]]]


class LiveDatePairProbe:
    """Synchronous adapter around the async live search runner."""

    def __init__(
        self,
        *,
        project_root: Path,
        browser_mode: BrowserMode = "headed",
        max_probes: int,
        timeout_seconds: float = 30.0,
        live_search_runner: LiveSearchRunner = run_live_search,
    ) -> None:
        self.state = init_app_state(project_root)
        self.browser_mode = browser_mode
        self.max_probes = max(max_probes, 0)
        self.timeout_seconds = timeout_seconds
        self.live_search_runner = live_search_runner
        self.probes_started = 0

    def __call__(self, intent: SearchIntent) -> dict[str, Any]:
        if self.probes_started >= self.max_probes:
            return {
                "query_id": intent.query_id,
                "status": "skipped",
                "reason": "max_live_probes_reached",
                "results": [],
                "unsupported": [],
                "warnings": ["date pair skipped because max live probe limit was reached"],
                "evidence": {
                    "run_id": "date-scan-live-probe-limit",
                    "source_surfaces": ["date-scan-live-probe-limit"],
                    "artifacts": [],
                },
            }

        self.probes_started += 1
        input_path = self._write_probe_intent(intent)
        _exit_code, payload = asyncio.run(
            self.live_search_runner(
                input_json=input_path,
                project_root=self.state.root,
                browser_mode=self.browser_mode,
                timeout_seconds=self.timeout_seconds,
            )
        )
        if isinstance(payload, list):
            return (
                payload[0]
                if payload and isinstance(payload[0], dict)
                else _tool_error_payload(intent, input_path)
            )
        return payload

    def _write_probe_intent(self, intent: SearchIntent) -> Path:
        probe_root = self.state.artifact_root / "date-scan-live-probes"
        probe_root.mkdir(parents=True, exist_ok=True)
        date_key = intent.departure_window.start
        if intent.return_window is not None:
            date_key = f"{date_key}-{intent.return_window.start}"
        path = probe_root / f"{_safe_filename(intent.query_id)}-{date_key}.json"
        path.write_text(json.dumps(intent.model_dump(mode="json"), indent=2) + "\n")
        return path


def _tool_error_payload(intent: SearchIntent, input_path: Path) -> dict[str, Any]:
    return {
        "query_id": intent.query_id,
        "status": "tool_error",
        "results": [],
        "unsupported": [],
        "warnings": ["live search returned an invalid batch payload for one date-pair probe"],
        "evidence": {
            "run_id": "date-scan-live-probe-error",
            "source_surfaces": ["date-scan-live-probe"],
            "artifacts": [str(input_path)],
        },
    }


def _safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.lower())
