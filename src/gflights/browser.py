"""cdp subprocess adapter kept outside the pure flight-search domain."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

BrowserMode = Literal["headless", "headed"]
Runner = Callable[[Sequence[str], float], Awaitable["ProcessResult"]]

BLOCKED_STOP_STATES = {
    "blocked",
    "access_denied",
    "login_required",
    "unusual_traffic",
    "human_required",
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


class CdpAdapter:
    def __init__(
        self,
        runner: Runner | None = None,
        executable: str = "cdp",
    ) -> None:
        self._runner = runner or run_subprocess
        self._executable = executable

    async def run_json(
        self,
        args: Sequence[str],
        *,
        browser_mode: BrowserMode = "headless",
        timeout_seconds: float = 30.0,
    ) -> CdpResult:
        argv = [
            self._executable,
            "--browser-mode",
            browser_mode,
            "--json",
            "--timeout",
            _format_timeout(timeout_seconds),
            *args,
        ]
        try:
            process = await self._runner(argv, timeout_seconds)
        except asyncio.TimeoutError:
            return CdpResult(
                argv=argv,
                browser_mode=browser_mode,
                returncode=-1,
                stdout="",
                stderr="",
                status="tool_error",
                exit_code=6,
                error=f"cdp command timed out after {_format_timeout(timeout_seconds)}",
                timeout=True,
            )

        if process.returncode != 0:
            return CdpResult(
                argv=argv,
                browser_mode=browser_mode,
                returncode=process.returncode,
                stdout=process.stdout,
                stderr=process.stderr,
                status="tool_error",
                exit_code=6,
                error=process.stderr or f"cdp exited with {process.returncode}",
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


async def run_subprocess(argv: Sequence[str], timeout_seconds: float) -> ProcessResult:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
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
