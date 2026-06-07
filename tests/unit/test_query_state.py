from __future__ import annotations

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
    state = build_query_state(intent())

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
        )
    )

    assert state.params["tfs"] == ONE_WAY_TFS
    assert state.params["tfu"] == "EgYIAhAAGAA"
    assert state.source_surfaces == ["query-state:tfs", "query-state:tfu"]


def test_build_query_state_rejects_unexpanded_date_windows() -> None:
    with pytest.raises(UnsupportedQueryState) as error:
        build_query_state(intent(departure_window={"start": "2026-10-01", "end": "2026-10-07"}))

    assert error.value.field == "departure_window"
    assert "one concrete departure date" in error.value.reason
