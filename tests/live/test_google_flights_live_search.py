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
    input_json = tmp_path / "live-smoke-intents.json"
    input_json.write_text(
        json.dumps(
            [
                {
                    "query_id": "del-cph-window-oct-nov",
                    "origin": {"text": "Delhi", "kind": "city_or_airport"},
                    "destination": {"text": "Copenhagen", "kind": "city_or_airport"},
                    "trip_type": "round_trip",
                    "departure_window": {"start": "2026-10-01", "end": "2026-10-07"},
                    "return_window": {"start": "2026-11-24", "end": "2026-11-30"},
                    "passengers": {
                        "adults": 2,
                        "children": 0,
                        "infants_in_seat": 0,
                        "infants_on_lap": 0,
                    },
                    "cabin": "economy",
                    "currency": "EUR",
                    "language": "en",
                    "location": None,
                    "sort": "top_flights",
                },
                {
                    "query_id": "cph-lko-oneway-jun",
                    "origin": {"text": "CPH", "kind": "airport_code"},
                    "destination": {
                        "text": "Lucknow",
                        "kind": "city_or_airport",
                        "selected": {
                            "text": "Lucknow, Uttar Pradesh, India",
                            "kind": "city",
                            "display_name": "Lucknow, Uttar Pradesh, India",
                            "code_or_id": "/m/022tq4",
                            "confidence": "strong",
                            "evidence": {
                                "source_surfaces": [
                                    "route-autocomplete-visible-text",
                                    "protobuf-decode-report",
                                ],
                                "artifacts": [
                                    "reviewed-route-choice.json",
                                    "decode-report.md",
                                ],
                            },
                        },
                    },
                    "trip_type": "one_way",
                    "departure_window": {"start": "2026-06-15", "end": "2026-06-15"},
                    "return_window": None,
                    "passengers": {
                        "adults": 1,
                        "children": 0,
                        "infants_in_seat": 0,
                        "infants_on_lap": 0,
                    },
                    "cabin": "economy",
                    "currency": "EUR",
                    "language": "en",
                    "location": None,
                    "sort": "price",
                },
            ],
            indent=2,
        )
    )

    result = subprocess.run(
        [
            "uv",
            "run",
            "--quiet",
            "gflights",
            "search",
            "--input-json",
            str(input_json),
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

    assert result.returncode in {0, 3, 4}, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert [item["query_id"] for item in payload] == [
        "del-cph-window-oct-nov",
        "cph-lko-oneway-jun",
    ]
    for item in payload:
        assert item["live_mode"] is True
        assert item["browser_mode"] == "headless"
        assert item["status"] in {"ok", "experimental", "unsupported", "blocked"}
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
    if result.returncode == 3:
        assert any(item["status"] == "unsupported" for item in payload)
    for item in payload:
        if item["status"] == "experimental":
            assert any(entry["field"] == "live_result_extraction" for entry in item["unsupported"])
        if item["status"] == "unsupported":
            assert any(
                entry["field"].startswith("live_result_extraction") for entry in item["unsupported"]
            )
        if item["status"] == "ok":
            assert item["results"]
