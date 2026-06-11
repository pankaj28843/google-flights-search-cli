"""cdp subprocess adapter kept outside the pure flight-search domain."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any, Literal

BrowserMode = Literal["headless", "headed"]
Runner = Callable[[Sequence[str], float], Awaitable["ProcessResult"]]

BLOCKED_STOP_STATES = {
    "blocked",
    "access_denied",
    "login_required",
    "unusual_traffic",
    "human_required",
    "payment_or_booking_boundary",
    "personal_data_required",
    "permission_required",
}


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class CdpResult:
    argv: list[str]
    browser_mode: BrowserMode
    returncode: int
    stdout: str
    stderr: str
    status: str
    exit_code: int
    json_payload: dict[str, Any] | None = None
    stop_state: str | None = None
    fallback: dict[str, str] | None = None
    error: str = ""
    timeout: bool = False
    attempt_count: int = 1
    max_attempts: int = 1
    attempts: list[dict[str, Any]] | None = None


class CdpAdapter:
    def __init__(
        self,
        runner: Runner | None = None,
        executable: str = "cdp",
        max_tabs: int | None = None,
        allow_over_budget: bool = False,
    ) -> None:
        self._runner = runner or run_subprocess
        self._executable = executable
        self._max_tabs = max_tabs if max_tabs and max_tabs > 0 else None
        self._allow_over_budget = allow_over_budget

    async def run_json(
        self,
        args: Sequence[str],
        *,
        browser_mode: BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> CdpResult:
        max_attempts = 3
        total_timeout = max(0.001, float(timeout_seconds))
        started = perf_counter()
        attempt_summaries: list[dict[str, Any]] = []
        last_result: CdpResult | None = None

        for attempt in range(1, max_attempts + 1):
            remaining_timeout = (
                total_timeout if attempt == 1 else total_timeout - (perf_counter() - started)
            )
            if remaining_timeout <= 0:
                return _retry_deadline_exhausted_result(
                    argv=last_result.argv
                    if last_result is not None
                    else self._argv(args, browser_mode, total_timeout),
                    browser_mode=browser_mode,
                    total_timeout=total_timeout,
                    max_attempts=max_attempts,
                    attempts=attempt_summaries,
                )
            argv = self._argv(args, browser_mode, remaining_timeout)
            try:
                process = await self._runner(argv, remaining_timeout)
            except asyncio.TimeoutError:
                result = CdpResult(
                    argv=argv,
                    browser_mode=browser_mode,
                    returncode=-1,
                    stdout="",
                    stderr="",
                    status="tool_error",
                    exit_code=6,
                    error=f"cdp command timed out after {_format_timeout(remaining_timeout)}",
                    timeout=True,
                )
                attempt_summaries.append(_attempt_summary(attempt, remaining_timeout, result))
                return _with_attempts(result, max_attempts=max_attempts, attempts=attempt_summaries)
            result = _cdp_result_from_process(argv=argv, browser_mode=browser_mode, process=process)
            attempt_summaries.append(_attempt_summary(attempt, remaining_timeout, result))
            last_result = result
            if attempt >= max_attempts or not _is_transient_command_failure(result):
                return _with_attempts(result, max_attempts=max_attempts, attempts=attempt_summaries)
            sleep_seconds = min(2.0, 0.4 * attempt)
            remaining_after_attempt = total_timeout - (perf_counter() - started)
            if remaining_after_attempt <= 0:
                return _retry_deadline_exhausted_result(
                    argv=argv,
                    browser_mode=browser_mode,
                    total_timeout=total_timeout,
                    max_attempts=max_attempts,
                    attempts=attempt_summaries,
                )
            await asyncio.sleep(min(sleep_seconds, remaining_after_attempt))
        if last_result is not None:
            return _with_attempts(
                last_result,
                max_attempts=max_attempts,
                attempts=attempt_summaries,
            )
        return _retry_deadline_exhausted_result(
            argv=self._argv(args, browser_mode, total_timeout),
            browser_mode=browser_mode,
            total_timeout=total_timeout,
            max_attempts=max_attempts,
            attempts=attempt_summaries,
        )

    def _argv(
        self,
        args: Sequence[str],
        browser_mode: BrowserMode,
        timeout_seconds: float,
    ) -> list[str]:
        argv = [
            self._executable,
            "--browser-mode",
            browser_mode,
            "--json",
            "--timeout",
            _format_timeout(timeout_seconds),
        ]
        if self._allow_over_budget:
            argv.append("--allow-over-budget")
        if self._max_tabs is not None:
            argv.extend(["--max-tabs", str(self._max_tabs)])
        argv.extend(args)
        return argv


async def run_subprocess(argv: Sequence[str], timeout_seconds: float) -> ProcessResult:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
        raise
    return ProcessResult(
        returncode=process.returncode or 0,
        stdout=stdout.decode(),
        stderr=stderr.decode(),
    )


def _format_timeout(timeout_seconds: float) -> str:
    seconds = float(timeout_seconds)
    if seconds.is_integer():
        return f"{int(seconds)}s"
    return f"{seconds:g}s"


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _cdp_result_from_process(
    *,
    argv: list[str],
    browser_mode: BrowserMode,
    process: ProcessResult,
) -> CdpResult:
    if process.returncode != 0:
        payload = _json_object(process.stdout)
        message = payload.get("message") if payload is not None else None
        if _is_resource_budget_payload(payload):
            stop_state = str(payload.get("code"))
            return CdpResult(
                argv=argv,
                browser_mode=browser_mode,
                returncode=process.returncode,
                stdout=process.stdout,
                stderr=process.stderr,
                status="blocked",
                exit_code=4,
                json_payload=payload,
                stop_state=stop_state,
                fallback=_headed_fallback(browser_mode, "blocked", stop_state),
                error=process.stderr or str(message or f"cdp exited with {process.returncode}"),
            )
        return CdpResult(
            argv=argv,
            browser_mode=browser_mode,
            returncode=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
            status="tool_error",
            exit_code=6,
            json_payload=payload,
            error=process.stderr or str(message or f"cdp exited with {process.returncode}"),
        )

    try:
        payload = json.loads(process.stdout or "{}")
    except json.JSONDecodeError as exc:
        return CdpResult(
            argv=argv,
            browser_mode=browser_mode,
            returncode=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
            status="tool_error",
            exit_code=6,
            error=f"invalid JSON from cdp: {exc.msg}",
        )

    status = str(payload.get("status") or "ok")
    stop_state = payload.get("stop_state")
    stop_state_text = str(stop_state) if stop_state is not None else None
    fallback = _headed_fallback(browser_mode, status, stop_state_text)
    return CdpResult(
        argv=argv,
        browser_mode=browser_mode,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
        status=status,
        exit_code=4 if fallback else 0,
        json_payload=payload,
        stop_state=stop_state_text,
        fallback=fallback,
    )


def _with_attempts(
    result: CdpResult,
    *,
    max_attempts: int,
    attempts: list[dict[str, Any]],
) -> CdpResult:
    return replace(
        result,
        attempt_count=len(attempts),
        max_attempts=max_attempts,
        attempts=list(attempts),
    )


def _retry_deadline_exhausted_result(
    *,
    argv: list[str],
    browser_mode: BrowserMode,
    total_timeout: float,
    max_attempts: int,
    attempts: list[dict[str, Any]],
) -> CdpResult:
    return CdpResult(
        argv=argv,
        browser_mode=browser_mode,
        returncode=-1,
        stdout="",
        stderr="",
        status="tool_error",
        exit_code=6,
        error=(
            "cdp command retry deadline exhausted after "
            f"{len(attempts)} attempt(s) and {_format_timeout(total_timeout)}"
        ),
        timeout=True,
        attempt_count=len(attempts),
        max_attempts=max_attempts,
        attempts=list(attempts),
    )


def _attempt_summary(
    attempt: int,
    timeout_seconds: float,
    result: CdpResult,
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "timeout_seconds": timeout_seconds,
        "returncode": result.returncode,
        "status": result.status,
        "exit_code": result.exit_code,
        "stop_state": result.stop_state,
        "error": result.error,
        "timeout": result.timeout,
    }


def _is_transient_command_failure(result: CdpResult) -> bool:
    if result.status != "tool_error" or result.timeout:
        return False
    if _is_transient_connection_failure(result):
        return True
    text = _result_error_text(result)
    return any(
        marker in text
        for marker in [
            "target_not_found",
            "target not found",
            "no target",
            "could not find target",
            "cannot find context with specified id",
            "execution context was destroyed",
            "target closed",
            "session closed",
            "context canceled",
            "websocket: close",
            "i/o timeout",
            "daemon rpc",
        ]
    )


def _is_transient_connection_failure(result: CdpResult) -> bool:
    payload = result.json_payload or {}
    if payload.get("code") != "connection_failed" and payload.get("err_class") != "connection":
        return False
    text = _result_error_text(result)
    return any(
        marker in text
        for marker in [
            "failed to read json message",
            "failed to get reader",
            "use of closed network connection",
            "connection refused",
            "browser_dial_failed",
            "browser commands require a running",
            "keepalive repair is locked",
            "starting_daemon",
            "daemon rpc",
        ]
    )


def _result_error_text(result: CdpResult) -> str:
    payload = result.json_payload or {}
    return " ".join(
        str(value)
        for value in [
            result.error,
            result.stderr,
            payload.get("message"),
            payload.get("error"),
            payload.get("code"),
            payload.get("err_class"),
        ]
        if value
    ).lower()


def _is_resource_budget_payload(payload: dict[str, Any] | None) -> bool:
    if payload is None:
        return False
    return (
        payload.get("code") == "browser_resource_budget_exceeded"
        or payload.get("err_class") == "resource_budget"
    )


def _headed_fallback(
    browser_mode: BrowserMode,
    status: str,
    stop_state: str | None,
) -> dict[str, str] | None:
    if browser_mode != "headless":
        return None
    if status not in BLOCKED_STOP_STATES and stop_state not in BLOCKED_STOP_STATES:
        return None
    return {
        "recommended_browser_mode": "headed",
        "reason": "headless blocked or human confirmation required",
    }
