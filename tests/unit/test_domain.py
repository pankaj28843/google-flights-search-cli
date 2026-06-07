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


def test_route_endpoint_accepts_selected_route_choice_contract() -> None:
    intent = SearchIntent.model_validate(
        base_intent(
            destination={
                "text": "Washington DC",
                "kind": "city_or_airport",
                "selected": {
                    "text": "Washington, USA",
                    "kind": "city",
                    "display_name": "Washington, USA",
                    "code_or_id": "/m/0rh6k",
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

    assert intent.destination.selected is not None
    assert intent.destination.selected.kind == "city"
    assert intent.destination.selected.code_or_id == "/m/0rh6k"
    assert intent.destination.selected.evidence["source_surfaces"] == [
        "route-autocomplete-visible-text",
        "protobuf-decode-report",
    ]


def test_selected_route_choice_requires_audit_evidence() -> None:
    with pytest.raises(ValidationError, match="route choices require evidence"):
        SearchIntent.model_validate(
            base_intent(
                destination={
                    "text": "Lucknow",
                    "kind": "city_or_airport",
                    "selected": {
                        "text": "Lucknow, Uttar Pradesh, India",
                        "kind": "city",
                        "display_name": "Lucknow, Uttar Pradesh, India",
                        "code_or_id": None,
                        "confidence": "weak",
                        "evidence": {},
                    },
                }
            )
        )


def test_decoded_city_route_choice_requires_decode_evidence() -> None:
    with pytest.raises(ValidationError, match="decoded city route ids require"):
        SearchIntent.model_validate(
            base_intent(
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
                            "source_surfaces": ["route-autocomplete-visible-text"],
                            "artifacts": ["route_autocomplete_visible_text_fixture.json"],
                        },
                    },
                }
            )
        )


def test_airport_route_choice_requires_visible_iata_code() -> None:
    with pytest.raises(ValidationError, match="airport route choices require"):
        SearchIntent.model_validate(
            base_intent(
                destination={
                    "text": "Chaudhary Charan Singh International Airport",
                    "kind": "city_or_airport",
                    "selected": {
                        "text": "Chaudhary Charan Singh International Airport",
                        "kind": "airport_code",
                        "display_name": "Chaudhary Charan Singh International Airport",
                        "code_or_id": None,
                        "confidence": "weak",
                        "evidence": {
                            "source_surfaces": ["route-autocomplete-visible-text"],
                            "artifacts": ["route_autocomplete_visible_text_fixture.json"],
                        },
                    },
                }
            )
        )
