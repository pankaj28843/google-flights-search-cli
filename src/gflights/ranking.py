"""Pure post-result ranking policies for visible or cached flight observations."""

from __future__ import annotations

from copy import deepcopy
import re
from collections.abc import Sequence
from typing import Any

from gflights.domain import SearchIntent

RankingObjective = str
SUPPORTED_OBJECTIVES = {"cheapest", "fastest", "least_layover", "balanced"}


def rank_observed_pairs(
    intent: SearchIntent,
    pairs: list[dict[str, Any]],
    *,
    objective: RankingObjective = "balanced",
    top_k: int | None = None,
    allow_transit: Sequence[str] | None = None,
    deny_transit: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Rank observed date pairs without claiming Google Flights filters were applied."""

    normalized_objective = normalize_objectives([objective])[0]
    enriched: list[tuple[Any, dict[str, Any]]] = []
    for index, pair in enumerate(pairs):
        ranked_pair = deepcopy(pair)
        explanation = _score_pair(
            intent,
            ranked_pair,
            objective=normalized_objective,
            allow_transit=allow_transit,
            deny_transit=deny_transit,
        )
        ranked_pair["scoring_explanation"] = explanation
        enriched.append(
            (_sort_key(ranked_pair, explanation, normalized_objective, index), ranked_pair)
        )
    ranked = [pair for _key, pair in sorted(enriched, key=lambda item: item[0])]
    return ranked[:top_k] if top_k and top_k > 0 else ranked


def rank_observed_results(
    results: list[dict[str, Any]],
    *,
    objectives: Sequence[str] | None = None,
    top_k: int = 10,
    allow_transit: Sequence[str] | None = None,
    deny_transit: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return top-K result rows by each requested post-result objective."""

    normalized = normalize_objectives(objectives or ["balanced"])
    limit = max(1, top_k)
    top: dict[str, Any] = {
        "ranking": {
            "objectives": normalized,
            "top_k": limit,
            "google_flights_filters_applied": False,
            "source": "post_result_visible_rows",
            "transit_policy": {
                "allow": _normalized_airports(allow_transit),
                "deny": _normalized_airports(deny_transit),
            },
        }
    }
    for objective in normalized:
        rows: list[tuple[Any, dict[str, Any]]] = []
        for index, result in enumerate(results):
            ranked_result = deepcopy(result)
            explanation = _score_result(
                ranked_result,
                objective=objective,
                allow_transit=allow_transit,
                deny_transit=deny_transit,
            )
            ranked_result["scoring_explanation"] = explanation
            rows.append((_sort_key(ranked_result, explanation, objective, index), ranked_result))
        top[f"top_{objective}"] = [row for _key, row in sorted(rows, key=lambda item: item[0])][
            :limit
        ]
    return top


def normalize_objectives(objectives: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    for raw in objectives:
        value = raw.strip().replace("-", "_")
        if not value:
            continue
        if value not in SUPPORTED_OBJECTIVES:
            continue
        if value not in normalized:
            normalized.append(value)
    return normalized or ["balanced"]


def _score_pair(
    intent: SearchIntent,
    pair: dict[str, Any],
    *,
    objective: RankingObjective,
    allow_transit: Sequence[str] | None,
    deny_transit: Sequence[str] | None,
) -> dict[str, Any]:
    del intent
    summary = _summary(pair)
    price = _price_amount(pair)
    duration = _duration_minutes(pair)
    stops = _stop_count(summary)
    emissions_kg = _emissions_kg(summary.get("emissions"))
    layovers = summary.get("layovers")
    max_layover = _max_layover_minutes(layovers)
    disallowed = _disallowed_transit_airports(layovers, allow_transit, deny_transit)
    overnight = _has_overnight_arrival(summary)

    duration_weight = 0.2
    stop_weight = 30
    layover_weight = 0.25
    emissions_weight = 0.02
    disallowed_penalty = 100000
    overnight_penalty = 250

    score = (
        float(price)
        + (duration * duration_weight if duration < 999999 else 0)
        + (stops * stop_weight if stops < 999999 else 0)
        + ((max_layover or 0) * layover_weight)
        + ((emissions_kg or 0) * emissions_weight)
        + (len(disallowed) * disallowed_penalty)
        + (overnight_penalty if overnight else 0)
    )
    return {
        "policy": "price_duration_v1",
        "objective": objective,
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
                "name": "max_layover_minutes",
                "value": max_layover,
                "weight": layover_weight,
                "effect": "lower_is_better_when_visible",
            },
            {
                "name": "emissions_kg",
                "value": emissions_kg,
                "weight": emissions_weight,
                "effect": "lower_is_better_when_visible",
            },
            {
                "name": "disallowed_transit_airports",
                "value": disallowed,
                "weight": disallowed_penalty,
                "effect": "avoid_when_visible",
            },
            {
                "name": "overnight_arrival_or_layover",
                "value": overnight,
                "weight": overnight_penalty,
                "effect": "avoid_when_visible",
            },
        ],
    }


def _score_result(
    result: dict[str, Any],
    *,
    objective: RankingObjective,
    allow_transit: Sequence[str] | None,
    deny_transit: Sequence[str] | None,
) -> dict[str, Any]:
    pair_like = {
        "best_observed_price": result.get("price"),
        "top_result_summary": result,
    }
    return _score_pair(
        SearchIntent.model_construct(query_id="ranking"),
        pair_like,
        objective=objective,
        allow_transit=allow_transit,
        deny_transit=deny_transit,
    )


def _sort_key(
    item: dict[str, Any],
    explanation: dict[str, Any],
    objective: RankingObjective,
    index: int,
) -> tuple[Any, ...]:
    summary = _summary(item) or item
    price = _price_amount(item) if "best_observed_price" in item else _result_price_amount(item)
    duration = _duration_minutes(item) if "top_result_summary" in item else _result_duration(item)
    stops = _stop_count(summary)
    max_layover = _max_layover_minutes(summary.get("layovers"))
    disallowed_count = len(_component_value(explanation, "disallowed_transit_airports") or [])
    if objective == "cheapest":
        return (disallowed_count, price, duration, stops, index)
    if objective == "fastest":
        return (disallowed_count, duration, stops, price, index)
    if objective == "least_layover":
        return (disallowed_count, max_layover or 999999, stops, duration, price, index)
    return (explanation["score"], price, duration, index)


def _summary(pair: dict[str, Any]) -> dict[str, Any]:
    summary = pair.get("top_result_summary")
    return summary if isinstance(summary, dict) else {}


def _price_amount(pair: dict[str, Any]) -> int | float:
    price = pair.get("best_observed_price")
    if isinstance(price, dict) and isinstance(price.get("amount"), int | float):
        return price["amount"]
    return 999999


def _result_price_amount(result: dict[str, Any]) -> int | float:
    price = result.get("price")
    if isinstance(price, dict) and isinstance(price.get("amount"), int | float):
        return price["amount"]
    return 999999


def _duration_minutes(pair: dict[str, Any]) -> int:
    value = _summary(pair).get("duration_minutes")
    return int(value) if isinstance(value, int | float) else 999999


def _result_duration(result: dict[str, Any]) -> int:
    value = result.get("duration_minutes")
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


def _max_layover_minutes(value: Any) -> int | None:
    if not isinstance(value, list):
        return None
    durations = [_duration_from_text(str(item)) for item in value]
    durations = [duration for duration in durations if duration is not None]
    return max(durations) if durations else None


def _duration_from_text(value: str) -> int | None:
    hours = re.search(r"(\d+)\s+hr", value)
    minutes = re.search(r"(\d+)\s+min", value)
    if hours is None and minutes is None:
        return None
    return (int(hours.group(1)) * 60 if hours else 0) + (int(minutes.group(1)) if minutes else 0)


def _disallowed_transit_airports(
    layovers: Any,
    allow_transit: Sequence[str] | None,
    deny_transit: Sequence[str] | None,
) -> list[str]:
    airports = _layover_airports(layovers)
    allowed = set(_normalized_airports(allow_transit))
    denied = set(_normalized_airports(deny_transit))
    disallowed = [airport for airport in airports if airport in denied]
    if allowed:
        disallowed.extend(airport for airport in airports if airport not in allowed)
    return sorted(set(disallowed))


def _layover_airports(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    airports: list[str] = []
    for item in value:
        if isinstance(item, dict):
            code = item.get("airport")
            if isinstance(code, str):
                airports.append(code.upper())
            continue
        airports.extend(match.upper() for match in re.findall(r"\b[A-Z]{3}\b", str(item)))
    return airports


def _normalized_airports(value: Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    return sorted({item.strip().upper() for item in value if item.strip()})


def _has_overnight_arrival(summary: dict[str, Any]) -> bool:
    text_parts: list[str] = []
    for key in ("arrival_times", "layovers", "duration_text"):
        value = summary.get(key)
        if isinstance(value, list):
            text_parts.extend(str(item) for item in value)
        elif value is not None:
            text_parts.append(str(value))
    text = " ".join(text_parts).casefold()
    return "+1" in text or "overnight" in text


def _component_value(explanation: dict[str, Any], name: str) -> Any:
    components = explanation.get("components")
    if not isinstance(components, list):
        return None
    for component in components:
        if isinstance(component, dict) and component.get("name") == name:
            return component.get("value")
    return None
