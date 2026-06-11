from __future__ import annotations

import base64

import pytest

from gflights.domain import SearchIntent
from gflights.query_state import UnsupportedQueryState, build_query_state


ROUND_TRIP_TFS = (
    "CBwQAhokEgoyMDI2LTA2LTE1agcIARIDQ1BIcg0IAxIJL20vMDIydHE0"
    "GiQSCjIwMjYtMDYtMjJqDQgDEgkvbS8wMjJ0cTRyBwgBEgNDUEhAAUgBcAGCAQsI"
    "____________AZgBAQ"
)
ONE_WAY_TFS = (
    "CBwQAhokEgoyMDI2LTA2LTE1agcIARIDQ1BIcg0IAxIJL20vMDIydHE0QAFIAXABggELCP___________wGYAQI"
)


def intent(**overrides: object) -> SearchIntent:
    payload = {
        "query_id": "encoded-cph-lko",
        "origin": {"text": "CPH", "kind": "airport_code"},
        "destination": {"text": "Lucknow", "kind": "city_or_airport"},
        "trip_type": "round_trip",
        "departure_window": {"start": "2026-06-15", "end": "2026-06-15"},
        "return_window": {"start": "2026-06-22", "end": "2026-06-22"},
        "passengers": {
            "adults": 1,
            "children": 0,
            "infants_in_seat": 0,
            "infants_on_lap": 0,
        },
        "cabin": "economy",
        "currency": "EUR",
        "language": "en",
        "sort": "top_flights",
    }
    payload.update(overrides)
    return SearchIntent.model_validate(payload)


def test_build_query_state_reproduces_observed_cph_lucknow_round_trip_tfs() -> None:
    state = build_query_state(
        intent(
            destination={
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
                            "route_autocomplete_choices_fixture.json",
                            "decode-report.md",
                        ],
                    },
                },
            }
        )
    )

    assert state.params["tfs"] == ROUND_TRIP_TFS
    assert state.params["hl"] == "en"
    assert state.params["curr"] == "EUR"
    assert "tfu" not in state.params
    assert state.confidence == "strong"
    assert "query-state:tfs" in state.source_surfaces


def test_build_query_state_reproduces_observed_cph_lucknow_one_way_price_sort() -> None:
    state = build_query_state(
        intent(
            trip_type="one_way",
            return_window=None,
            sort="price",
            destination={
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
                            "route_autocomplete_choices_fixture.json",
                            "decode-report.md",
                        ],
                    },
                },
            },
        )
    )

    assert state.params["tfs"] == ONE_WAY_TFS
    assert state.params["tfu"] == "EgYIAhAAGAA"
    assert state.source_surfaces == ["query-state:tfs", "query-state:tfu"]
    assert any(
        "ranking objectives are still applied after row extraction" in warning
        for warning in state.warnings
    )


def test_build_query_state_rejects_unexpanded_date_windows() -> None:
    with pytest.raises(UnsupportedQueryState) as error:
        build_query_state(intent(departure_window={"start": "2026-10-01", "end": "2026-10-07"}))

    assert error.value.field == "departure_window"
    assert "one concrete departure date" in error.value.reason


def test_build_query_state_rejects_unselected_city_or_airport_text() -> None:
    with pytest.raises(UnsupportedQueryState) as error:
        build_query_state(intent())

    assert error.value.field == "destination"
    assert "selected route choice" in error.value.reason


def test_build_query_state_uses_selected_airport_choice_code() -> None:
    state = build_query_state(
        intent(
            destination={
                "text": "Lucknow",
                "kind": "city_or_airport",
                "selected": {
                    "text": "Chaudhary Charan Singh International Airport LKO",
                    "kind": "airport_code",
                    "display_name": "Chaudhary Charan Singh International Airport",
                    "code_or_id": "LKO",
                    "confidence": "weak",
                    "evidence": {
                        "source_surfaces": ["route-autocomplete-visible-text"],
                        "artifacts": ["route_autocomplete_visible_text_fixture.json"],
                    },
                },
            }
        )
    )

    assert state.params["tfs"] != ROUND_TRIP_TFS
    padded = state.params["tfs"] + "=" * (-len(state.params["tfs"]) % 4)
    assert b"LKO" in base64.urlsafe_b64decode(padded)
