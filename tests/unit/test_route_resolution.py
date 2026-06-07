from __future__ import annotations

import json
from pathlib import Path

from gflights.route_resolution import extract_route_choices_from_snapshot

FIXTURES = Path(__file__).resolve().parents[1] / "e2e" / "fixtures"


def route_visible_text_fixture() -> dict[str, object]:
    return json.loads((FIXTURES / "route_autocomplete_visible_text_fixture.json").read_text())


def query_fixture(input_text: str) -> dict[str, object]:
    fixture = route_visible_text_fixture()
    queries = fixture["queries"]
    assert isinstance(queries, list)
    for query in queries:
        assert isinstance(query, dict)
        if query["input_text"] == input_text:
            return query
    raise AssertionError(f"missing query fixture for {input_text!r}")


def test_extracts_washington_multi_airport_autocomplete_choices() -> None:
    query = query_fixture("Washington DC")

    exit_code, payload = extract_route_choices_from_snapshot(
        input_text="Washington DC",
        field="destination",
        snapshot=query["snapshot"],
        evidence_artifact="route_autocomplete_visible_text_fixture.json",
        source_surface="route-autocomplete-visible-text",
    )

    assert exit_code == 2
    assert payload["status"] == "ambiguous"
    assert payload["selected"] is None
    assert payload["ambiguity_reason"] == "multiple_route_choices"
    assert [choice["display_name"] for choice in payload["choices"]] == [
        "Washington, USA",
        "Ronald Reagan Washington National Airport",
        "Dulles International Airport",
        "Baltimore/Washington International Thurgood Marshall Airport",
    ]
    assert [choice["code_or_id"] for choice in payload["choices"]] == [
        None,
        "DCA",
        "IAD",
        "BWI",
    ]
    assert payload["choices"][0]["kind"] == "city"
    assert payload["choices"][1]["kind"] == "airport_code"


def test_extracts_lucknow_location_disambiguation_without_silent_selection() -> None:
    query = query_fixture("Lucknow")

    exit_code, payload = extract_route_choices_from_snapshot(
        input_text="Lucknow",
        field="destination",
        snapshot=query["snapshot"],
        evidence_artifact="route_autocomplete_visible_text_fixture.json",
        source_surface="route-autocomplete-visible-text",
    )

    assert exit_code == 2
    assert payload["status"] == "ambiguous"
    assert [choice["display_name"] for choice in payload["choices"]] == [
        "Lucknow, Uttar Pradesh, India",
        "Chaudhary Charan Singh International Airport",
        "Lucknow, Ontario, Canada",
        "Lucknow, Australia",
        "Lucknow, Australia",
    ]
    assert [choice["kind"] for choice in payload["choices"]] == [
        "city",
        "airport_code",
        "city",
        "city",
        "city",
    ]
    assert payload["choices"][1]["code_or_id"] == "LKO"


def test_extracts_visible_airport_codes_but_keeps_city_text_ambiguous() -> None:
    query = query_fixture("Copenhagen")

    exit_code, payload = extract_route_choices_from_snapshot(
        input_text="Copenhagen",
        field="origin",
        snapshot=query["snapshot"],
        evidence_artifact="route_autocomplete_visible_text_fixture.json",
        source_surface="route-autocomplete-visible-text",
    )

    assert exit_code == 2
    assert payload["status"] == "ambiguous"
    assert [choice["display_name"] for choice in payload["choices"]] == [
        "Copenhagen, Denmark",
        "Copenhagen Airport",
        "Copenhagen Central Station",
        "Copenhagen, New York, USA",
    ]
    assert [choice["code_or_id"] for choice in payload["choices"]] == [
        None,
        "CPH",
        None,
        None,
    ]


def test_snapshot_without_route_autocomplete_choices_is_explicitly_unsupported() -> None:
    exit_code, payload = extract_route_choices_from_snapshot(
        input_text="Atlantis",
        field="destination",
        snapshot={"items": [{"text": "Flights Round trip 1 Economy Search"}]},
        evidence_artifact="route_autocomplete_visible_text_fixture.json",
        source_surface="route-autocomplete-visible-text",
    )

    assert exit_code == 3
    assert payload["status"] == "unsupported"
    assert payload["choices"] == []
    assert payload["unsupported"][0]["field"] == "route.resolve.live_autocomplete_extraction"
