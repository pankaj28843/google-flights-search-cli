from __future__ import annotations

import json
from pathlib import Path

import pytest

from gflights import services

FIXTURES = Path(__file__).resolve().parents[1] / "e2e" / "fixtures"


def test_schema_for_search_intent_exports_contract_properties() -> None:
    schema = services.json_schema_for("search-intent")

    assert schema["title"] == "SearchIntent"
    assert "query_id" in schema["properties"]
    assert "passengers" in schema["properties"]


def test_unknown_schema_model_returns_unsupported_exit_code() -> None:
    with pytest.raises(services.ServiceError) as error:
        services.json_schema_for("booking-result")

    assert error.value.exit_code == 3
    assert error.value.payload["status"] == "unsupported"


def test_parse_intents_preserves_batch_order() -> None:
    parsed = services.parse_intents(FIXTURES / "search_intents.json")

    assert [item["query_id"] for item in parsed] == [
        "del-cph-senior-oct-nov",
        "cph-lko-oneway-jun",
    ]
    assert all(item["status"] == "ok" for item in parsed)


def test_scan_dates_counts_round_trip_window_pairs() -> None:
    results = services.scan_dates(FIXTURES / "search_intents.json", FIXTURES)

    assert results[0]["generated_pairs"] == 49
    assert results[0]["ranked_pairs"][0]["scoring_explanation"]["policy"]
    assert results[1]["generated_pairs"] == 1
    assert results[1]["ranked_pairs"][0]["return_date"] is None


def test_replay_fixture_returns_result_evidence() -> None:
    exit_code, payload = services.replay_fixture(FIXTURES / "offline_results_fixture.json")

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["results"][0]["carriers"] == ["KLM", "IndiGo"]
    assert payload["evidence"]["run_id"] == "gf-20260607-073838-04-result-controls-cheap-dates"


def test_blocked_fixture_returns_stop_exit_code() -> None:
    exit_code, payload = services.replay_fixture(FIXTURES / "blocked_headless_fixture.json")

    assert exit_code == 4
    assert payload["status"] == "blocked"
    assert payload["fallback"]["recommended_browser_mode"] == "headed"


def test_codec_decode_preserves_confidence_boundary() -> None:
    payload = services.decode_codec_fixture(FIXTURES / "codec_tfu_price_fixture.json")

    assert payload["confidence"] == "strong"
    assert payload["confidence"] != "proven"
    assert payload["wire_paths"][0]["path"] == "tfu.2.1"
    assert payload["codec"]["round_trip_ok"] is True
    assert payload["codec"]["round_trip_value"] == "EgYIAhAAGAA"
    assert {"path": "tfu.2.1", "wire_type": "varint", "value": 2} in payload["codec"][
        "observed_wire_paths"
    ]


def test_codec_decode_marks_fixture_stale_when_expected_wire_path_is_missing(
    tmp_path: Path,
) -> None:
    fixture = json.loads((FIXTURES / "codec_tfu_price_fixture.json").read_text())
    fixture["expected"]["wire_paths"][0]["path"] = "tfu.99"
    stale_fixture = tmp_path / "stale-codec-fixture.json"
    stale_fixture.write_text(json.dumps(fixture))

    payload = services.decode_codec_fixture(stale_fixture)

    assert payload["status"] == "stale_fixture"
    assert payload["confidence"] == "unknown"
    assert "tfu.99" in payload["warnings"][0]


def test_search_returns_unsupported_for_deferred_live_layover_filter() -> None:
    exit_code, payload = services.search_offline(
        FIXTURES / "unsupported_live_filter_intent.json",
        FIXTURES,
    )

    assert exit_code == 3
    assert payload["status"] == "unsupported"
    assert payload["unsupported"][0]["field"] == "google_filters.maximum_layover_minutes"


def test_project_init_creates_config_and_artifact_root(tmp_path: Path) -> None:
    payload = services.init_project(tmp_path)
    config = json.loads((tmp_path / ".gflights" / "config.json").read_text())

    assert payload["status"] == "ok"
    assert (tmp_path / ".gflights" / "artifacts").is_dir()
    assert (tmp_path / ".gflights" / "fixtures").is_dir()
    assert (tmp_path / ".gflights" / "runs").is_dir()
    assert config["browser_default_mode"] == "headless"
    assert config["fixture_root"] == str(tmp_path / ".gflights" / "fixtures")
    assert config["run_root"] == str(tmp_path / ".gflights" / "runs")
