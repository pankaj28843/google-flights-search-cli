"""Pure planning for Google Flights form interactions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gflights.domain import SearchIntent


class UnsupportedLiveForm(ValueError):
    def __init__(self, field: str, value: Any, reason: str) -> None:
        super().__init__(reason)
        self.field = field
        self.value = value
        self.reason = reason


@dataclass(frozen=True)
class LiveFormStep:
    name: str
    args: list[str]
    source_surface: str
    artifact_name: str


@dataclass(frozen=True)
class LiveFormPlan:
    steps: list[LiveFormStep]


def plan_live_form_interaction(intent: SearchIntent, *, page_id: str) -> LiveFormPlan:
    """Build cdp command steps for observed Google Flights form controls."""

    validate_live_form_support(intent)
    steps: list[LiveFormStep] = []

    if intent.trip_type == "one_way":
        steps.append(_click_text("trip-menu-open", "Round trip", page_id=page_id))
        steps.append(_click_text("trip-one-way", "One way", page_id=page_id, wait_text="One way"))

    steps.extend(
        [
            _fill_label("origin-fill", "Where from?", intent.origin.text, page_id=page_id),
            _press_label(
                "origin-select", "Where from?", "Enter", intent.origin.text, page_id=page_id
            ),
            _fill_label("destination-fill", "Where to?", intent.destination.text, page_id=page_id),
            _press_label(
                "destination-select", "Where to?", "Enter", intent.destination.text, page_id=page_id
            ),
            _fill_label(
                "departure-fill",
                "Departure",
                intent.departure_window.start,
                page_id=page_id,
            ),
            _press_label(
                "departure-select",
                "Departure",
                "Enter",
                intent.departure_window.start,
                page_id=page_id,
            ),
        ]
    )

    if intent.trip_type == "round_trip" and intent.return_window is not None:
        steps.extend(
            [
                _fill_label("return-fill", "Return", intent.return_window.start, page_id=page_id),
                _press_label(
                    "return-select",
                    "Return",
                    "Enter",
                    intent.return_window.start,
                    page_id=page_id,
                ),
            ]
        )

    steps.extend(_passenger_steps(intent, page_id=page_id))
    steps.extend(_cabin_steps(intent, page_id=page_id))
    steps.append(
        _click_text(
            "search-submit",
            "Search",
            page_id=page_id,
            wait_url_contains="/travel/flights/search",
        )
    )
    return LiveFormPlan(steps=steps)


def validate_live_form_support(intent: SearchIntent) -> None:
    if intent.trip_type == "multi_city":
        raise UnsupportedLiveForm(
            "trip_type",
            intent.trip_type,
            "multi-city live form interaction is deferred until focused evidence proves it",
        )
    if intent.cabin in {"premium_economy", "first"}:
        raise UnsupportedLiveForm(
            "cabin",
            intent.cabin,
            "premium economy and first live cabin interaction are deferred until focused evidence proves them",
        )
    if intent.origin.kind != "airport_code":
        raise UnsupportedLiveForm(
            "origin.kind",
            intent.origin.kind,
            "live route interaction is limited to airport-code inputs until autocomplete disambiguation is implemented",
        )
    if intent.destination.kind != "airport_code":
        raise UnsupportedLiveForm(
            "destination.kind",
            intent.destination.kind,
            "live route interaction is limited to airport-code inputs until autocomplete disambiguation is implemented",
        )
    if intent.departure_window.start != intent.departure_window.end:
        raise UnsupportedLiveForm(
            "departure_window",
            intent.departure_window.model_dump(mode="json"),
            "live form interaction accepts one concrete departure date at a time",
        )
    if (
        intent.trip_type == "round_trip"
        and intent.return_window is not None
        and intent.return_window.start != intent.return_window.end
    ):
        raise UnsupportedLiveForm(
            "return_window",
            intent.return_window.model_dump(mode="json"),
            "live form interaction accepts one concrete return date at a time",
        )
    if intent.passengers.adults < 1:
        raise UnsupportedLiveForm(
            "passengers.adults",
            intent.passengers.adults,
            "Google Flights form interaction starts from one adult and cannot remove the final adult",
        )


def _passenger_steps(intent: SearchIntent, *, page_id: str) -> list[LiveFormStep]:
    steps: list[LiveFormStep] = []
    passengers = intent.passengers
    add_counts = {
        "adult": max(passengers.adults - 1, 0),
        "child": passengers.children,
        "infant-seat": passengers.infants_in_seat,
        "infant-lap": passengers.infants_on_lap,
    }
    if not any(add_counts.values()):
        return steps

    steps.append(
        _click_role_button(
            "passenger-menu-open",
            "1 passenger",
            page_id=page_id,
            wait_text="Add adult",
        )
    )
    for index in range(add_counts["adult"]):
        steps.append(
            _click_text(
                f"passenger-add-adult-{index + 2}",
                "Add adult",
                page_id=page_id,
            )
        )
    for index in range(add_counts["child"]):
        steps.append(
            _click_text(
                f"passenger-add-child-{index + 1}",
                "Add child aged 2 to 11",
                page_id=page_id,
            )
        )
    for index in range(add_counts["infant-seat"]):
        steps.append(
            _click_text(
                f"passenger-add-infant-seat-{index + 1}",
                "Add infant in seat",
                page_id=page_id,
            )
        )
    for index in range(add_counts["infant-lap"]):
        steps.append(
            _click_text(
                f"passenger-add-infant-lap-{index + 1}",
                "Add infant on lap",
                page_id=page_id,
            )
        )
    steps.append(_click_text("passenger-done", "Done", page_id=page_id))
    return steps


def _cabin_steps(intent: SearchIntent, *, page_id: str) -> list[LiveFormStep]:
    if intent.cabin == "economy":
        return []
    return [
        _click_text("cabin-menu-open", "Economy", page_id=page_id),
        _click_text("cabin-business", "Business", page_id=page_id, wait_text="Business"),
    ]


def _fill_label(name: str, label: str, value: str, *, page_id: str) -> LiveFormStep:
    return LiveFormStep(
        name=name,
        args=[
            "fill",
            label,
            value,
            "--by",
            "label",
            "--exact",
            *_target_args(page_id),
            "--wait-text",
            value,
        ],
        source_surface=f"cdp:form:{name}",
        artifact_name=f"form-{name}.json",
    )


def _press_label(
    name: str,
    label: str,
    key: str,
    wait_text: str,
    *,
    page_id: str,
) -> LiveFormStep:
    return LiveFormStep(
        name=name,
        args=[
            "press",
            key,
            label,
            "--by",
            "label",
            "--exact",
            *_target_args(page_id),
            "--wait-text",
            wait_text,
        ],
        source_surface=f"cdp:form:{name}",
        artifact_name=f"form-{name}.json",
    )


def _click_text(
    name: str,
    text: str,
    *,
    page_id: str,
    wait_text: str | None = None,
    wait_url_contains: str | None = None,
) -> LiveFormStep:
    args = ["click", text, "--by", "text", "--exact", *_target_args(page_id)]
    if wait_text is not None:
        args.extend(["--wait-text", wait_text])
    if wait_url_contains is not None:
        args.extend(["--wait-url-contains", wait_url_contains])
    return LiveFormStep(
        name=name,
        args=args,
        source_surface=f"cdp:form:{name}",
        artifact_name=f"form-{name}.json",
    )


def _click_role_button(
    name: str,
    label: str,
    *,
    page_id: str,
    wait_text: str | None = None,
) -> LiveFormStep:
    args = ["click", label, "--by", "role", "--role", "button", *_target_args(page_id)]
    if wait_text is not None:
        args.extend(["--wait-text", wait_text])
    return LiveFormStep(
        name=name,
        args=args,
        source_surface=f"cdp:form:{name}",
        artifact_name=f"form-{name}.json",
    )


def _target_args(page_id: str) -> list[str]:
    if not page_id:
        return []
    return ["--target", page_id]
