from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from gflights import browser
from gflights.browser import CdpAdapter, ProcessResult


class FakeRunner:
    def __init__(
        self,
        result: ProcessResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result or ProcessResult(returncode=0, stdout='{"status":"ok"}', stderr="")
        self.error = error
        self.calls: list[tuple[list[str], float]] = []

    async def __call__(self, argv: Sequence[str], timeout_seconds: float) -> ProcessResult:
        self.calls.append((list(argv), timeout_seconds))
        if self.error is not None:
            raise self.error
        return self.result


class SequenceRunner:
    def __init__(self, results: list[ProcessResult]) -> None:
        self.results = results
        self.calls: list[tuple[list[str], float]] = []

    async def __call__(self, argv: Sequence[str], timeout_seconds: float) -> ProcessResult:
        self.calls.append((list(argv), timeout_seconds))
        return self.results.pop(0)


def test_cdp_adapter_builds_headless_json_command() -> None:
    runner = FakeRunner(ProcessResult(returncode=0, stdout='{"targets":[]}', stderr=""))
    adapter = CdpAdapter(runner=runner)

    result = asyncio.run(adapter.run_json(["pages"]))

    assert runner.calls == [
        (
            ["cdp", "--browser-mode", "headless", "--json", "--timeout", "30s", "pages"],
            30.0,
        )
    ]
    assert result.status == "ok"
    assert result.browser_mode == "headless"
    assert result.json_payload == {"targets": []}


def test_cdp_adapter_retries_transient_connection_failure() -> None:
    runner = SequenceRunner(
        [
            ProcessResult(
                returncode=3,
                stdout=(
                    '{"ok":false,"code":"connection_failed","err_class":"connection",'
                    '"message":"check browser resource budget: failed to read JSON message: '
                    'failed to get reader: use of closed network connection"}'
                ),
                stderr="",
            ),
            ProcessResult(returncode=0, stdout='{"status":"ok","page_id":"page-1"}', stderr=""),
        ]
    )
    adapter = CdpAdapter(runner=runner)

    result = asyncio.run(adapter.run_json(["open", "https://www.google.com/travel/flights"]))

    assert len(runner.calls) == 2
    assert result.status == "ok"
    assert result.json_payload == {"status": "ok", "page_id": "page-1"}
    assert result.attempt_count == 2
    assert result.max_attempts == 3
    assert [attempt["status"] for attempt in result.attempts or []] == [
        "tool_error",
        "ok",
    ]


def test_cdp_adapter_retries_headless_daemon_start_lock() -> None:
    runner = SequenceRunner(
        [
            ProcessResult(
                returncode=6,
                stdout=(
                    '{"ok":false,"code":"connection_failed","err_class":"connection",'
                    '"message":"browser commands require a running headless cdp daemon; '
                    "automatic headless daemon repair failed: headless keepalive repair "
                    'is locked by pid 82788 in phase starting_daemon"}'
                ),
                stderr="",
            ),
            ProcessResult(
                returncode=3,
                stdout=(
                    '{"ok":false,"code":"connection_failed","err_class":"connection",'
                    '"message":"check browser resource budget: failed to read JSON message: '
                    'failed to get reader: use of closed network connection"}'
                ),
                stderr="",
            ),
            ProcessResult(returncode=0, stdout='{"status":"ok","page_id":"page-2"}', stderr=""),
        ]
    )
    adapter = CdpAdapter(runner=runner)

    result = asyncio.run(adapter.run_json(["open", "https://www.google.com/travel/flights"]))

    assert len(runner.calls) == 3
    assert result.status == "ok"
    assert result.json_payload == {"status": "ok", "page_id": "page-2"}
    assert result.attempt_count == 3
    assert result.max_attempts == 3


def test_cdp_adapter_retries_transient_target_lookup_failure() -> None:
    runner = SequenceRunner(
        [
            ProcessResult(
                returncode=6,
                stdout='{"ok":false,"message":"target_not_found: no target page-1 matched"}',
                stderr="",
            ),
            ProcessResult(returncode=0, stdout='{"status":"ok","items":[]}', stderr=""),
        ]
    )
    adapter = CdpAdapter(runner=runner)

    result = asyncio.run(adapter.run_json(["text", "body", "--target", "page-1"]))

    assert len(runner.calls) == 2
    assert result.status == "ok"
    assert result.attempt_count == 2
    assert result.max_attempts == 3
    assert (result.attempts or [])[0]["error"] == "target_not_found: no target page-1 matched"


def test_cdp_adapter_allows_explicit_headed_mode() -> None:
    runner = FakeRunner()
    adapter = CdpAdapter(runner=runner)

    asyncio.run(adapter.run_json(["pages"], browser_mode="headed", timeout_seconds=12.5))

    assert runner.calls[0][0] == [
        "cdp",
        "--browser-mode",
        "headed",
        "--json",
        "--timeout",
        "12.5s",
        "pages",
    ]
    assert runner.calls[0][1] == 12.5


def test_cdp_adapter_passes_allow_over_budget_flag() -> None:
    runner = FakeRunner()
    adapter = CdpAdapter(runner=runner, allow_over_budget=True)

    asyncio.run(adapter.run_json(["open", "https://www.google.com/travel/flights"]))

    assert runner.calls[0][0] == [
        "cdp",
        "--browser-mode",
        "headless",
        "--json",
        "--timeout",
        "30s",
        "--allow-over-budget",
        "open",
        "https://www.google.com/travel/flights",
    ]


def test_cdp_adapter_reports_invalid_json_as_tool_error() -> None:
    runner = FakeRunner(ProcessResult(returncode=0, stdout="not json", stderr=""))
    adapter = CdpAdapter(runner=runner)

    result = asyncio.run(adapter.run_json(["pages"]))

    assert result.status == "tool_error"
    assert result.exit_code == 6
    assert result.json_payload is None
    assert "invalid JSON" in result.error


def test_cdp_adapter_captures_nonzero_stderr() -> None:
    runner = FakeRunner(ProcessResult(returncode=7, stdout="", stderr="cdp failed"))
    adapter = CdpAdapter(runner=runner)

    result = asyncio.run(adapter.run_json(["pages"]))

    assert result.status == "tool_error"
    assert result.returncode == 7
    assert result.stderr == "cdp failed"
    assert result.exit_code == 6


def test_cdp_adapter_preserves_nonzero_json_stdout() -> None:
    runner = FakeRunner(
        ProcessResult(
            returncode=3,
            stdout='{"ok":false,"code":"connection_failed","message":"missing context"}',
            stderr="",
        )
    )
    adapter = CdpAdapter(runner=runner)

    result = asyncio.run(adapter.run_json(["wait", "load-state", "domcontentloaded"]))

    assert result.status == "tool_error"
    assert result.returncode == 3
    assert result.exit_code == 6
    assert result.json_payload == {
        "ok": False,
        "code": "connection_failed",
        "message": "missing context",
    }
    assert result.error == "missing context"


def test_cdp_adapter_maps_resource_budget_to_browser_stop() -> None:
    runner = FakeRunner(
        ProcessResult(
            returncode=3,
            stdout='{"ok":false,"code":"browser_resource_budget_exceeded","err_class":"resource_budget","message":"browser resource budget exceeded: 25/25 tabs"}',
            stderr="",
        )
    )
    adapter = CdpAdapter(runner=runner)

    result = asyncio.run(adapter.run_json(["open", "https://www.google.com/travel/flights"]))

    assert result.status == "blocked"
    assert result.stop_state == "browser_resource_budget_exceeded"
    assert result.exit_code == 4
    assert result.fallback == {
        "recommended_browser_mode": "headed",
        "reason": "headless blocked or human confirmation required",
    }
    assert "browser resource budget exceeded" in result.error


def test_cdp_adapter_reports_timeout() -> None:
    runner = FakeRunner(error=asyncio.TimeoutError())
    adapter = CdpAdapter(runner=runner)

    result = asyncio.run(adapter.run_json(["pages"], timeout_seconds=1))

    assert result.status == "tool_error"
    assert result.timeout is True
    assert result.exit_code == 6
    assert "timed out" in result.error


def test_run_subprocess_kills_and_reaps_timeout(monkeypatch: object) -> None:
    class HangingProcess:
        returncode: int | None = None
        killed = False
        waited = False

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(60)
            return b"", b""

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            self.waited = True
            return self.returncode or -9

    process = HangingProcess()

    async def fake_create_subprocess_exec(*args: object, **kwargs: object) -> HangingProcess:
        del args, kwargs
        return process

    monkeypatch.setattr(browser.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(browser.run_subprocess(["cdp", "pages"], timeout_seconds=0.01))

    assert process.killed is True
    assert process.waited is True


def test_headless_blocked_payload_recommends_headed_fallback() -> None:
    runner = FakeRunner(
        ProcessResult(
            returncode=0,
            stdout='{"status":"blocked","stop_state":"unusual_traffic"}',
            stderr="",
        )
    )
    adapter = CdpAdapter(runner=runner)

    result = asyncio.run(adapter.run_json(["open", "https://www.google.com/travel/flights"]))

    assert result.status == "blocked"
    assert result.stop_state == "unusual_traffic"
    assert result.fallback == {
        "recommended_browser_mode": "headed",
        "reason": "headless blocked or human confirmation required",
    }


def test_headless_payment_boundary_recommends_headed_fallback() -> None:
    runner = FakeRunner(
        ProcessResult(
            returncode=0,
            stdout='{"status":"payment_or_booking_boundary"}',
            stderr="",
        )
    )
    adapter = CdpAdapter(runner=runner)

    result = asyncio.run(adapter.run_json(["snapshot", "--target", "page-1"]))

    assert result.status == "payment_or_booking_boundary"
    assert result.exit_code == 4
    assert result.fallback == {
        "recommended_browser_mode": "headed",
        "reason": "headless blocked or human confirmation required",
    }
