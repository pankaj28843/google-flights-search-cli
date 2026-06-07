"""Pure domain models for agent-facing flight search contracts."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Confidence = Literal["proven", "strong", "weak", "unknown", "rejected"]
SearchStatus = Literal[
    "ok",
    "unsupported",
    "deferred",
    "ambiguous",
    "blocked",
    "no_results",
    "experimental",
    "stale_fixture",
    "tool_error",
]
TripType = Literal["round_trip", "one_way", "multi_city"]
Cabin = Literal["economy", "premium_economy", "business", "first"]
Sort = Literal[
    "top_flights",
    "price",
    "departure_time",
    "arrival_time",
    "duration",
    "emissions",
]


class RouteEndpoint(BaseModel):
    text: str
    kind: Literal["airport_code", "city_or_airport"]


class DateWindow(BaseModel):
    start: str
    end: str

    @model_validator(mode="after")
    def validate_order(self) -> DateWindow:
        if date.fromisoformat(self.start) > date.fromisoformat(self.end):
            raise ValueError("date window start must be on or before end")
        return self


class PassengerParty(BaseModel):
    adults: int = Field(default=1, ge=0)
    children: int = Field(default=0, ge=0)
    infants_in_seat: int = Field(default=0, ge=0)
    infants_on_lap: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def require_one_traveler(self) -> PassengerParty:
        if self.adults + self.children + self.infants_in_seat + self.infants_on_lap <= 0:
            raise ValueError("at least one traveler is required")
        return self


class SearchIntent(BaseModel):
    query_id: str
    origin: RouteEndpoint
    destination: RouteEndpoint
    trip_type: TripType
    departure_window: DateWindow
    return_window: DateWindow | None = None
    passengers: PassengerParty = Field(default_factory=PassengerParty)
    traveler_profiles: list[dict[str, Any]] = Field(default_factory=list)
    cabin: Cabin = "economy"
    airline_preferences: list[dict[str, Any]] = Field(default_factory=list)
    consider_all_airlines: bool = True
    currency: str = "EUR"
    language: str = "en"
    location: str | None = None
    sort: Sort = "top_flights"
    google_filters: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_trip_dates(self) -> SearchIntent:
        if self.trip_type == "round_trip" and self.return_window is None:
            raise ValueError("round_trip requires return_window")
        if self.trip_type == "one_way" and self.return_window is not None:
            raise ValueError("one_way must not include return_window")
        return self
