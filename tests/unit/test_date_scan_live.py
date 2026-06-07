from __future__ import annotations

from pathlib import Path
from typing import Any

from gflights.date_scan_live import LiveDatePairProbe
from gflights.domain import SearchIntent


def test_live_date_pair_probe_calls_live_search_and_writes_input_artifact(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_live_search_runner(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        calls.append(kwargs)
        return (
            0,
            {
                "query_id": "probe-intent",
                "status": "ok",
                "results": [
                    {
                        "price": {"amount": 701, "currency": "EUR", "text": "EUR 701"},
                        "carriers": ["Air India"],
                    }
                ],
                "unsupported": [],
                "warnings": [],
                "evidence": {
                    "run_id": "gf-probe",
                    "source_surfaces": ["fake-live-search"],
                    "artifacts": ["probe.json"],
                },
            },
        )

    probe = LiveDatePairProbe(
        project_root=tmp_path / "state",
        browser_mode="headless",
        max_probes=1,
        timeout_seconds=7.0,
        live_search_runner=fake_live_search_runner,
    )

    payload = probe(_probe_intent())
    skipped = probe(_probe_intent())

    assert payload["status"] == "ok"
    assert payload["results"][0]["price"]["amount"] == 701
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "max_live_probes_reached"
    assert len(calls) == 1
    assert calls[0]["project_root"] == tmp_path / "state"
    assert calls[0]["browser_mode"] == "headless"
    assert calls[0]["timeout_seconds"] == 7.0
    assert Path(calls[0]["input_json"]).is_file()
    assert Path(calls[0]["input_json"]).parent.name == "date-scan-live-probes"
    assert Path(calls[0]["input_json"]).name == "probe-intent-2026-11-21-2027-01-02.json"


def _probe_intent() -> SearchIntent:
    return SearchIntent.model_validate(
        {
            "query_id": "probe-intent",
            "origin": {"text": "CPH", "kind": "airport_code"},
            "destination": {"text": "LKO", "kind": "airport_code"},
            "trip_type": "round_trip",
            "departure_window": {"start": "2026-11-21", "end": "2026-11-21"},
            "return_window": {"start": "2027-01-02", "end": "2027-01-02"},
            "passengers": {"adults": 2, "infants_on_lap": 1},
            "cabin": "economy",
            "currency": "EUR",
            "language": "en",
            "sort": "top_flights",
        }
    )
