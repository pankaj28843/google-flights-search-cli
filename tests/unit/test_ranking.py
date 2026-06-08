from __future__ import annotations

from gflights.domain import SearchIntent
from gflights.ranking import rank_observed_pairs


def test_price_duration_ranking_prefers_lower_scored_visible_observation() -> None:
    ranked = rank_observed_pairs(
        _intent(),
        [
            _pair(
                "cheap-long-two-stop",
                price=640,
                carriers=["KLM"],
                duration_minutes=1800,
                stops=2,
                emissions="800 kg CO2e",
            ),
            _pair(
                "balanced-one-stop",
                price=700,
                carriers=["Air India"],
                duration_minutes=870,
                stops=1,
                emissions="900 kg CO2e",
            ),
        ],
    )

    assert [pair["top_result_summary"]["result_id"] for pair in ranked] == [
        "balanced-one-stop",
        "cheap-long-two-stop",
    ]
    explanation = ranked[0]["scoring_explanation"]
    assert explanation["policy"] == "price_duration_v1"
    assert explanation["google_flights_filters_applied"] is False
    components = {component["name"]: component for component in explanation["components"]}
    assert components["price"]["value"] == 700
    assert components["stops"]["value"] == 1
    assert components["duration_minutes"]["value"] == 870


def test_price_ranking_wins_when_duration_and_stops_are_close() -> None:
    ranked = rank_observed_pairs(
        _intent(),
        [
            _pair(
                "one-stop-more-expensive",
                price=720,
                carriers=["Air India"],
                duration_minutes=870,
                stops=1,
                emissions="900 kg CO2e",
            ),
            _pair(
                "cheapest",
                price=640,
                carriers=["KLM"],
                duration_minutes=930,
                stops=2,
                emissions="800 kg CO2e",
            ),
        ],
    )

    assert [pair["top_result_summary"]["result_id"] for pair in ranked] == [
        "cheapest",
        "one-stop-more-expensive",
    ]
    components = {
        component["name"]: component for component in ranked[0]["scoring_explanation"]["components"]
    }
    assert components["price"]["value"] == 640
    assert "airline_preference" not in ranked[0]["scoring_explanation"]


def _intent() -> SearchIntent:
    return SearchIntent.model_validate(
        {
            "query_id": "visible-observation-ranking",
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


def _pair(
    result_id: str,
    *,
    price: int,
    carriers: list[str],
    duration_minutes: int,
    stops: int,
    emissions: str,
) -> dict[str, object]:
    return {
        "departure_date": "2026-10-01",
        "return_date": "2026-11-24",
        "best_observed_price": {"amount": price, "currency": "EUR", "text": f"EUR {price}"},
        "result_count": 1,
        "top_result_summary": {
            "result_id": result_id,
            "carriers": carriers,
            "duration_minutes": duration_minutes,
            "stops": {"count": stops, "text": f"{stops} stops"},
            "emissions": {"text": emissions},
        },
        "evidence": {"source_surfaces": ["unit-test"], "artifacts": []},
    }
