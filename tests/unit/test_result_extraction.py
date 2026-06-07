from __future__ import annotations

import json
from pathlib import Path

from gflights.result_extraction import extract_primary_results

FIXTURES = Path(__file__).resolve().parents[1] / "e2e" / "fixtures"


def test_extract_primary_results_from_visible_text_fixture() -> None:
    fixture = json.loads((FIXTURES / "primary_results_visible_text_fixture.json").read_text())

    results = extract_primary_results(
        fixture["snapshot"],
        source_surface=fixture["source_surface"],
        evidence_artifact=fixture["source_artifact"],
        confidence=fixture["confidence"],
    )

    assert len(results) == fixture["expected"]["result_count"]
    first = results[0]
    assert first["result_id"] == "visible-text-result-1"
    assert first["source_surface"] == fixture["expected"]["first_result"]["source_surface"]
    assert first["confidence"] == fixture["expected"]["first_result"]["confidence"]
    assert first["origin_airports"] == fixture["expected"]["first_result"]["origin_airports"]
    assert (
        first["destination_airports"] == fixture["expected"]["first_result"]["destination_airports"]
    )
    assert first["departure_times"] == fixture["expected"]["first_result"]["departure_times"]
    assert first["arrival_times"] == fixture["expected"]["first_result"]["arrival_times"]
    assert first["carriers"] == fixture["expected"]["first_result"]["carriers"]
    assert first["duration_text"] == fixture["expected"]["first_result"]["duration_text"]
    assert first["duration_minutes"] == fixture["expected"]["first_result"]["duration_minutes"]
    assert first["stops"] == fixture["expected"]["first_result"]["stops"]
    assert first["layovers"] == fixture["expected"]["first_result"]["layovers"]
    assert first["price"] == fixture["expected"]["first_result"]["price"]
    assert first["emissions"] == fixture["expected"]["first_result"]["emissions"]
    assert first["warnings"] == []
    assert first["evidence"]["artifacts"] == [fixture["source_artifact"]]


def test_extract_primary_results_returns_empty_without_primary_rows() -> None:
    results = extract_primary_results(
        {"text": {"text": "Search results 0 results returned. Track prices Date grid Price graph"}},
        source_surface="primary-results-visible-text",
        evidence_artifact="snapshot.json",
    )

    assert results == []
