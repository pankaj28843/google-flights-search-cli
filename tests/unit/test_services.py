from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from gflights import services
from gflights.app_state import PriceCache, init_app_state

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


def test_scan_dates_uses_fresh_cache_or_probe_for_every_generated_pair(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    state = init_app_state(tmp_path / "state")
    cache = PriceCache(state.database_path)
    cache.put_price(
        cache_key="cached-del-cph-2026-10-01-2026-11-24",
        query_id="del-cph-window",
        departure_date="2026-10-01",
        return_date="2026-11-24",
        currency="EUR",
        price_amount=702,
        price_payload={
            "result_id": "cached-ai-702",
            "price": {"amount": 702, "currency": "EUR", "text": "EUR 702"},
            "carriers": ["Air India"],
            "duration_minutes": 870,
            "stops": {"count": 1, "text": "1 stop"},
        },
        captured_at=now - timedelta(hours=1),
        source_run_id="gf-cache-fresh",
    )
    intent_path = _write_window_intent(tmp_path)
    probed_dates: list[tuple[str, str | None]] = []

    def fake_probe(concrete_intent: object) -> dict[str, object]:
        intent = concrete_intent  # typed by runtime pydantic model in the service contract
        probed_dates.append(
            (
                getattr(intent.departure_window, "start"),
                getattr(intent.return_window, "start", None),
            )
        )
        return {
            "status": "ok",
            "results": [
                {
                    "result_id": "live-klm-640",
                    "price": {"amount": 640, "currency": "EUR", "text": "EUR 640"},
                    "carriers": ["KLM"],
                    "duration_minutes": 930,
                    "stops": {"count": 2, "text": "2 stops"},
                }
            ],
            "unsupported": [],
            "warnings": [],
            "evidence": {
                "run_id": "gf-live-probe",
                "source_surfaces": ["fake-live-probe"],
                "artifacts": ["probe.json"],
            },
        }

    results = services.scan_dates(
        intent_path,
        FIXTURES,
        project_root=state.root,
        now=now,
        date_pair_probe=fake_probe,
    )

    result = results[0]
    assert result["generated_pairs"] == 2
    assert result["coverage_counts"] == {
        "fresh_cache": 1,
        "probed": 1,
        "unsupported": 0,
        "skipped": 0,
    }
    assert probed_dates == [("2026-10-02", "2026-11-24")]
    assert [(pair["departure_date"], pair["status"]) for pair in result["pair_coverage"]] == [
        ("2026-10-01", "fresh_cache"),
        ("2026-10-02", "probed"),
    ]
    assert [pair["best_observed_price"]["amount"] for pair in result["ranked_pairs"]] == [
        640,
        702,
    ]
    assert result["ranked_pairs"][0]["departure_date"] == "2026-10-02"
    assert result["ranked_pairs"][0]["evidence"]["source_surfaces"] == ["fake-live-probe"]
    assert result["ranked_pairs"][1]["evidence"]["source_surfaces"] == ["sqlite-cache"]


def test_scan_dates_without_cache_or_probe_records_skipped_pairs(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    state = init_app_state(tmp_path / "state")
    PriceCache(state.database_path).put_price(
        cache_key="stale-del-cph-2026-10-01-2026-11-24",
        query_id="del-cph-window",
        departure_date="2026-10-01",
        return_date="2026-11-24",
        currency="EUR",
        price_amount=650,
        price_payload={
            "result_id": "stale-ai-650",
            "price": {"amount": 650, "currency": "EUR", "text": "EUR 650"},
        },
        captured_at=now - timedelta(hours=7),
        source_run_id="gf-cache-stale",
    )
    intent_path = _write_window_intent(tmp_path)

    results = services.scan_dates(
        intent_path,
        FIXTURES,
        project_root=state.root,
        now=now,
    )

    result = results[0]
    assert result["status"] == "experimental"
    assert result["generated_pairs"] == 2
    assert result["ranked_pairs"] == []
    assert result["coverage_counts"] == {
        "fresh_cache": 0,
        "probed": 0,
        "unsupported": 0,
        "skipped": 2,
    }
    assert {pair["status"] for pair in result["pair_coverage"]} == {"skipped"}
    assert all(pair["evidence"]["source_surfaces"] for pair in result["pair_coverage"])


def test_replay_fixture_returns_result_evidence() -> None:
    exit_code, payload = services.replay_fixture(FIXTURES / "offline_results_fixture.json")

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["results"][0]["carriers"] == ["KLM", "IndiGo"]
    assert payload["evidence"]["run_id"] == "gf-20260607-073838-04-result-controls-cheap-dates"


def test_replay_visible_text_fixture_extracts_primary_result_rows() -> None:
    exit_code, payload = services.replay_fixture(
        FIXTURES / "primary_results_visible_text_fixture.json"
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["results"][0]["carriers"] == ["KLM", "IndiGo"]
    assert payload["results"][0]["duration_minutes"] == 920
    assert payload["results"][0]["stops"] == {"count": 2, "text": "2 stops"}
    assert payload["results"][0]["price"] == {"amount": 3206, "currency": "EUR", "text": "€3,206"}
    assert payload["results"][0]["source_surface"] == "primary-results-visible-text"
    assert payload["evidence"]["source_surfaces"] == ["primary-results-visible-text"]


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
    config = json.loads((tmp_path / "config.json").read_text())

    assert payload["status"] == "ok"
    assert (tmp_path / "artifacts").is_dir()
    assert (tmp_path / "fixtures").is_dir()
    assert (tmp_path / "runs").is_dir()
    assert config["browser_default_mode"] == "headless"
    assert config["live_google_flights_by_default"] is True
    assert config["fixture_root"] == str(tmp_path / "fixtures")
    assert config["run_root"] == str(tmp_path / "runs")


def _write_window_intent(path: Path) -> Path:
    intent_path = path / "date-window-intent.json"
    intent_path.write_text(
        json.dumps(
            {
                "query_id": "del-cph-window",
                "origin": {"text": "Delhi", "kind": "city_or_airport"},
                "destination": {"text": "Copenhagen", "kind": "city_or_airport"},
                "trip_type": "round_trip",
                "departure_window": {"start": "2026-10-01", "end": "2026-10-02"},
                "return_window": {"start": "2026-11-24", "end": "2026-11-24"},
                "passengers": {
                    "adults": 2,
                    "children": 0,
                    "infants_in_seat": 0,
                    "infants_on_lap": 0,
                },
                "traveler_profiles": [{"kind": "senior", "comfort_weight": "high"}],
                "cabin": "economy",
                "airline_preferences": [{"airline": "Air India", "mode": "preferred"}],
                "consider_all_airlines": True,
                "currency": "EUR",
                "language": "en",
                "sort": "price",
            }
        )
    )
    return intent_path
