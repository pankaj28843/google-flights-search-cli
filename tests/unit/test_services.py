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
        702,
        640,
    ]
    assert result["ranking_policy"] == "comfort_aware_v1"
    assert result["ranked_pairs"][0]["departure_date"] == "2026-10-01"
    assert result["ranked_pairs"][0]["evidence"]["source_surfaces"] == ["sqlite-cache"]
    components = {
        component["name"]: component
        for component in result["ranked_pairs"][0]["scoring_explanation"]["components"]
    }
    assert components["preferred_airline"]["value"] == "matched"
    assert components["senior_comfort"]["value"] == "high"
    assert result["ranked_pairs"][1]["evidence"]["source_surfaces"] == ["fake-live-probe"]


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
        FIXTURES,
        project_root=state.root,
        now=now,
    )

    result = results[0]
    assert result["coverage_counts"]["fresh_cache"] == 1
    assert result["pair_coverage"][0]["status"] == "fresh_cache"
    assert result["ranked_pairs"][0]["best_observed_price"]["amount"] == 650


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


def test_replay_selected_itinerary_visible_text_fixture_extracts_detail_fields() -> None:
    exit_code, payload = services.replay_fixture(
        FIXTURES / "selected_itinerary_visible_text_fixture.json"
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["confidence"] == "strong"
    itinerary = payload["itinerary"]
    assert itinerary["summary"]["total_price"] == {
        "amount": 1708,
        "currency": "EUR",
        "text": "EUR 1,708",
    }
    assert itinerary["segments"][0]["flight_number"] == "KL 1268"
    assert itinerary["segments"][0]["airline"] == "KLM"
    assert itinerary["segments"][2]["flight_number"] == "6E 6026"
    assert itinerary["segments"][5]["destination_airport"] == "CPH"
    assert itinerary["layovers"][1] == {
        "direction": "outbound",
        "airport": "DEL",
        "city": "New Delhi",
        "duration_text": "3 hr 35 min",
        "overnight": True,
    }
    assert itinerary["baggage"]["included"] == ["1 free carry-on", "1st checked bag free"]
    assert "Bag fees may be higher at the airport." in itinerary["baggage"]["warnings"]
    assert itinerary["emissions"]["segments"] == [
        "64 kg CO2e",
        "347 kg CO2e",
        "57 kg CO2e",
        "49 kg CO2e",
        "339 kg CO2e",
        "64 kg CO2e",
    ]
    assert "Wi-Fi for a fee" in itinerary["cabin_facilities"]
    assert itinerary["booking_options"][0]["provider"] == "KLM"
    assert itinerary["booking_options"][0]["boundary_control"] == "Continue"
    assert itinerary["baggage_policy_links"][0]["url"].startswith("https://www.klm.co.uk/")
    assert itinerary["terminal_info"]["status"] == "not_found"
    assert itinerary["boundary"]["stop_state"] == "payment_or_booking_boundary"
    assert itinerary["boundary"]["provider_continue_clicked"] is False
    assert itinerary["boundary"]["payment_entered"] is False
    assert payload["unsupported"] == []
    assert payload["evidence"]["source_surfaces"] == ["selected-itinerary-visible-text"]


def test_resolve_route_offline_fixture_returns_airport_choice() -> None:
    exit_code, payload = services.resolve_route("CPH", FIXTURES)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["input_text"] == "CPH"
    assert payload["selected"]["code_or_id"] == "CPH"
    assert payload["selected"]["kind"] == "airport_code"
    assert payload["choices"] == [payload["selected"]]
    assert payload["unsupported"] == []
    assert payload["evidence"]["fixture_id"] == "route-autocomplete-choices-20260607"


def test_resolve_route_offline_fixture_returns_ambiguous_candidates() -> None:
    exit_code, payload = services.resolve_route("Washington DC", FIXTURES)

    assert exit_code == 2
    assert payload["status"] == "ambiguous"
    assert payload["selected"] is None
    assert payload["ambiguity_reason"] == "multi_airport_city_autocomplete"
    assert [choice["code_or_id"] for choice in payload["choices"]] == [
        "/m/0rh6k",
        "DCA",
        "IAD",
        "BWI",
    ]
    assert all(choice["evidence"]["source_surfaces"] for choice in payload["choices"])


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


def test_search_offline_blocks_ambiguous_route_text_without_selected_choice(
    tmp_path: Path,
) -> None:
    intent_path = _write_lucknow_intent(tmp_path, selected_destination=None)

    exit_code, payload = services.search_offline(intent_path, FIXTURES)

    assert exit_code == 2
    assert payload["status"] == "ambiguous"
    assert payload["query_id"] == "cph-lucknow-ambiguous"
    assert payload["route_ambiguities"][0]["field"] == "destination"
    assert [choice["code_or_id"] for choice in payload["route_ambiguities"][0]["choices"]] == [
        "/m/022tq4",
        "LKO",
    ]
    assert payload["results"] == []


def test_search_offline_accepts_selected_route_choice_for_ambiguous_text(
    tmp_path: Path,
) -> None:
    intent_path = _write_lucknow_intent(
        tmp_path,
        selected_destination=_lucknow_city_choice(),
    )

    exit_code, payload = services.search_offline(intent_path, FIXTURES)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["route_resolution"]["status"] == "ok"
    assert payload["route_resolution"]["selected_fields"] == ["destination"]


def test_search_offline_reports_route_ambiguity_per_batch_item(
    tmp_path: Path,
) -> None:
    intent_path = tmp_path / "mixed-route-intents.json"
    intent_path.write_text(
        json.dumps(
            [
                _cph_del_intent(),
                _lucknow_intent_payload(selected_destination=None),
            ]
        )
    )

    exit_code, payload = services.search_offline(intent_path, FIXTURES)

    assert exit_code == 2
    assert isinstance(payload, list)
    assert [item["query_id"] for item in payload] == [
        "cph-del-selected",
        "cph-lucknow-ambiguous",
    ]
    assert [item["status"] for item in payload] == ["ok", "ambiguous"]
    assert payload[1]["route_ambiguities"][0]["field"] == "destination"


def test_search_offline_rejects_unresolved_route_text_without_guessing(
    tmp_path: Path,
) -> None:
    exit_code, payload = services.search_offline(_write_unknown_city_intent(tmp_path), FIXTURES)

    assert exit_code == 3
    assert payload["status"] == "unsupported"
    assert payload["query_id"] == "cph-unknown-route"
    assert payload["results"] == []
    assert payload["route_resolution"]["status"] == "unsupported"
    assert payload["unsupported"][0]["field"] == "route.resolve.input_text"


def test_scan_dates_blocks_ambiguous_route_text_without_selected_choice(
    tmp_path: Path,
) -> None:
    result = services.scan_dates(
        _write_lucknow_intent(tmp_path, selected_destination=None),
        FIXTURES,
    )[0]

    assert result["status"] == "ambiguous"
    assert result["generated_pairs"] == 0
    assert result["ranked_pairs"] == []
    assert result["route_ambiguities"][0]["field"] == "destination"


def test_scan_dates_rejects_unresolved_route_text_without_pairs(
    tmp_path: Path,
) -> None:
    result = services.scan_dates(_write_unknown_city_intent(tmp_path), FIXTURES)[0]

    assert result["status"] == "unsupported"
    assert result["generated_pairs"] == 0
    assert result["ranked_pairs"] == []
    assert result["route_resolution"]["status"] == "unsupported"
    assert result["unsupported"][0]["field"] == "route.resolve.input_text"


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


def _write_lucknow_intent(
    path: Path,
    *,
    selected_destination: dict[str, object] | None,
) -> Path:
    intent_path = path / "lucknow-intent.json"
    intent_path.write_text(json.dumps(_lucknow_intent_payload(selected_destination)))
    return intent_path


def _write_unknown_city_intent(path: Path) -> Path:
    intent_path = path / "unknown-city-intent.json"
    intent_path.write_text(
        json.dumps(
            {
                "query_id": "cph-unknown-route",
                "origin": {"text": "CPH", "kind": "airport_code"},
                "destination": {"text": "Atlantis", "kind": "city_or_airport"},
                "trip_type": "one_way",
                "departure_window": {"start": "2026-06-15", "end": "2026-06-15"},
                "return_window": None,
                "passengers": {"adults": 1},
                "cabin": "economy",
                "currency": "EUR",
                "language": "en",
                "sort": "price",
            }
        )
    )
    return intent_path


def _lucknow_intent_payload(
    selected_destination: dict[str, object] | None,
) -> dict[str, object]:
    destination: dict[str, object] = {"text": "Lucknow", "kind": "city_or_airport"}
    if selected_destination is not None:
        destination["selected"] = selected_destination
    return {
        "query_id": "cph-lucknow-ambiguous",
        "origin": {"text": "CPH", "kind": "airport_code"},
        "destination": destination,
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
        "sort": "price",
    }


def _cph_del_intent() -> dict[str, object]:
    return {
        "query_id": "cph-del-selected",
        "origin": {"text": "CPH", "kind": "airport_code"},
        "destination": {"text": "DEL", "kind": "airport_code"},
        "trip_type": "one_way",
        "departure_window": {"start": "2026-06-15", "end": "2026-06-15"},
        "return_window": None,
        "passengers": {"adults": 1},
        "cabin": "economy",
        "currency": "EUR",
        "language": "en",
        "sort": "price",
    }


def _lucknow_city_choice() -> dict[str, object]:
    return {
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
                "route_autocomplete_choices_fixture.json",
                "decode-report.md",
            ],
        },
    }
