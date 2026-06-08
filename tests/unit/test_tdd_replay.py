from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gflights.codec import decode_query_value
from gflights.itinerary_extraction import extract_selected_itinerary
from gflights.result_extraction import extract_primary_results
from gflights.route_resolution import extract_route_choices_from_snapshot

FIXTURES = Path(__file__).resolve().parents[1] / "e2e" / "fixtures"


def test_tdd_primary_result_snapshot_extracts_visible_rows() -> None:
    record = _load_fixture("primary_results_visible_text_fixture.json")
    expected = record["expected"]["first_result"]

    results = extract_primary_results(
        record["snapshot"],
        source_surface=record["source_surface"],
        evidence_artifact=record["source_artifact"],
        confidence=record["confidence"],
    )

    assert len(results) == record["expected"]["result_count"]
    assert results[0]["carriers"] == expected["carriers"]
    assert results[0]["duration_minutes"] == expected["duration_minutes"]
    assert results[0]["stops"] == expected["stops"]
    assert results[0]["price"] == expected["price"]
    assert results[0]["source_surface"] == "primary-results-visible-text"


def test_tdd_selected_itinerary_snapshot_extracts_booking_boundary_fields() -> None:
    record = _load_fixture("selected_itinerary_visible_text_fixture.json")

    itinerary = extract_selected_itinerary(
        record,
        source_surface=record["source_surface"],
        confidence=record["confidence"],
    )

    assert itinerary["summary"]["total_price"] == {
        "amount": 1708,
        "currency": "EUR",
        "text": "EUR 1,708",
    }
    assert itinerary["segments"][0]["flight_number"] == "KL 1268"
    assert itinerary["segments"][2]["flight_number"] == "6E 6026"
    assert itinerary["segments"][5]["destination_airport"] == "CPH"
    assert itinerary["layovers"][1]["airport"] == "DEL"
    assert itinerary["booking_options"][0]["provider"] == "KLM"
    assert itinerary["booking_options"][0]["boundary_control"] == "Continue"
    assert itinerary["terminal_info"]["status"] == "not_found"
    assert itinerary["boundary"]["stop_state"] == "payment_or_booking_boundary"
    assert itinerary["boundary"]["provider_continue_clicked"] is False
    assert itinerary["boundary"]["payment_entered"] is False


def test_tdd_route_visible_text_snapshot_extracts_ambiguous_choices() -> None:
    record = _load_fixture("route_autocomplete_visible_text_fixture.json")
    query = record["queries"][0]

    exit_code, payload = extract_route_choices_from_snapshot(
        input_text=query["input_text"],
        field=query["field"],
        snapshot=query["snapshot"],
        evidence_artifact=str(FIXTURES / "route_autocomplete_visible_text_fixture.json"),
        source_surface=record["source_surface"],
        confidence=record["confidence"],
    )

    assert exit_code == query["expected"]["exit_code"]
    assert payload["status"] == query["expected"]["status"]
    assert [choice["display_name"] for choice in payload["choices"]] == query["expected"][
        "candidate_labels"
    ]
    assert [choice["code_or_id"] for choice in payload["choices"]] == query["expected"][
        "candidate_code_or_ids"
    ]


def test_tdd_codec_snapshot_preserves_confidence_boundary() -> None:
    record = _load_fixture("codec_tfu_price_fixture.json")
    expected = record["expected"]

    decoded = decode_query_value(record["key"], record["raw_value"])

    assert expected["confidence"] == "strong"
    assert expected["confidence"] != "proven"
    assert {"path": "tfu.2.1", "wire_type": "varint", "value": 2} in decoded.wire_paths
    assert _stale_warnings(expected["wire_paths"], decoded.wire_paths) == []
    assert decoded.round_trip_value == record["raw_value"]


def test_tdd_codec_snapshot_detects_stale_expected_wire_path() -> None:
    record = _load_fixture("codec_tfu_price_fixture.json")
    decoded = decode_query_value(record["key"], record["raw_value"])
    changed_expected = [{"path": "tfu.99", "value": 2, "hypothesis": "price sort"}]

    warnings = _stale_warnings(changed_expected, decoded.wire_paths)

    assert warnings == ["expected wire path tfu.99=2 was not observed"]


def test_tdd_blocked_stop_state_snapshot_keeps_headed_fallback_policy() -> None:
    record = _load_fixture("blocked_headless_fixture.json")

    assert record["expected"]["exit_code"] == 4
    assert record["expected"]["status"] == "blocked"
    assert record["expected"]["browser_mode"] == "headless"
    assert record["expected"]["fallback"]["recommended_browser_mode"] == "headed"


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def _stale_warnings(
    expected_paths: list[dict[str, Any]],
    observed_paths: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    for expected_path in expected_paths:
        path = expected_path.get("path")
        value = expected_path.get("value")
        matched = any(
            observed_path.get("path") == path and observed_path.get("value") == value
            for observed_path in observed_paths
        )
        if not matched:
            warnings.append(f"expected wire path {path}={value!r} was not observed")
    return warnings
