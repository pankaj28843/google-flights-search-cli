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
        "del-cph-window-oct-nov",
        "cph-lko-oneway-jun",
    ]
    assert all(item["status"] == "ok" for item in parsed)


def test_scan_dates_counts_round_trip_window_pairs(tmp_path: Path) -> None:
    results = services.scan_dates(
        FIXTURES / "search_intents.json",
        project_root=tmp_path / "state",
    )

    assert results[0]["generated_pairs"] == 49
    assert results[0]["status"] == "experimental"
    assert results[0]["ranked_pairs"] == []
    assert results[1]["generated_pairs"] == 1
    assert results[1]["pair_coverage"][0]["return_date"] is None


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
    assert result["ranking_policy"] == "price_duration_v1"
    assert result["ranked_pairs"][0]["departure_date"] == "2026-10-02"
    assert result["ranked_pairs"][0]["evidence"]["source_surfaces"] == ["fake-live-probe"]
    components = {
        component["name"]: component
        for component in result["ranked_pairs"][0]["scoring_explanation"]["components"]
    }
    assert components["price"]["value"] == 640
    assert components["stops"]["value"] == 2
    assert result["ranked_pairs"][1]["evidence"]["source_surfaces"] == ["sqlite-cache"]


def test_scan_dates_top_k_limits_ranked_pairs_not_coverage(tmp_path: Path) -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    state = init_app_state(tmp_path / "state")
    cache = PriceCache(state.database_path)
    for departure, price in [("2026-10-01", 701), ("2026-10-02", 650)]:
        cache.put_price(
            cache_key=f"del-cph-{departure}-2026-11-24",
            query_id="del-cph-window",
            departure_date=departure,
            return_date="2026-11-24",
            currency="EUR",
            price_amount=price,
            price_payload={
                "result_id": f"cache-{departure}",
                "price": {"amount": price, "currency": "EUR", "text": f"EUR {price}"},
                "duration_minutes": 900,
                "stops": {"count": 1, "text": "1 stop"},
            },
            captured_at=now,
            source_run_id="gf-cache-top-k",
        )

    result = services.scan_dates(
        _write_window_intent(tmp_path),
        project_root=state.root,
        now=now,
        objective="cheapest",
        top_k=1,
    )[0]

    assert result["generated_pairs"] == 2
    assert len(result["pair_coverage"]) == 2
    assert [pair["best_observed_price"]["amount"] for pair in result["ranked_pairs"]] == [650]
    assert result["objective"] == "cheapest"
    assert result["top_k"] == 1


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


def test_scan_dates_records_live_probe_limit_skips(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    state = init_app_state(tmp_path / "state")
    intent_path = _write_window_intent(tmp_path)

    def fake_probe(_concrete_intent: object) -> dict[str, object]:
        return {
            "status": "skipped",
            "reason": "max_live_probes_reached",
            "results": [],
            "unsupported": [],
            "warnings": ["date pair skipped because max live probe limit was reached"],
            "evidence": {
                "run_id": "date-scan-live-probe-limit",
                "source_surfaces": ["date-scan-live-probe-limit"],
                "artifacts": [],
            },
        }

    results = services.scan_dates(
        intent_path,
        project_root=state.root,
        now=now,
        date_pair_probe=fake_probe,
    )

    result = results[0]
    assert result["status"] == "experimental"
    assert result["coverage_counts"] == {
        "fresh_cache": 0,
        "probed": 0,
        "unsupported": 0,
        "skipped": 2,
    }
    assert [pair["status"] for pair in result["pair_coverage"]] == ["skipped", "skipped"]
    assert {pair["reason"] for pair in result["pair_coverage"]} == {"max_live_probes_reached"}
    assert any("max_live_probes_reached" in warning for warning in result["warnings"])
    assert "date-scan-live-probe-limit" in result["evidence"]["source_surfaces"]


def test_scan_dates_uses_configured_cache_max_age(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    state = init_app_state(tmp_path / "state")
    config = json.loads(state.config_path.read_text())
    config["cache_max_age_seconds"] = 8 * 60 * 60
    state.config_path.write_text(json.dumps(config, indent=2) + "\n")
    PriceCache(state.database_path).put_price(
        cache_key="seven-hour-del-cph-2026-10-01-2026-11-24",
        query_id="del-cph-window",
        departure_date="2026-10-01",
        return_date="2026-11-24",
        currency="EUR",
        price_amount=650,
        price_payload={
            "result_id": "seven-hour-ai-650",
            "price": {"amount": 650, "currency": "EUR", "text": "EUR 650"},
        },
        captured_at=now - timedelta(hours=7),
        source_run_id="gf-cache-seven-hour",
    )

    results = services.scan_dates(
        _write_single_pair_intent(tmp_path),
        project_root=state.root,
        now=now,
    )

    result = results[0]
    assert result["coverage_counts"]["fresh_cache"] == 1
    assert result["pair_coverage"][0]["status"] == "fresh_cache"
    assert result["ranked_pairs"][0]["best_observed_price"]["amount"] == 650


def test_project_init_creates_config_and_artifact_root(tmp_path: Path) -> None:
    payload = services.init_project(tmp_path)
    config = json.loads((tmp_path / "config.json").read_text())

    assert payload["status"] == "ok"
    assert (tmp_path / "cache").is_dir()
    assert (tmp_path / "cache" / "cache.sqlite").is_file()
    assert (tmp_path / "artifacts").is_dir()
    assert not (tmp_path / "fixtures").exists()
    assert (tmp_path / "runs").is_dir()
    assert config["browser_default_mode"] == "headed"
    assert config["browser_max_tabs"] == 5
    assert config["live_google_flights_by_default"] is True
    assert config["cache_root"] == str(tmp_path / "cache")
    assert config["database_path"] == str(tmp_path / "cache" / "cache.sqlite")
    assert "fixture_root" not in config
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
                "cabin": "economy",
                "currency": "EUR",
                "language": "en",
                "sort": "price",
            }
        )
    )
    return intent_path


def _write_single_pair_intent(path: Path) -> Path:
    intent_path = path / "single-pair-intent.json"
    intent_path.write_text(
        json.dumps(
            {
                "query_id": "del-cph-window",
                "origin": {"text": "Delhi", "kind": "city_or_airport"},
                "destination": {"text": "Copenhagen", "kind": "city_or_airport"},
                "trip_type": "round_trip",
                "departure_window": {"start": "2026-10-01", "end": "2026-10-01"},
                "return_window": {"start": "2026-11-24", "end": "2026-11-24"},
                "passengers": {"adults": 2},
                "cabin": "economy",
                "currency": "EUR",
                "language": "en",
                "sort": "price",
            }
        )
    )
    return intent_path
