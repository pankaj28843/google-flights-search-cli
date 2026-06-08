"""Pure post-result ranking policies for visible or cached flight observations."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from gflights.domain import SearchIntent


def rank_observed_pairs(
    intent: SearchIntent,
    pairs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rank observed date pairs without claiming Google Flights filters were applied."""

    enriched: list[tuple[float, int | float, int, int, dict[str, Any]]] = []
    for index, pair in enumerate(pairs):
        ranked_pair = deepcopy(pair)
        explanation = _score_pair(intent, ranked_pair)
        ranked_pair["scoring_explanation"] = explanation
        enriched.append(
            (
                explanation["score"],
                _price_amount(ranked_pair),
                _duration_minutes(ranked_pair),
                index,
                ranked_pair,
            )
        )
    return [pair for *_sort_values, pair in sorted(enriched, key=lambda item: item[:4])]


def _score_pair(intent: SearchIntent, pair: dict[str, Any]) -> dict[str, Any]:
    del intent
    summary = _summary(pair)
    price = _price_amount(pair)
    duration = _duration_minutes(pair)
    stops = _stop_count(summary)
    emissions_kg = _emissions_kg(summary.get("emissions"))

    duration_weight = 0.05
    stop_weight = 30
    emissions_weight = 0.02

    score = (
        float(price)
        + (duration * duration_weight if duration < 999999 else 0)
        + (stops * stop_weight if stops < 999999 else 0)
        + ((emissions_kg or 0) * emissions_weight)
    )
    return {
        "policy": "price_duration_v1",
        "score": round(score, 3),
        "google_flights_filters_applied": False,
        "components": [
            {"name": "price", "value": price, "effect": "lower_is_better"},
            {
                "name": "duration_minutes",
                "value": None if duration >= 999999 else duration,
                "weight": duration_weight,
                "effect": "lower_is_better",
            },
            {
                "name": "stops",
                "value": None if stops >= 999999 else stops,
                "weight": stop_weight,
                "effect": "lower_is_better",
            },
            {
                "name": "emissions_kg",
                "value": emissions_kg,
                "weight": emissions_weight,
                "effect": "lower_is_better_when_visible",
            },
        ],
    }


def _summary(pair: dict[str, Any]) -> dict[str, Any]:
    summary = pair.get("top_result_summary")
    return summary if isinstance(summary, dict) else {}


def _price_amount(pair: dict[str, Any]) -> int | float:
    price = pair.get("best_observed_price")
    if isinstance(price, dict) and isinstance(price.get("amount"), int | float):
        return price["amount"]
    return 999999


def _duration_minutes(pair: dict[str, Any]) -> int:
    value = _summary(pair).get("duration_minutes")
    return int(value) if isinstance(value, int | float) else 999999


def _stop_count(summary: dict[str, Any]) -> int:
    stops = summary.get("stops")
    if isinstance(stops, dict) and isinstance(stops.get("count"), int | float):
        return int(stops["count"])
    return 999999


def _emissions_kg(value: Any) -> int | None:
    text = value.get("text") if isinstance(value, dict) else value
    if not isinstance(text, str):
        return None
    match = re.search(r"(\d[\d,]*)\s+kg\s+CO2e", text)
    if match is None:
        return None
    return int(match.group(1).replace(",", ""))
