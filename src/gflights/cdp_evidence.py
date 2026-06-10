"""Async CDP command helpers with evidence artifacts."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Any, AsyncIterator

from gflights.browser import BrowserMode, CdpAdapter, CdpResult


class CdpAssertionTimeout(TimeoutError):
    """Raised when a CDP polling assertion does not become true in time."""

    def __init__(self, message: str, *, outcome: dict[str, Any]) -> None:
        super().__init__(message)
        self.outcome = outcome


class CdpEvidenceHelper:
    """Run CDP commands with artifact logging and bounded polling loops."""

    def __init__(
        self,
        *,
        adapter: CdpAdapter,
        browser_mode: BrowserMode,
        default_timeout_seconds: float,
        run_root: Path,
        executed: list[dict[str, Any]],
        artifacts: list[str],
        source_surfaces: list[str],
    ) -> None:
        self.adapter = adapter
        self.browser_mode = browser_mode
        self.default_timeout_seconds = default_timeout_seconds
        self.run_root = run_root
        self.executed = executed
        self.artifacts = artifacts
        self.source_surfaces = source_surfaces

    @asynccontextmanager
    async def stage(self, name: str) -> AsyncIterator["CdpEvidenceHelper"]:
        started = perf_counter()
        status = "ok"
        try:
            yield self
        except BaseException:
            status = "error"
            raise
        finally:
            artifact_path = self.run_root / f"flow-{_slug(name)}.json"
            write_json(
                artifact_path,
                {
                    "stage": name,
                    "status": status,
                    "elapsed_seconds": round(perf_counter() - started, 3),
                },
            )
            self.artifacts.append(str(artifact_path))
            self.source_surfaces.append(f"cdp:flow:{_slug(name)}")

    async def run(
        self,
        args: list[str],
        *,
        artifact_name: str,
        source_surface: str,
        timeout_seconds: float | None = None,
    ) -> CdpResult:
        timeout = self.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        result = await self.adapter.run_json(
            args,
            browser_mode=self.browser_mode,
            timeout_seconds=timeout,
        )
        artifact_path = self.run_root / artifact_name
        write_json(artifact_path, result_artifact(result))
        self.artifacts.append(str(artifact_path))
        self.source_surfaces.append(source_surface)
        self.executed.append(
            {
                "args": args,
                "browser_mode": self.browser_mode,
                "timeout_seconds": timeout,
                "artifact": str(artifact_path),
                "status": result.status,
                "exit_code": result.exit_code,
            }
        )
        return result

    async def eval(
        self,
        js: str,
        *,
        page_id: str,
        artifact_name: str,
        source_surface: str,
        timeout_seconds: float | None = None,
    ) -> CdpResult:
        return await self.run(
            ["eval", js, "--target", page_id],
            artifact_name=artifact_name,
            source_surface=source_surface,
            timeout_seconds=timeout_seconds,
        )

    async def poll_eval(
        self,
        js: str,
        *,
        page_id: str,
        artifact_prefix: str,
        source_surface: str,
        ready: Callable[[Any, CdpResult], bool],
        timeout_seconds: float,
        interval_seconds: float = 1.0,
        per_attempt_timeout_seconds: float = 3.0,
    ) -> dict[str, Any]:
        started = perf_counter()
        attempts: list[dict[str, Any]] = []
        last_result: CdpResult | None = None
        attempt = 0
        while perf_counter() - started < timeout_seconds:
            attempt += 1
            remaining = timeout_seconds - (perf_counter() - started)
            result = await self.eval(
                js,
                page_id=page_id,
                artifact_name=f"{artifact_prefix}-{attempt:02d}.json",
                source_surface=f"{source_surface}:attempt",
                timeout_seconds=max(0.25, min(per_attempt_timeout_seconds, remaining)),
            )
            last_result = result
            value = eval_value(result.json_payload or {})
            attempts.append(
                {
                    "attempt": attempt,
                    "status": result.status,
                    "exit_code": result.exit_code,
                    "value_summary": _value_summary(value),
                }
            )
            if result.status != "tool_error" and ready(value, result):
                return {
                    "ready": True,
                    "attempts": attempts,
                    "attempt_count": attempt,
                    "elapsed_seconds": round(perf_counter() - started, 3),
                    "value": value,
                    "result": result,
                }
            sleep_for = min(interval_seconds, timeout_seconds - (perf_counter() - started))
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
        return {
            "ready": False,
            "attempts": attempts,
            "attempt_count": attempt,
            "elapsed_seconds": round(perf_counter() - started, 3),
            "value": eval_value((last_result.json_payload if last_result else {}) or {}),
            "result": last_result,
        }

    async def assert_eval(
        self,
        js: str,
        *,
        page_id: str,
        assertion_name: str,
        artifact_prefix: str,
        source_surface: str,
        ready: Callable[[Any, CdpResult], bool],
        timeout_seconds: float,
        interval_seconds: float = 1.0,
        per_attempt_timeout_seconds: float = 3.0,
    ) -> dict[str, Any]:
        outcome = await self.poll_eval(
            js,
            page_id=page_id,
            artifact_prefix=artifact_prefix,
            source_surface=source_surface,
            ready=ready,
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
            per_attempt_timeout_seconds=per_attempt_timeout_seconds,
        )
        summary = serializable_poll_outcome(outcome)
        summary["assertion"] = assertion_name
        summary["status"] = "ok" if outcome["ready"] else "assertion_timeout"
        artifact_path = self.run_root / f"{artifact_prefix}-assertion.json"
        write_json(artifact_path, summary)
        self.artifacts.append(str(artifact_path))
        self.source_surfaces.append(f"{source_surface}:assertion")
        if not outcome["ready"]:
            raise CdpAssertionTimeout(
                f"{assertion_name} timed out after {timeout_seconds:g}s",
                outcome=summary,
            )
        return outcome


def eval_value(payload: dict[str, Any]) -> Any:
    for key in ("value", "result"):
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, dict):
            if "value" in value:
                return value["value"]
            nested_result = value.get("result")
            if isinstance(nested_result, dict) and "value" in nested_result:
                return nested_result["value"]
            return value
        if isinstance(value, list | str | int | float | bool) or value is None:
            return value
    wait = payload.get("wait")
    if isinstance(wait, dict):
        if "value" in wait:
            return wait["value"]
        if "result" in wait:
            return wait["result"]
        evidence = wait.get("evidence")
        if isinstance(evidence, dict) and "value" in evidence:
            return evidence["value"]
    return None


def eval_dict(payload: dict[str, Any]) -> dict[str, Any]:
    value = eval_value(payload)
    return value if isinstance(value, dict) else {}


def eval_list(payload: dict[str, Any]) -> list[Any]:
    value = eval_value(payload)
    return value if isinstance(value, list) else []


def eval_string(payload: dict[str, Any]) -> str:
    value = eval_value(payload)
    return value if isinstance(value, str) else ""


def result_artifact(result: CdpResult) -> dict[str, Any]:
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
        "stdout": result.stdout,
        "stderr": result.stderr,
        "json_payload": result.json_payload,
    }


def serializable_poll_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    result = outcome.get("result")
    return {
        "ready": bool(outcome.get("ready")),
        "attempts": outcome.get("attempts", []),
        "attempt_count": outcome.get("attempt_count", 0),
        "elapsed_seconds": outcome.get("elapsed_seconds", 0),
        "value": outcome.get("value"),
        "last_result": result_artifact(result) if isinstance(result, CdpResult) else None,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _slug(value: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in value.lower()).strip(
        "-"
    )


def _value_summary(value: Any) -> Any:
    if isinstance(value, list):
        return {"type": "list", "count": len(value)}
    if isinstance(value, dict):
        return {"type": "dict", "keys": sorted(str(key) for key in value)[:20]}
    if isinstance(value, str):
        return value[:300]
    return value
