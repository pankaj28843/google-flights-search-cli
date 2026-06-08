"""Fixture-backed Google Flights URL query-state construction."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from gflights.domain import RouteChoice, RouteEndpoint, SearchIntent


class UnsupportedQueryState(ValueError):
    def __init__(self, field: str, value: Any, reason: str) -> None:
        super().__init__(reason)
        self.field = field
        self.value = value
        self.reason = reason


@dataclass(frozen=True)
class EncodedQueryState:
    params: dict[str, str]
    confidence: str
    source_surfaces: list[str]
    evidence_refs: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class QueryEndpoint:
    kind_value: int
    encoded_value: str


_CABIN_VALUES = {
    "economy": 1,
    "business": 3,
}
_PASSENGER_VALUES = {
    "adults": 1,
    "children": 2,
    "infants_on_lap": 3,
    "infants_in_seat": 4,
}
_SORT_VALUES = {
    "price": 2,
    "departure_time": 3,
    "arrival_time": 4,
    "duration": 5,
    "emissions": 6,
}
_EVIDENCE_REFS = [
    "docs/query-state-maintenance.md",
    "docs/fixture-contract.md",
    "tests/e2e/fixtures/codec_tfu_price_fixture.json",
]


def build_query_state(intent: SearchIntent) -> EncodedQueryState:
    """Build supported populated Google Flights query parameters."""

    _validate_concrete_dates(intent)
    origin = _endpoint(intent.origin, field="origin")
    destination = _endpoint(intent.destination, field="destination")
    cabin = _cabin(intent)

    params = {
        "tfs": _encode_tfs(intent, origin=origin, destination=destination, cabin=cabin),
    }
    source_surfaces = ["query-state:tfs"]
    if intent.sort != "top_flights":
        params["tfu"] = _encode_sort(intent)
        source_surfaces.append("query-state:tfu")
    params["hl"] = intent.language
    params["curr"] = intent.currency
    return EncodedQueryState(
        params=params,
        confidence="strong",
        source_surfaces=source_surfaces,
        evidence_refs=_EVIDENCE_REFS,
        warnings=[
            "encoded Google Flights URL state is a strong fixture-backed hypothesis, not a proven public API"
        ],
    )


def _validate_concrete_dates(intent: SearchIntent) -> None:
    if intent.trip_type == "multi_city":
        raise UnsupportedQueryState(
            "trip_type",
            intent.trip_type,
            "multi-city query-state encoding is deferred until populated multi-city evidence proves it",
        )
    if intent.departure_window.start != intent.departure_window.end:
        raise UnsupportedQueryState(
            "departure_window",
            intent.departure_window.model_dump(mode="json"),
            "query-state encoding accepts one concrete departure date at a time",
        )
    if intent.trip_type == "round_trip":
        if intent.return_window is None:
            raise UnsupportedQueryState(
                "return_window",
                None,
                "round-trip query-state encoding requires one concrete return date",
            )
        if intent.return_window.start != intent.return_window.end:
            raise UnsupportedQueryState(
                "return_window",
                intent.return_window.model_dump(mode="json"),
                "query-state encoding accepts one concrete return date at a time",
            )


def _endpoint(endpoint: RouteEndpoint, *, field: str) -> QueryEndpoint:
    if endpoint.selected is not None:
        return _selected_endpoint(endpoint.selected, field=field)

    if endpoint.kind == "airport_code":
        airport_code = endpoint.text.strip().upper()
        if len(airport_code) != 3 or not airport_code.isalpha():
            raise UnsupportedQueryState(
                f"{field}.text",
                endpoint.text,
                "airport-code query-state encoding requires a three-letter IATA code",
            )
        return QueryEndpoint(1, airport_code)

    raise UnsupportedQueryState(
        field,
        endpoint.model_dump(mode="json"),
        "city-or-airport query-state encoding requires a selected route choice from route resolve",
    )


def _selected_endpoint(choice: RouteChoice, *, field: str) -> QueryEndpoint:
    code_or_id = choice.code_or_id
    if choice.kind == "airport_code" and code_or_id is not None:
        return QueryEndpoint(1, code_or_id.strip().upper())
    if code_or_id and code_or_id.startswith("/"):
        return QueryEndpoint(3, code_or_id)
    raise UnsupportedQueryState(
        f"{field}.selected",
        choice.model_dump(mode="json"),
        "selected route choice needs a visible airport code or reviewed decoded city id for query-state encoding",
    )


def _cabin(intent: SearchIntent) -> int:
    cabin = _CABIN_VALUES.get(intent.cabin)
    if cabin is None:
        raise UnsupportedQueryState(
            "cabin",
            intent.cabin,
            "premium economy and first query-state encoding are deferred until populated evidence proves them",
        )
    return cabin


def _encode_tfs(
    intent: SearchIntent,
    *,
    origin: QueryEndpoint,
    destination: QueryEndpoint,
    cabin: int,
) -> str:
    legs = [
        _message(
            3,
            _leg(
                date_value=intent.departure_window.start,
                origin=origin,
                destination=destination,
            ),
        )
    ]
    trip_value = 2
    if intent.trip_type == "round_trip":
        trip_value = 1
        assert intent.return_window is not None
        legs.append(
            _message(
                3,
                _leg(
                    date_value=intent.return_window.start,
                    origin=destination,
                    destination=origin,
                ),
            )
        )

    payload = b"".join(
        [
            _varint_field(1, 28),
            _varint_field(2, 2),
            *legs,
            *_passenger_fields(intent),
            _varint_field(9, cabin),
            _varint_field(14, 1),
            _message(16, _varint_field(1, 18446744073709551615)),
            _varint_field(19, trip_value),
        ]
    )
    return _base64url_no_padding(payload)


def _leg(
    *,
    date_value: str,
    origin: QueryEndpoint,
    destination: QueryEndpoint,
) -> bytes:
    return b"".join(
        [
            _string_field(2, date_value),
            _message(13, _endpoint_message(origin)),
            _message(14, _endpoint_message(destination)),
        ]
    )


def _endpoint_message(endpoint: QueryEndpoint) -> bytes:
    return b"".join(
        [
            _varint_field(1, endpoint.kind_value),
            _string_field(2, endpoint.encoded_value),
        ]
    )


def _passenger_fields(intent: SearchIntent) -> list[bytes]:
    passengers = intent.passengers
    counts = {
        "adults": passengers.adults,
        "children": passengers.children,
        "infants_on_lap": passengers.infants_on_lap,
        "infants_in_seat": passengers.infants_in_seat,
    }
    if not any(counts.values()):
        raise UnsupportedQueryState(
            "passengers",
            passengers.model_dump(mode="json"),
            "query-state encoding requires at least one passenger",
        )
    fields: list[bytes] = []
    for passenger_kind, count in counts.items():
        fields.extend(_varint_field(8, _PASSENGER_VALUES[passenger_kind]) for _ in range(count))
    return fields


def _encode_sort(intent: SearchIntent) -> str:
    sort_value = _SORT_VALUES.get(intent.sort)
    if sort_value is None:
        raise UnsupportedQueryState(
            "sort",
            intent.sort,
            "top-flights query-state encoding uses absent tfu because observed top-flight tfu variants are not reload-equivalent yet",
        )
    return _base64url_no_padding(
        _message(
            2,
            b"".join(
                [
                    _varint_field(1, sort_value),
                    _varint_field(2, 0),
                    _varint_field(3, 0),
                ]
            ),
        )
    )


def _varint_field(field_number: int, value: int) -> bytes:
    return _varint((field_number << 3) | 0) + _varint(value)


def _string_field(field_number: int, value: str) -> bytes:
    data = value.encode("utf-8")
    return _varint((field_number << 3) | 2) + _varint(len(data)) + data


def _message(field_number: int, value: bytes) -> bytes:
    return _varint((field_number << 3) | 2) + _varint(len(value)) + value


def _varint(value: int) -> bytes:
    chunks: list[int] = []
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            chunks.append(byte | 0x80)
            continue
        chunks.append(byte)
        return bytes(chunks)


def _base64url_no_padding(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
