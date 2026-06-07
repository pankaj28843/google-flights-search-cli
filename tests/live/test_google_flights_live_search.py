from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.live_google_flights
def test_live_google_flights_search_smoke_captures_task_scoped_artifacts(
    tmp_path: Path,
) -> None:
    if os.environ.get("GFLIGHTS_RUN_GOOGLE_FLIGHTS_LIVE") != "1":
        pytest.skip("set GFLIGHTS_RUN_GOOGLE_FLIGHTS_LIVE=1 to open Google Flights")

    result = subprocess.run(
        [
            "uv",
            "run",
            "--quiet",
            "gflights",
            "search",
            "--input-json",
            str(ROOT / "tests/e2e/fixtures/search_intents.json"),
            "--live-cdp",
            "--project-root",
            str(tmp_path),
            "--browser-mode",
            "headless",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )

    assert result.returncode in {0, 4}, result.stderr
    payload = json.loads(result.stdout)
    assert payload["live_mode"] is True
    assert payload["browser_mode"] == "headless"
    assert payload["status"] in {"experimental", "blocked"}
    assert payload["evidence"]["run_id"].startswith("gf-")
    assert (tmp_path / ".gflights" / "runs" / payload["evidence"]["run_id"]).is_dir()
    for artifact in payload["evidence"]["artifacts"]:
        assert Path(artifact).is_file()

    if result.returncode == 4:
        assert payload["fallback"]["recommended_browser_mode"] == "headed"
    else:
        assert payload["unsupported"][0]["field"] == "live_result_extraction"
