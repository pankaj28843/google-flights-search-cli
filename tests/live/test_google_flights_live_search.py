from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.live_google_flights
def test_live_google_flights_search_smoke_captures_task_scoped_artifacts(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            "uv",
            "run",
            "--quiet",
            "gflights",
            "search",
            "--input-json",
            str(ROOT / "tests/e2e/fixtures/search_intents.json"),
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
    assert isinstance(payload, list)
    assert [item["query_id"] for item in payload] == [
        "del-cph-senior-oct-nov",
        "cph-lko-oneway-jun",
    ]
    for item in payload:
        assert item["live_mode"] is True
        assert item["browser_mode"] == "headless"
        assert item["status"] in {"ok", "experimental", "blocked"}
        assert item["evidence"]["run_id"].startswith("gf-")
        assert item["query_population"]["status"] in {"encoded", "unsupported"}
        assert (tmp_path / "runs" / item["evidence"]["run_id"]).is_dir()
        for artifact in item["evidence"]["artifacts"]:
            assert Path(artifact).is_file()
    assert payload[0]["query_population"]["status"] == "unsupported"
    assert payload[1]["query_population"]["status"] == "encoded"
    assert "tfs=" in payload[1]["target_url"]

    if result.returncode == 4:
        assert any(
            item.get("fallback", {}).get("recommended_browser_mode") == "headed" for item in payload
        )
    for item in payload:
        if item["status"] == "experimental":
            assert any(entry["field"] == "live_result_extraction" for entry in item["unsupported"])
        if item["status"] == "ok":
            assert item["results"]
