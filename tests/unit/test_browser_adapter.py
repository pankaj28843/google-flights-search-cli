from __future__ import annotations

import asyncio
from collections.abc import Sequence

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
