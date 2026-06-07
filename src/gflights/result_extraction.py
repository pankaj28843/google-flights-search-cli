"""Pure extraction of primary flight result rows from visible evidence."""

from __future__ import annotations

import re
from typing import Any

_CURRENCY_BY_SYMBOL = {
    "\u20ac": "EUR",
    "$": "USD",
    "\u00a3": "GBP",
}

_ROW_RE = re.compile(
    r"(?P<departure_time>\d{1,2}:\d{2}\s+[AP]M)\s+[\u2013-]\s+"
    r"(?P<arrival_time>\d{1,2}:\d{2}\s+[AP]M(?:\+\d+)?)\s+"
    r"(?P<carriers>.+?)\s+"
    r"(?P<duration>\d+\s+hr(?:\s+\d+\s+min)?|\d+\s+min)\s+"
    r"(?P<route>[A-Z]{3}[\u2013-][A-Z]{3})\s+"
    r"(?P<stops>\d+\s+stops?|Nonstop)\s+"
    r"(?P<layovers>.*?)\s+"
    r"(?P<emissions>\d[\d,]*\s+kg\s+CO2e(?:\s+(?:[+-]\d+%\s+emissions|Avg emissions))?)\s+"
    r"(?P<price>[\u20ac$£]\s?[\d,]+)(?:\s+round trip)?",
)
_COMPACT_ROW_RE = re.compile(
    r"(?P<departure_time>\d{1,2}:\d{2}\s+[AP]M)\s+"
    r"(?P<origin>[A-Z]{3})\s+"
    r"(?P<arrival_time>\d{1,2}:\d{2}\s+[AP]M(?:\+\d+)?)\s+"
    r"(?P<destination>[A-Z]{3})\s+"
    r"(?:(?:Economy|Premium Economy|Business|First|\+|\s)+\s+)?"
    r"(?P<price>[\u20ac$£]\s?[\d,]+)\s+round trip\s+"
    r"(?P<stops>\d+\s+stops?|Nonstop)\s*"
    r"(?P<duration>\d+\s+hr(?:\s+\d+\s+min)?|\d+\s+min)"
    r"(?P<carriers>.+?)\s+"
    r"(?P<emissions>(?:[+-]\d+%\s+emissions|Avg emissions|"
    r"\d[\d,]*\s+kg\s+CO2e(?:\s+(?:[+-]\d+%\s+emissions|Avg emissions))?))",
)
_NO_RESULTS_RE = re.compile(
    r"\b(no flights|no results|try changing|could(?:n['\u2019]?t| not) find)\b",
    re.IGNORECASE,
)


def extract_primary_results(
    snapshot_payload: dict[str, Any],
    *,
    source_surface: str = "primary-results-visible-text",
    evidence_artifact: str | None = None,
    confidence: str = "weak",
) -> list[dict[str, Any]]:
    text = _visible_text(snapshot_payload)
    if not text:
        return []

    results = _extract_legacy_rows(
        text,
        source_surface=source_surface,
        evidence_artifact=evidence_artifact,
        confidence=confidence,
    )
    if results:
        return results
    return _extract_compact_rows(
        text,
        source_surface=source_surface,
        evidence_artifact=evidence_artifact,
        confidence=confidence,
    )


def _extract_legacy_rows(
    text: str,
    *,
    source_surface: str,
    evidence_artifact: str | None,
    confidence: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, match in enumerate(_ROW_RE.finditer(text), start=1):
        route = _split_route(match.group("route"))
        price = _parse_price(match.group("price"))
        stops = _parse_stops(match.group("stops"))
        result = {
            "result_id": f"visible-text-result-{index}",
            "source_surface": source_surface,
            "confidence": confidence,
            "origin_airports": [route[0]] if route else [],
            "destination_airports": [route[1]] if route else [],
            "departure_times": [match.group("departure_time")],
            "arrival_times": [match.group("arrival_time")],
            "carriers": _parse_carriers(match.group("carriers")),
            "flight_numbers": [],
            "duration_text": match.group("duration"),
            "duration_minutes": _parse_duration_minutes(match.group("duration")),
            "stops": stops,
            "layovers": _parse_layovers(match.group("layovers")),
            "price": price,
            "currency": price["currency"] if price else None,
            "emissions": {"text": match.group("emissions")},
            "baggage_summary": None,
            "warnings": [],
            "evidence": {
                "source_surfaces": [source_surface],
                "artifacts": [evidence_artifact] if evidence_artifact else [],
            },
        }
        results.append(result)
    return results


def _extract_compact_rows(
    text: str,
    *,
    source_surface: str,
    evidence_artifact: str | None,
    confidence: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, match in enumerate(_COMPACT_ROW_RE.finditer(text), start=1):
        price = _parse_price(match.group("price"))
        stops = _parse_stops(match.group("stops"))
        result = {
            "result_id": f"visible-text-result-{index}",
            "source_surface": source_surface,
            "confidence": confidence,
            "origin_airports": [match.group("origin")],
            "destination_airports": [match.group("destination")],
            "departure_times": [match.group("departure_time")],
            "arrival_times": [match.group("arrival_time")],
            "carriers": _parse_carriers(match.group("carriers")),
            "flight_numbers": [],
            "duration_text": match.group("duration"),
            "duration_minutes": _parse_duration_minutes(match.group("duration")),
            "stops": stops,
            "layovers": [],
            "price": price,
            "currency": price["currency"],
            "emissions": {"text": match.group("emissions")},
            "baggage_summary": None,
            "warnings": [],
            "evidence": {
                "source_surfaces": [source_surface],
                "artifacts": [evidence_artifact] if evidence_artifact else [],
            },
        }
        results.append(result)
    return results


def classify_primary_result_absence(snapshot_payload: dict[str, Any]) -> str:
    """Classify visible no-row states without guessing flight availability."""

    text = _visible_text(snapshot_payload)
    if not text.strip():
        return "empty_snapshot"
    if "loading results" in text.casefold():
        return "loading_results"
    if _NO_RESULTS_RE.search(text):
        return "no_results"
    return "unknown"


def _visible_text(payload: dict[str, Any]) -> str:
    text = payload.get("text")
    if isinstance(text, str):
        return text
    if isinstance(text, dict):
        nested_text = text.get("text")
        if isinstance(nested_text, str):
            return nested_text
        items = text.get("items")
        item_text = _first_item_text(items)
        if item_text:
            return item_text

    items = payload.get("items")
    item_text = _first_item_text(items)
    if item_text:
        return item_text

    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict):
        return _visible_text(snapshot)
    return ""


def _first_item_text(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            return item["text"]
    return ""


def _split_route(route: str) -> tuple[str, str] | None:
    parts = re.split(r"[\u2013-]", route)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def _parse_carriers(value: str) -> list[str]:
    visible_carriers = value.split("Operated by", 1)[0]
    return [carrier.strip() for carrier in visible_carriers.split(",") if carrier.strip()]


def _parse_duration_minutes(value: str) -> int:
    hours = re.search(r"(\d+)\s+hr", value)
    minutes = re.search(r"(\d+)\s+min", value)
    return (int(hours.group(1)) * 60 if hours else 0) + (int(minutes.group(1)) if minutes else 0)


def _parse_stops(value: str) -> dict[str, Any]:
    if value == "Nonstop":
        return {"count": 0, "text": value}
    count = int(value.split()[0])
    return {"count": count, "text": value}


def _parse_layovers(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_price(value: str) -> dict[str, Any]:
    symbol = value.strip()[0]
    amount = int(re.sub(r"[^\d]", "", value))
    return {
        "amount": amount,
        "currency": _CURRENCY_BY_SYMBOL.get(symbol, "unknown"),
        "text": value.replace(" ", ""),
    }
