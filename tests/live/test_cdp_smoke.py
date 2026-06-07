from __future__ import annotations

import asyncio
import os

import pytest

from gflights.browser import CdpAdapter


@pytest.mark.live_cdp
def test_cdp_doctor_smoke_reports_headless_runtime() -> None:
    if os.environ.get("GFLIGHTS_RUN_LIVE_CDP") != "1":
        pytest.skip("set GFLIGHTS_RUN_LIVE_CDP=1 to run live cdp smoke")

    result = asyncio.run(CdpAdapter().run_json(["doctor"]))

    assert result.status == "ok"
    assert result.browser_mode == "headless"
    assert result.json_payload is not None
    assert result.json_payload["ok"] is True


@pytest.mark.live_cdp
def test_cdp_pages_smoke_reaches_headless_browser_runtime() -> None:
    if os.environ.get("GFLIGHTS_RUN_LIVE_CDP") != "1":
        pytest.skip("set GFLIGHTS_RUN_LIVE_CDP=1 to run live cdp smoke")

    result = asyncio.run(CdpAdapter().run_json(["pages"]))

    assert result.status == "ok"
    assert result.browser_mode == "headless"
    assert result.json_payload is not None
    assert result.json_payload["ok"] is True
    assert result.json_payload["budget"]["browser_mode"] == "headless"
