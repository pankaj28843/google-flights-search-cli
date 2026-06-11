"""Pure extraction of primary flight result rows from visible evidence."""

from __future__ import annotations

import re
from typing import Any

_CURRENCY_BY_SYMBOL = {
    "\u20ac": "EUR",
    "$": "USD",
    "\u00a3": "GBP",
    "\u20b9": "INR",
}
_CURRENCY_BY_NAME = {
    "danish kroner": "DKK",
    "dollar": "USD",
    "dollars": "USD",
    "euro": "EUR",
    "euros": "EUR",
    "indian rupee": "INR",
    "indian rupees": "INR",
    "norwegian kroner": "NOK",
    "pound": "GBP",
    "pounds": "GBP",
    "rupee": "INR",
    "rupees": "INR",
    "swedish kroner": "SEK",
    "swedish kronor": "SEK",
    "us dollar": "USD",
    "us dollars": "USD",
}
_CURRENCY_CODES = {"DKK", "EUR", "GBP", "INR", "NOK", "SEK", "USD"}
_PRICE_PATTERN = r"(?:[\u20ac$£₹]\s?[\d,]+|(?:DKK|EUR|GBP|INR|NOK|SEK|USD)\s*[\d,]+)"
_PRICE_NAME_RE = re.compile(
    r"(?P<amount>[\d,]+)\s+"
    r"(?P<name>us dollars?|dollars?|euros?|pounds?|indian rupees?|rupees?|"
    r"danish kroner|norwegian kroner|swedish kronor|swedish kroner)",
    re.IGNORECASE,
)

_ROW_RE = re.compile(
    r"(?P<departure_time>\d{1,2}:\d{2}\s+[AP]M)\s+[\u2013-]\s+"
    r"(?P<arrival_time>\d{1,2}:\d{2}\s+[AP]M(?:\+\d+)?)\s+"
    r"(?P<carriers>.+?)\s+"
    r"(?P<duration>\d+\s+hr(?:\s+\d+\s+min)?|\d+\s+min)\s+"
    r"(?P<route>[A-Z]{3}[\u2013-][A-Z]{3})\s+"
    r"(?P<stops>\d+\s+stops?|Nonstop)\s+"
    r"(?P<layovers>.*?)\s+"
    r"(?P<emissions>\d[\d,]*\s+kg\s+CO2e(?:\s+(?:[+-]\d+%\s+emissions|Avg emissions))?)\s+"
    rf"(?P<price>{_PRICE_PATTERN})(?:\s+round trip)?",
)
_COMPACT_ROW_RE = re.compile(
    r"(?P<departure_time>\d{1,2}:\d{2}\s+[AP]M)\s+"
    r"(?P<origin>[A-Z]{3})\s+"
    r"(?P<arrival_time>\d{1,2}:\d{2}\s+[AP]M(?:\+\d+)?)\s+"
    r"(?P<destination>[A-Z]{3})\s+"
    r"(?:(?:Economy|Premium Economy|Business|First|\+|\d+|\s)+\s+)?"
    rf"(?P<price>{_PRICE_PATTERN})\s+round trip\s+"
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
_TIME_ROUTE_RE = re.compile(
    r"(?P<departure_time>\d{1,2}:\d{2}\s+[AP]M)\s+"
    r"(?P<origin>[A-Z]{3})\s+"
    r"(?P<arrival_time>\d{1,2}:\d{2}\s+[AP]M(?:\+\d+)?)\s+"
    r"(?P<destination>[A-Z]{3})"
)
_AIRPORT_CODE_RE = re.compile(r"\(([A-Z]{3})\)")
_TIME_RE = re.compile(r"\d{1,2}:\d{2}\s+[AP]M(?:\+\d+)?")
_DURATION_RE = re.compile(
    r"(?:Total duration\s*)?(?P<duration>\d+\s+hr(?:\s+\d+\s+min)?|\d+\s+min)",
    re.IGNORECASE,
)
_EMISSIONS_RE = re.compile(
    r"(?P<emissions>\d[\d,]*\s+kg\s+CO2e(?:\s+(?:[+-]\d+%\s+emissions|Avg emissions))?|"
    r"[+-]\d+%\s+emissions|Avg emissions)",
    re.IGNORECASE,
)


def extract_primary_results(
    snapshot_payload: dict[str, Any],
    *,
    source_surface: str = "primary-results-visible-text",
    evidence_artifact: str | None = None,
    confidence: str = "weak",
) -> list[dict[str, Any]]:
    accessible_results = _extract_accessible_rows(
        snapshot_payload,
        source_surface=source_surface,
        evidence_artifact=evidence_artifact,
        confidence=confidence,
    )
    if accessible_results:
        return accessible_results

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


def _extract_accessible_rows(
    payload: dict[str, Any],
    *,
    source_surface: str,
    evidence_artifact: str | None,
    confidence: str,
) -> list[dict[str, Any]]:
    rows = _accessible_rows(payload)
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        parsed = _accessible_row_result(
            index,
            row,
            source_surface=source_surface,
            evidence_artifact=evidence_artifact,
            confidence=confidence,
        )
        if parsed is not None:
            results.append(parsed)
    return results


def _accessible_row_result(
    index: int,
    row: dict[str, Any],
    *,
    source_surface: str,
    evidence_artifact: str | None,
    confidence: str,
) -> dict[str, Any] | None:
    text = _normalized_str(row.get("text"))
    aria_label = _normalized_str(row.get("ariaLabel"))
    combined_text = _normalized_str(row.get("combinedText") or f"{text} {aria_label}")
    parse_text = combined_text or text or aria_label
    if not parse_text:
        return None

    route = _parse_accessible_route(text=text, aria_label=aria_label, combined_text=parse_text)
    price = _parse_price_from_text(parse_text)
    stops = _parse_stops_from_text(parse_text)
    duration_text = _parse_duration_text(parse_text)
    if price is None or stops is None or duration_text is None:
        return None

    carriers = _parse_accessible_carriers(parse_text, duration_text=duration_text)
    result: dict[str, Any] = {
        "result_id": f"accessible-row-result-{index}",
        "source_surface": source_surface,
        "confidence": confidence,
        "origin_airports": [route["origin"]] if route.get("origin") else [],
        "destination_airports": [route["destination"]] if route.get("destination") else [],
        "departure_times": [route["departure_time"]] if route.get("departure_time") else [],
        "arrival_times": [route["arrival_time"]] if route.get("arrival_time") else [],
        "carriers": carriers,
        "flight_numbers": _parse_flight_numbers(parse_text),
        "duration_text": duration_text,
        "duration_minutes": _parse_duration_minutes(duration_text),
        "stops": stops,
        "layovers": _parse_accessible_layovers(parse_text),
        "price": price,
        "currency": price["currency"],
        "emissions": _parse_emissions(parse_text),
        "baggage_summary": _parse_baggage_summary(parse_text),
        "cabin": _parse_cabin(parse_text),
        "cabin_facilities": _parse_cabin_facilities(parse_text),
        "aria_label": aria_label,
        "locator_strategy": _normalized_str(row.get("locatorStrategy")) or None,
        "row_rank": row.get("rank") if isinstance(row.get("rank"), int) else index,
        "warnings": [],
        "evidence": {
            "source_surfaces": [source_surface],
            "artifacts": [evidence_artifact] if evidence_artifact else [],
        },
    }
    return result


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
    if "oops, something went wrong" in text.casefold() or (
        "no results returned" in text.casefold() and "reload" in text.casefold()
    ):
        return "google_page_error"
    if "loading results" in text.casefold() and not _has_visible_fare_rows(text):
        return "loading_results"
    if _NO_RESULTS_RE.search(text):
        return "no_results"
    return "unknown"


def _has_visible_fare_rows(text: str) -> bool:
    return bool(_ROW_RE.search(text) or _COMPACT_ROW_RE.search(text))


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
    normalized = re.sub(r"\s+", " ", value).strip()
    code_match = re.match(r"(?P<code>[A-Z]{3})\s*(?P<amount>[\d,]+)$", normalized)
    if code_match:
        code = code_match.group("code")
        return {
            "amount": int(code_match.group("amount").replace(",", "")),
            "currency": code if code in _CURRENCY_CODES else "unknown",
            "text": normalized,
        }

    symbol = normalized[0]
    amount = int(re.sub(r"[^\d]", "", normalized))
    return {
        "amount": amount,
        "currency": _CURRENCY_BY_SYMBOL.get(symbol, "unknown"),
        "text": normalized.replace(" ", ""),
    }


def _accessible_rows(payload: dict[str, Any]) -> list[Any]:
    rows = payload.get("accessible_rows")
    if isinstance(rows, list):
        return rows

    value = _eval_value(payload)
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("accessible_rows"), list):
        return value["accessible_rows"]
    return []


def _eval_value(payload: dict[str, Any]) -> Any:
    for key in ("value", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            if "value" in value:
                return value["value"]
            nested_result = value.get("result")
            if isinstance(nested_result, dict) and "value" in nested_result:
                return nested_result["value"]
    return None


def _normalized_str(value: Any) -> str:
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""


def _parse_price_from_text(text: str) -> dict[str, Any] | None:
    match = re.search(_PRICE_PATTERN, text, re.IGNORECASE)
    if match:
        return _parse_price(match.group(0))

    name_match = _PRICE_NAME_RE.search(text)
    if not name_match:
        return None
    name = name_match.group("name").casefold()
    amount = int(name_match.group("amount").replace(",", ""))
    return {
        "amount": amount,
        "currency": _CURRENCY_BY_NAME.get(name, "unknown"),
        "text": f"{name_match.group('amount')} {name_match.group('name')}",
    }


def _parse_stops_from_text(text: str) -> dict[str, Any] | None:
    if re.search(r"\bnonstop\b", text, re.IGNORECASE):
        return {"count": 0, "text": "Nonstop"}
    match = re.search(r"\b(?P<count>\d+)\s+stops?\b", text, re.IGNORECASE)
    if match:
        count = int(match.group("count"))
        return {"count": count, "text": f"{count} stop" if count == 1 else f"{count} stops"}
    return None


def _parse_duration_text(text: str) -> str | None:
    match = _DURATION_RE.search(text)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group("duration")).strip()


def _parse_accessible_carriers(text: str, *, duration_text: str) -> list[str]:
    carrier_match = re.search(r"\bflight with (?P<carrier>[^,.]+)", text, re.IGNORECASE)
    if carrier_match:
        return _parse_carriers(carrier_match.group("carrier"))

    select_match = re.search(r"\bSelect flight,\s*(?P<carrier>[^,]+)", text, re.IGNORECASE)
    if select_match:
        return _parse_carriers(select_match.group("carrier"))

    duration_match = re.search(re.escape(duration_text), text, re.IGNORECASE)
    if duration_match:
        compact_prefix = text[: duration_match.start()]
        compact_match = re.search(
            r"\d{1,2}:\d{2}\s+[AP]M(?:\s+[A-Z]{3})?\s+[\u2013-]\s+"
            r"\d{1,2}:\d{2}\s+[AP]M(?:\+\d+)?(?:\s+[A-Z]{3})?\s+"
            r"(?P<carrier>.+?)\s*$",
            compact_prefix,
        )
        if compact_match:
            carriers = _parse_carriers(compact_match.group("carrier"))
            if carriers:
                return carriers
        suffix = text[duration_match.end() :]
        suffix = _EMISSIONS_RE.split(suffix, maxsplit=1)[0]
        suffix = re.split(r"\b(?:Select flight|From|Price)\b", suffix, maxsplit=1)[0]
        carriers = _parse_carriers(suffix)
        if carriers:
            return carriers
    return []


def _parse_accessible_route(
    *,
    text: str,
    aria_label: str,
    combined_text: str,
) -> dict[str, str | None]:
    route_match = (
        _TIME_ROUTE_RE.search(text)
        or _TIME_ROUTE_RE.search(aria_label)
        or _TIME_ROUTE_RE.search(combined_text)
    )
    if route_match:
        return {
            "origin": route_match.group("origin"),
            "destination": route_match.group("destination"),
            "departure_time": route_match.group("departure_time"),
            "arrival_time": route_match.group("arrival_time"),
        }

    airport_codes = _AIRPORT_CODE_RE.findall(text) or _AIRPORT_CODE_RE.findall(combined_text)
    times = _TIME_RE.findall(text) or _TIME_RE.findall(combined_text)
    return {
        "origin": airport_codes[0] if len(airport_codes) >= 1 else None,
        "destination": airport_codes[1] if len(airport_codes) >= 2 else None,
        "departure_time": times[0] if len(times) >= 1 else None,
        "arrival_time": times[1] if len(times) >= 2 else None,
    }


def _parse_flight_numbers(text: str) -> list[str]:
    numbers = re.findall(r"\b[A-Z0-9]{2}\s?\d{2,4}\b", text)
    return sorted({number.replace(" ", "") for number in numbers})


def _parse_accessible_layovers(text: str) -> list[str]:
    return sorted(
        {match.group(1) for match in re.finditer(r"\bstop(?:s)?\s+(?:in\s+)?([A-Z]{3})\b", text)}
    )


def _parse_emissions(text: str) -> dict[str, str] | None:
    match = _EMISSIONS_RE.search(text)
    if not match:
        return None
    return {"text": re.sub(r"\s+", " ", match.group("emissions")).strip()}


def _parse_baggage_summary(text: str) -> dict[str, Any] | None:
    carry_on = _bag_count(text, r"carry[- ]?on")
    checked = _bag_count(text, r"checked")
    if carry_on is None and checked is None:
        return None
    summary: dict[str, Any] = {
        "text": _baggage_text(text),
        "carry_on_bags_included": carry_on,
        "checked_bags_included": checked,
    }
    if checked is not None:
        summary["checked_bag_allowed"] = checked > 0
    if carry_on is not None:
        summary["carry_on_bag_allowed"] = carry_on > 0
    return summary


def _bag_count(text: str, bag_kind_pattern: str) -> int | None:
    included = re.search(
        rf"\b(?P<count>\d+)\s+{bag_kind_pattern}\s+bags?\s+included\b",
        text,
        re.IGNORECASE,
    )
    if included:
        return int(included.group("count"))
    if re.search(rf"\b{bag_kind_pattern}\s+bags?\s+not\s+included\b", text, re.IGNORECASE):
        return 0
    return None


def _baggage_text(text: str) -> str:
    fragments = re.findall(
        r"\b\d+\s+(?:carry[- ]?on|checked)\s+bags?\s+included\b|"
        r"\b(?:carry[- ]?on|checked)\s+bags?\s+not\s+included\b",
        text,
        re.IGNORECASE,
    )
    return "; ".join(fragment.strip() for fragment in fragments)


def _parse_cabin(text: str) -> str | None:
    for cabin in ("Premium Economy", "Business", "First", "Economy"):
        if re.search(rf"\b{re.escape(cabin)}\b", text, re.IGNORECASE):
            return cabin.lower().replace(" ", "_")
    return None


def _parse_cabin_facilities(text: str) -> list[str]:
    patterns = (
        r"(?:below |above |average )?legroom(?: \([^)]+\))?",
        r"(?:Free )?Wi-Fi(?: for a fee)?",
        r"In-seat USB outlets?",
        r"In-seat power (?:&|and) USB outlets?",
        r"In-seat power outlets?",
        r"On-demand video",
        r"Live TV",
        r"Often delayed by [^,.]+",
    )
    facilities: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            value = match.group(0).strip()
            if value not in facilities:
                facilities.append(value)
    return facilities
