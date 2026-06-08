"""Pure extraction of selected-itinerary details from visible evidence."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

_PRICE_RE = re.compile(r"(?P<currency>EUR|DKK)\s+(?P<amount>[\d,]+)")
_AIRPORT_RE = re.compile(r"\(([A-Z]{3})\)")
_SEGMENT_LINE_RE = re.compile(r"\([A-Z]{3}\)\s+to\s+.*\([A-Z]{3}\)")
_FLIGHT_LINE_RE = re.compile(
    r"^(?P<airline>.+?)\s+Economy\s+(?P<aircraft>.+?)\s+flight\s+"
    r"(?P<flight_number>[A-Z0-9]{2}\s+\d+)$"
)
_LAYOVER_RE = re.compile(
    r"^(?P<duration>\d+\s+hr(?:\s+\d+\s+min)?|\d+\s+min)\s+layover\s+"
    r"(?P<city>.*?)\s+\((?P<airport>[A-Z]{3})\)$"
)
_ITINERARY_EMISSIONS_RE = re.compile(
    r"^(?P<text>\d[\d,]*\s+kg\s+CO2e)\s+(?P<comparison>[+-]\d+%\s+emissions)$"
)


def extract_selected_itinerary(
    snapshot_record: dict[str, Any],
    *,
    source_surface: str = "selected-itinerary-visible-text",
    confidence: str = "weak",
) -> dict[str, Any]:
    lines = _visible_lines(snapshot_record.get("snapshot", {}))
    text = "\n".join(lines)
    segments = _segments(lines)
    layovers = _layovers(lines)
    booking_options = _booking_options(lines)
    return {
        "summary": _summary(lines),
        "segments": segments,
        "layovers": layovers,
        "baggage": _baggage(lines),
        "emissions": {
            "itinerary": _itinerary_emissions(lines),
            "segments": [
                line.removeprefix("Emissions estimate: ").strip()
                for line in lines
                if line.startswith("Emissions estimate: ")
            ],
        },
        "cabin_facilities": _cabin_facilities(lines),
        "booking_options": booking_options,
        "baggage_policy_links": _baggage_policy_links(
            lines,
            snapshot_record.get("decoded_links"),
        ),
        "terminal_info": _terminal_info(text),
        "boundary": _boundary(booking_options),
        "source_surface": source_surface,
        "confidence": confidence,
    }


def _visible_lines(payload: dict[str, Any]) -> list[str]:
    text = _visible_text(payload)
    return [line.strip() for line in text.splitlines() if line.strip()]


def _visible_text(payload: dict[str, Any]) -> str:
    text = payload.get("text")
    if isinstance(text, str):
        return text

    items = payload.get("items")
    if isinstance(items, list):
        return "\n".join(
            item["text"]
            for item in items
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )

    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict):
        return _visible_text(snapshot)
    return ""


def _summary(lines: list[str]) -> dict[str, Any]:
    route = _first_line_with(lines, " to ")
    origin, destination = _split_route_text(route)
    total_price = _first_price(lines)
    return {
        "origin": origin,
        "destination": destination,
        "trip_type": _trip_type(lines),
        "cabin": _cabin(lines),
        "passengers": _first_matching(lines, lambda line: "passenger" in line),
        "total_price": total_price,
    }


def _split_route_text(route: str | None) -> tuple[str | None, str | None]:
    if not route or " to " not in route:
        return None, None
    origin, destination = route.split(" to ", 1)
    return origin, destination


def _trip_type(lines: list[str]) -> str | None:
    for line in lines:
        lowered = line.lower()
        if lowered in {"round trip", "one way", "multi-city"}:
            return lowered.replace(" ", "_")
    return None


def _cabin(lines: list[str]) -> str | None:
    for line in lines:
        lowered = line.lower()
        if lowered in {"economy", "premium economy", "business", "first"}:
            return lowered
    return None


def _first_line_with(lines: list[str], needle: str) -> str | None:
    return next((line for line in lines if needle in line), None)


def _first_matching(lines: list[str], predicate: Callable[[str], bool]) -> str | None:
    return next((line for line in lines if predicate(line)), None)


def _first_price(lines: list[str]) -> dict[str, Any] | None:
    for line in lines:
        price = _parse_price(line)
        if price is not None:
            return price
    return None


def _segments(lines: list[str]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    direction: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("Outbound "):
            direction = "outbound"
            index += 1
            continue
        if line.startswith("Return "):
            direction = "return"
            index += 1
            continue

        if direction is not None and _SEGMENT_LINE_RE.search(line):
            airports = _AIRPORT_RE.findall(line)
            duration = _duration_after(lines, index)
            flight_line = _flight_line_after(lines, index)
            flight = _parse_flight_line(flight_line)
            if len(airports) >= 2 and flight is not None:
                segments.append(
                    {
                        "direction": direction,
                        "origin_airport": airports[0],
                        "destination_airport": airports[1],
                        "airline": flight["airline"],
                        "flight_number": flight["flight_number"],
                        "aircraft": flight["aircraft"],
                        "duration_text": duration,
                    }
                )
        index += 1
    return segments


def _duration_after(lines: list[str], index: int) -> str | None:
    for line in lines[index + 1 : index + 4]:
        if line.startswith("Travel time: "):
            return line.removeprefix("Travel time: ").replace(" Overnight", "")
    return None


def _flight_line_after(lines: list[str], index: int) -> str | None:
    for line in lines[index + 1 : index + 5]:
        if _FLIGHT_LINE_RE.match(line):
            return line
    return None


def _parse_flight_line(line: str | None) -> dict[str, str] | None:
    if line is None:
        return None
    match = _FLIGHT_LINE_RE.match(line)
    if not match:
        return None
    return match.groupdict()


def _layovers(lines: list[str]) -> list[dict[str, Any]]:
    layovers: list[dict[str, Any]] = []
    direction: str | None = None
    for index, line in enumerate(lines):
        if line.startswith("Outbound "):
            direction = "outbound"
            continue
        if line.startswith("Return "):
            direction = "return"
            continue

        match = _LAYOVER_RE.match(line)
        if match and direction is not None:
            layovers.append(
                {
                    "direction": direction,
                    "airport": match.group("airport"),
                    "city": match.group("city"),
                    "duration_text": match.group("duration"),
                    "overnight": index + 1 < len(lines)
                    and lines[index + 1].lower() == "overnight layover",
                }
            )
    return layovers


def _baggage(lines: list[str]) -> dict[str, list[str]]:
    if "Baggage" not in lines:
        return {"included": [], "warnings": []}

    included: list[str] = []
    warnings: list[str] = []
    for line in _section_after(lines, "Baggage", stop_headers={"Booking options"}):
        if line == "KLM bag policy":
            continue
        if "free" in line and ("carry-on" in line or "checked bag" in line):
            included.append(line)
        elif line.startswith("Bag") or line.startswith("Baggage"):
            warnings.append(line)
    return {"included": included, "warnings": warnings}


def _section_after(lines: list[str], header: str, *, stop_headers: set[str]) -> list[str]:
    try:
        start = lines.index(header) + 1
    except ValueError:
        return []
    section: list[str] = []
    for line in lines[start:]:
        if line in stop_headers:
            break
        section.append(line)
    return section


def _itinerary_emissions(lines: list[str]) -> list[dict[str, str]]:
    emissions: list[dict[str, str]] = []
    direction: str | None = None
    for line in lines:
        if line.startswith("Outbound "):
            direction = "outbound"
            continue
        if line.startswith("Return "):
            direction = "return"
            continue
        match = _ITINERARY_EMISSIONS_RE.match(line)
        if match and direction is not None:
            emissions.append({"direction": direction, **match.groupdict()})
    return emissions


def _cabin_facilities(lines: list[str]) -> list[str]:
    facilities: list[str] = []
    markers = (
        "legroom",
        "USB outlet",
        "Wi-Fi",
        "On-demand video",
        "Often delayed",
    )
    for line in lines:
        if any(marker in line for marker in markers) and line not in facilities:
            facilities.append(line)
    return facilities


def _booking_options(lines: list[str]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    option_lines = _section_after(
        lines,
        "Booking options",
        stop_headers={
            "Prices include required taxes + fees for 1 adult. Optional charges and bag fees may apply."
        },
    )
    index = 0
    while index < len(option_lines):
        line = option_lines[index]
        if not line.startswith("Book with "):
            index += 1
            continue

        provider = line.removeprefix("Book with ").strip()
        option: dict[str, Any] = {"provider": provider}
        index += 1
        if index < len(option_lines) and option_lines[index] == "Airline":
            option["provider_type"] = "Airline"
            index += 1
        if index < len(option_lines):
            option["price"] = _parse_price(option_lines[index])
            index += 1
        if index < len(option_lines) and option_lines[index].startswith("DKK "):
            option["secondary_price"] = _parse_secondary_price(option_lines[index])
            index += 1
        if index < len(option_lines) and option_lines[index] == "Continue":
            option["boundary_control"] = "Continue"
            index += 1
        if index < len(option_lines) and option_lines[index] == "View options":
            index += 1
        options.append(option)
    return options


def _parse_price(line: str | None) -> dict[str, Any] | None:
    if line is None:
        return None
    match = _PRICE_RE.search(line)
    if not match:
        return None
    currency = match.group("currency")
    amount_text = match.group("amount")
    return {
        "amount": int(amount_text.replace(",", "")),
        "currency": currency,
        "text": f"{currency} {amount_text}",
    }


def _parse_secondary_price(line: str) -> dict[str, str]:
    price = _parse_price(line)
    if price is None:
        return {"currency": "unknown", "text": line}
    return {"currency": price["currency"], "text": price["text"]}


def _baggage_policy_links(lines: list[str], decoded_links: Any) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    if not isinstance(decoded_links, list):
        return links

    for decoded_link in decoded_links:
        if not isinstance(decoded_link, dict):
            continue
        text = decoded_link.get("text")
        url = decoded_link.get("url")
        if isinstance(text, str) and isinstance(url, str) and text in lines:
            links.append(
                {
                    "airline": text.split(" bag policy", 1)[0],
                    "text": text,
                    "url": url,
                }
            )
    return links


def _terminal_info(text: str) -> dict[str, str]:
    if re.search(r"\bterminal\b", text, flags=re.IGNORECASE):
        return {"status": "found"}
    return {
        "status": "not_found",
        "reason": "terminal information was not visible in selected-itinerary evidence",
    }


def _boundary(booking_options: list[dict[str, Any]]) -> dict[str, Any]:
    continue_visible = any(
        option.get("boundary_control") == "Continue" for option in booking_options
    )
    return {
        "stop_state": "payment_or_booking_boundary" if continue_visible else "not_applicable",
        "provider_continue_visible": continue_visible,
        "provider_continue_clicked": False,
        "checkout_entered": False,
        "payment_entered": False,
        "personal_data_entered": False,
        "login_entered": False,
    }
