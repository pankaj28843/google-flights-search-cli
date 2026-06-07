from __future__ import annotations

import pytest
from pydantic import ValidationError

from gflights.domain import DateWindow, PassengerParty, SearchIntent


def base_intent(**overrides: object) -> dict[str, object]:
    intent: dict[str, object] = {
        "query_id": "unit-cph-del",
        "origin": {"text": "CPH", "kind": "airport_code"},
        "destination": {"text": "DEL", "kind": "airport_code"},
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
        "sort": "top_flights",
    }
    intent.update(overrides)
    return intent


def test_date_window_rejects_reversed_ranges() -> None:
    with pytest.raises(ValidationError, match="date window start"):
        DateWindow.model_validate({"start": "2026-10-07", "end": "2026-10-01"})


def test_passenger_party_requires_at_least_one_traveler() -> None:
    with pytest.raises(ValidationError, match="at least one traveler"):
        PassengerParty(adults=0, children=0, infants_in_seat=0, infants_on_lap=0)


def test_round_trip_requires_return_window() -> None:
    with pytest.raises(ValidationError, match="round_trip requires return_window"):
        SearchIntent.model_validate(base_intent(return_window=None))


def test_one_way_must_not_retain_return_window() -> None:
    with pytest.raises(ValidationError, match="one_way must not include return_window"):
        SearchIntent.model_validate(base_intent(trip_type="one_way"))


def test_business_cabin_and_supported_sort_validate_without_side_effects() -> None:
    intent = SearchIntent.model_validate(base_intent(cabin="business", sort="price"))

    assert intent.cabin == "business"
    assert intent.sort == "price"
    assert intent.origin.text == "CPH"
