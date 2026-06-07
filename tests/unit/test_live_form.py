from __future__ import annotations

import pytest

from gflights.domain import DateWindow, PassengerParty, RouteEndpoint, SearchIntent
from gflights.live_form import UnsupportedLiveForm, plan_live_form_interaction


def intent(
    *,
    trip_type: str = "round_trip",
    cabin: str = "business",
    passengers: PassengerParty | None = None,
) -> SearchIntent:
    return SearchIntent.model_validate(
        {
            "query_id": "form-cph-del",
            "origin": RouteEndpoint(text="CPH", kind="airport_code").model_dump(),
            "destination": RouteEndpoint(text="DEL", kind="airport_code").model_dump(),
            "trip_type": trip_type,
            "departure_window": DateWindow(start="2026-10-01", end="2026-10-01").model_dump(),
            "return_window": None
            if trip_type == "one_way"
            else DateWindow(start="2026-11-24", end="2026-11-24").model_dump(),
            "passengers": (
                passengers
                or PassengerParty(
                    adults=2,
                    children=1,
                    infants_in_seat=1,
                    infants_on_lap=1,
                )
            ).model_dump(),
            "cabin": cabin,
            "currency": "EUR",
            "language": "en",
            "sort": "top_flights",
        }
    )


def test_plan_live_form_interaction_uses_semantic_cdp_locators() -> None:
    plan = plan_live_form_interaction(intent(), page_id="page-1")

    assert [step.name for step in plan.steps][:4] == [
        "origin-fill",
        "origin-select",
        "destination-fill",
        "destination-select",
    ]
    assert plan.steps[0].args == [
        "fill",
        "Where from?",
        "CPH",
        "--by",
        "label",
        "--exact",
        "--target",
        "page-1",
        "--wait-text",
        "CPH",
    ]
    assert plan.steps[2].args == [
        "fill",
        "Where to?",
        "DEL",
        "--by",
        "label",
        "--exact",
        "--target",
        "page-1",
        "--wait-text",
        "DEL",
    ]
    assert any(step.args[:2] == ["fill", "Departure"] for step in plan.steps)
    assert any(step.args[:2] == ["fill", "Return"] for step in plan.steps)
    assert any(step.args[0:2] == ["click", "Add adult"] for step in plan.steps)
    assert any(step.args[0:2] == ["click", "Add child aged 2 to 11"] for step in plan.steps)
    assert any(step.args[0:2] == ["click", "Add infant in seat"] for step in plan.steps)
    assert any(step.args[0:2] == ["click", "Add infant on lap"] for step in plan.steps)
    assert any(step.args[0:2] == ["click", "Business"] for step in plan.steps)
    assert plan.steps[-1].args[0:2] == ["click", "Search"]


def test_plan_live_form_interaction_avoids_selector_and_booking_actions() -> None:
    plan = plan_live_form_interaction(intent(), page_id="page-1")
    flat_args = [arg for step in plan.steps for arg in step.args]

    assert not any("[" in arg or "querySelector" in arg for arg in flat_args)
    assert not any(arg in {"Book", "Continue", "Passenger name", "Payment"} for arg in flat_args)
    assert all(step.source_surface.startswith("cdp:form:") for step in plan.steps)


def test_plan_live_form_interaction_supports_one_way_without_return_date() -> None:
    plan = plan_live_form_interaction(
        intent(
            trip_type="one_way",
            cabin="economy",
            passengers=PassengerParty(adults=1, children=0, infants_in_seat=0, infants_on_lap=0),
        ),
        page_id="page-1",
    )

    assert any(step.args[0:2] == ["click", "One way"] for step in plan.steps)
    assert not any(step.args[:2] == ["fill", "Return"] for step in plan.steps)
    assert not any(step.args[0:2] == ["click", "Business"] for step in plan.steps)


def test_plan_live_form_interaction_defers_unproven_cabin_classes() -> None:
    with pytest.raises(UnsupportedLiveForm) as error:
        plan_live_form_interaction(intent(cabin="premium_economy"), page_id="page-1")

    assert error.value.field == "cabin"
    assert error.value.value == "premium_economy"
    assert "deferred" in error.value.reason
