from __future__ import annotations

import json
from pathlib import Path

from gflights.result_extraction import classify_primary_result_absence, extract_primary_results

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


def test_extract_primary_results_from_current_compact_visible_rows() -> None:
    results = extract_primary_results(
        {
            "snapshot": {
                "items": [
                    {
                        "text": (
                            "Search results 5 results returned. Best Cheapest from €3,561 "
                            "Top departing flights Sorted by top flights "
                            "2:50 PM CPH 2:35 PM+1 LKO €3,561 round trip "
                            "2 stops19 hr 15 minBritish Airways, IndiGo +10% emissions "
                            "9:55 AM CPH 6:35 AM+1 LKO Economy + Premium Economy "
                            "€3,794 round trip 2 stops16 hr 10 minKLM, IndiGo +37% emissions "
                            "Other departing flights "
                            "6:45 AM CPH 6:35 AM+1 LKO €4,468 round trip "
                            "2 stops19 hr 20 minAir France, IndiGo -7% emissions "
                            "2:15 PM CPH 1:20 PM+1 LKO Price unavailable "
                            "2 stops18 hr 35 minLufthansa, Air India Avg emissions"
                        )
                    }
                ]
            }
        },
        source_surface="primary-results-visible-text",
        evidence_artifact="snapshot-results-retry-1.json",
    )

    assert len(results) == 3
    assert results[0]["origin_airports"] == ["CPH"]
    assert results[0]["destination_airports"] == ["LKO"]
    assert results[0]["departure_times"] == ["2:50 PM"]
    assert results[0]["arrival_times"] == ["2:35 PM+1"]
    assert results[0]["price"] == {"amount": 3561, "currency": "EUR", "text": "€3,561"}
    assert results[0]["stops"] == {"count": 2, "text": "2 stops"}
    assert results[0]["duration_minutes"] == 19 * 60 + 15
    assert results[0]["carriers"] == ["British Airways", "IndiGo"]
    assert results[0]["emissions"] == {"text": "+10% emissions"}
    assert results[0]["evidence"]["artifacts"] == ["snapshot-results-retry-1.json"]
    assert results[1]["carriers"] == ["KLM", "IndiGo"]
    assert results[1]["duration_minutes"] == 16 * 60 + 10
    assert results[2]["price"]["amount"] == 4468


def test_extract_primary_results_from_dkk_compact_visible_rows() -> None:
    results = extract_primary_results(
        {
            "snapshot": {
                "items": [
                    {
                        "text": (
                            "Search results 9 results returned. Best Cheapest from DKK 12,488 "
                            "Top departing flights Sorted by top flights "
                            "2:05 PM CPH 5:20 AM+1 DEL DKK 12,488 round trip "
                            "1 stop10 hr 45 minTurkish Airlines Avg emissions "
                            "8:25 PM CPH 9:40 AM+1 DEL DKK 16,582 round trip "
                            "Nonstop8 hr 45 minAir India -17% emissions"
                        )
                    }
                ]
            }
        },
        source_surface="primary-results-visible-text",
        evidence_artifact="snapshot-results-retry-1.json",
    )

    assert len(results) == 2
    assert results[0]["price"] == {"amount": 12488, "currency": "DKK", "text": "DKK 12,488"}
    assert results[0]["stops"] == {"count": 1, "text": "1 stop"}
    assert results[0]["duration_minutes"] == 10 * 60 + 45
    assert results[0]["carriers"] == ["Turkish Airlines"]
    assert results[1]["price"] == {"amount": 16582, "currency": "DKK", "text": "DKK 16,582"}
    assert results[1]["stops"] == {"count": 0, "text": "Nonstop"}
    assert results[1]["carriers"] == ["Air India"]


def test_classify_primary_result_absence_states() -> None:
    assert classify_primary_result_absence({"snapshot": {"items": []}}) == "empty_snapshot"
    assert (
        classify_primary_result_absence(
            {"snapshot": {"items": [{"text": "Search results Loading results Filters"}]}}
        )
        == "loading_results"
    )
    assert (
        classify_primary_result_absence(
            {"text": {"text": "No flights found. Try changing your dates."}}
        )
        == "no_results"
    )
    assert (
        classify_primary_result_absence({"text": {"text": "Search results Filters"}}) == "unknown"
    )
