from __future__ import annotations

import json
from pathlib import Path

from gflights.app_state import init_app_state
from gflights.india_trip import (
    build_concrete_intents,
    build_window_intent,
    render_markdown_report,
    run_india_trip_workflow,
    summarize_india_trip_report,
    write_india_trip_inputs,
)


def test_india_trip_inputs_expand_to_100_pairs_and_expected_passengers(
    tmp_path: Path,
) -> None:
    artifacts = write_india_trip_inputs(tmp_path)

    window_intent = json.loads(Path(artifacts["window_intent"]).read_text())
    concrete_intents = json.loads(Path(artifacts["concrete_intents"]).read_text())

    assert artifacts["concrete_pair_count"] == 100
    assert len(concrete_intents) == 100
    assert window_intent["departure_window"] == {
        "start": "2026-11-21",
        "end": "2026-11-30",
    }
    assert window_intent["return_window"] == {
        "start": "2027-01-01",
        "end": "2027-01-10",
    }
    assert window_intent["passengers"] == {
        "adults": 2,
        "children": 0,
        "infants_in_seat": 0,
        "infants_on_lap": 1,
    }
    assert window_intent["airline_preferences"] == [{"airline": "Air India", "mode": "preferred"}]
    assert concrete_intents[0]["departure_window"]["start"] == "2026-11-21"
    assert concrete_intents[0]["return_window"]["start"] == "2027-01-01"
    assert concrete_intents[-1]["departure_window"]["start"] == "2026-11-30"
    assert concrete_intents[-1]["return_window"]["start"] == "2027-01-10"


def test_india_trip_input_builder_is_deterministic() -> None:
    window_intent = build_window_intent()

    assert build_concrete_intents(window_intent) == build_concrete_intents(window_intent)


def test_india_trip_reducer_marks_zero_row_experimental_success_not_useful(
    tmp_path: Path,
) -> None:
    report_root = _write_zero_row_report(tmp_path)

    summary = summarize_india_trip_report(
        report_root,
        project_root=report_root / "state",
        live_attempted=True,
    )

    assert summary["verdict"] == "not_useful"
    assert summary["status"] == "unsupported"
    assert summary["counts"]["concrete_search_intents"] == 100
    assert summary["counts"]["parsed_result_rows"] == 0
    assert summary["counts"]["ranked_pairs"] == 0
    assert summary["counts"]["air_india_result_rows"] == 0
    assert summary["browser_budget"]["preflight"]["tab_count"] == 5
    assert summary["browser_budget"]["preflight"]["tabs_over_budget"] is False
    assert "no parsed live result rows" in summary["reason"]
    assert summary["airline_preference"] == {
        "preferred_airlines": ["Air India"],
        "google_flights_filters_applied": False,
        "local_ranking_preference_applied": True,
        "result_rows_matching_preference": 0,
        "result_rows_with_visible_carriers": 0,
        "ranked_pairs_matching_preference": 0,
        "ranked_pairs_with_visible_carriers": 0,
        "ranked_pairs_reporting_filter_policy": 0,
        "preference_observation_status": "not_observable",
        "message": (
            "No visible carrier data was available to evaluate Air India. "
            "Google Flights airline filters were not applied; ranking preference was local only."
        ),
    }


def test_india_trip_reducer_requires_ranked_price_and_evidence_for_useful(
    tmp_path: Path,
) -> None:
    report_root = _write_zero_row_report(tmp_path)
    date_scan = [
        {
            "status": "ok",
            "generated_pairs": 100,
            "probed_pairs": 100,
            "coverage_counts": {"fresh_cache": 0, "probed": 100, "unsupported": 0, "skipped": 0},
            "ranked_pairs": [
                {
                    "departure_date": "2026-11-22",
                    "return_date": "2027-01-04",
                    "best_observed_price": {
                        "amount": 721,
                        "currency": "EUR",
                        "text": "EUR 721",
                    },
                    "top_result_summary": {
                        "result_id": "ai-priced",
                        "carriers": ["Air India"],
                        "duration_minutes": 870,
                        "stops": {"count": 1, "text": "1 stop"},
                    },
                    "scoring_explanation": {
                        "policy": "comfort_aware_v1",
                        "google_flights_filters_applied": False,
                        "airline_preference": {
                            "preferred_airlines": ["Air India"],
                            "status": "matched",
                            "matched_carriers": ["Air India"],
                            "matched_carrier_count": 1,
                            "visible_carriers": ["Air India"],
                            "visible_carrier_count": 1,
                            "local_ranking_preference_applied": True,
                            "google_flights_filters_applied": False,
                        },
                    },
                    "evidence": {
                        "run_id": "gf-priced",
                        "source_surfaces": ["primary-results-visible-text"],
                        "artifacts": ["snapshot.json"],
                    },
                }
            ],
            "warnings": [],
        }
    ]
    _write_json(report_root / "outputs" / "date-scan-window.json", date_scan)

    summary = summarize_india_trip_report(
        report_root,
        project_root=report_root / "state",
        live_attempted=True,
    )

    assert summary["verdict"] == "useful"
    assert summary["status"] == "ok"
    assert summary["counts"]["ranked_pairs_with_evidence"] == 1
    assert summary["counts"]["air_india_ranked_pairs"] == 1
    assert summary["ranked_options"][0]["best_observed_price"]["amount"] == 721
    assert summary["airline_preference"]["google_flights_filters_applied"] is False
    assert summary["airline_preference"]["ranked_pairs_matching_preference"] == 1
    assert summary["airline_preference"]["preference_observation_status"] == "matched"

    report = render_markdown_report(summary)

    assert "- Google Flights airline filter applied: `False`." in report
    assert "- Ranked pairs matching preference: 1 of 1 visible-carrier ranked pairs." in report
    assert "Air India matched at least one visible row or ranked option" in report
    assert "Preference: matched; preferred Air India" in report


def test_india_trip_reducer_ranks_concrete_live_rows_when_date_scan_has_no_ranked_pairs(
    tmp_path: Path,
) -> None:
    report_root = _write_zero_row_report(tmp_path)
    _write_json(
        report_root / "outputs" / "live-search-100.json",
        [
            {
                "query_id": "cph-lko-family-airindia-2026-11-24-2027-01-05",
                "status": "ok",
                "query_population": {"status": "encoded"},
                "results": [
                    {
                        "result_id": "ba-2548",
                        "source_surface": "primary-results-visible-text",
                        "carriers": ["British Airways", "IndiGo"],
                        "duration_minutes": 1100,
                        "stops": {"count": 2, "text": "2 stops"},
                        "price": {"amount": 2548, "currency": "EUR", "text": "EUR 2548"},
                        "evidence": {
                            "source_surfaces": ["primary-results-visible-text"],
                            "artifacts": ["snapshot-ba.json"],
                        },
                    }
                ],
                "cache": {"price_observations_written": 1},
                "evidence": {
                    "run_id": "gf-ba",
                    "source_surfaces": ["cdp:snapshot"],
                    "artifacts": ["command-log-ba.json"],
                },
            },
            {
                "query_id": "cph-lko-family-airindia-2026-11-25-2027-01-03",
                "status": "ok",
                "query_population": {"status": "encoded"},
                "results": [
                    {
                        "result_id": "klm-2608",
                        "source_surface": "primary-results-visible-text",
                        "carriers": ["KLM", "IndiGo"],
                        "duration_minutes": 1135,
                        "stops": {"count": 2, "text": "2 stops"},
                        "price": {"amount": 2608, "currency": "EUR", "text": "EUR 2608"},
                        "evidence": {
                            "source_surfaces": ["primary-results-visible-text"],
                            "artifacts": ["snapshot-klm.json"],
                        },
                    }
                ],
                "cache": {"price_observations_written": 1},
                "evidence": {
                    "run_id": "gf-klm",
                    "source_surfaces": ["cdp:snapshot"],
                    "artifacts": ["command-log-klm.json"],
                },
            },
        ],
    )

    summary = summarize_india_trip_report(
        report_root,
        project_root=report_root / "state",
        live_attempted=True,
    )

    assert summary["verdict"] == "useful"
    assert summary["ranking_source"] == "live_search_results"
    assert summary["counts"]["ranked_pairs"] == 2
    assert summary["counts"]["ranked_pairs_with_evidence"] == 2
    assert summary["ranked_options"][0]["departure_date"] == "2026-11-24"
    assert summary["ranked_options"][0]["return_date"] == "2027-01-05"
    assert summary["ranked_options"][0]["source"] == "live_search_results"
    assert summary["ranked_options"][0]["evidence"]["artifacts"] == [
        "snapshot-ba.json",
        "command-log-ba.json",
    ]
    assert summary["airline_preference"]["preference_observation_status"] == "not_matched"

    report = render_markdown_report(summary)

    assert "| Ranking source | live_search_results |" in report
    assert "`2026-11-24` to `2027-01-05`: EUR 2548" in report


def test_india_trip_workflow_reduces_existing_outputs_and_writes_report(
    tmp_path: Path,
) -> None:
    report_root = _write_zero_row_report(tmp_path)

    exit_code, payload = run_india_trip_workflow(report_root=report_root)

    assert exit_code == 3
    assert payload["verdict"] == "not_useful"
    assert Path(payload["artifacts"]["summary_json"]).is_file()
    assert Path(payload["artifacts"]["markdown_report"]).is_file()
    assert (
        "no parsed live result rows"
        in json.loads(Path(payload["artifacts"]["summary_json"]).read_text())["reason"]
    )
    report = Path(payload["artifacts"]["markdown_report"]).read_text()
    assert "- Preference observation: `not_observable`." in report
    assert (
        "Google Flights airline filters were not applied; ranking preference was local only"
        in report
    )


def test_india_trip_live_workflow_refuses_over_budget_cdp_preflight(
    tmp_path: Path,
) -> None:
    fake_cdp = tmp_path / "fake-cdp"
    fake_cdp.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if 'pages' in sys.argv:\n"
        "    print(json.dumps({'budget': {'tab_count': 26, 'max_tabs': 25, "
        "'tabs_over_budget': True, 'window_count': 1, 'max_windows': 5, "
        "'windows_over_budget': False, 'browser_mode': 'headless'}, 'ok': True}))\n"
        "else:\n"
        "    print(json.dumps({'ok': True}))\n"
    )
    fake_cdp.chmod(0o755)
    report_root = tmp_path / "report"

    exit_code, payload = run_india_trip_workflow(
        report_root=report_root,
        execute_live=True,
        cdp_executable=str(fake_cdp),
    )

    assert exit_code == 4
    assert payload["status"] == "blocked"
    assert payload["verdict"] == "blocked"
    assert "tabs were over budget" in payload["reason"]
    assert (report_root / "cdp" / "preflight-pages.json").is_file()
    assert not (report_root / "outputs" / "live-search-100.json").exists()


def _write_zero_row_report(tmp_path: Path) -> Path:
    report_root = tmp_path / "report"
    (report_root / "outputs").mkdir(parents=True)
    (report_root / "cdp").mkdir()
    state = init_app_state(report_root / "state")
    write_india_trip_inputs(report_root)

    _write_json(
        report_root / "outputs" / "live-route-cph.json",
        {
            "status": "tool_error",
            "error": 'fill locator label "Where to?" matched no elements',
            "choices": [],
            "evidence": {"source_surfaces": ["cdp:open", "cdp:wait"]},
        },
    )
    _write_text(report_root / "outputs" / "live-route-cph.exit.txt", "6\n")
    _write_json(
        report_root / "outputs" / "live-route-lucknow.json",
        {
            "status": "tool_error",
            "error": "Cannot find default execution context",
            "choices": [],
            "evidence": {"source_surfaces": ["cdp:open", "cdp:wait"]},
        },
    )
    _write_text(report_root / "outputs" / "live-route-lucknow.exit.txt", "6\n")
    _write_json(
        report_root / "outputs" / "live-search-100.json",
        [
            {
                "query_id": "cph-lko-family-airindia-2026-11-21-2027-01-01",
                "status": "experimental",
                "query_population": {"status": "encoded"},
                "results": [],
                "unsupported": [{"field": "live_result_extraction", "status": "deferred"}],
                "cache": {
                    "database_path": str(state.database_path),
                    "price_observations_written": 0,
                },
                "evidence": {"source_surfaces": ["cdp:snapshot"], "artifacts": ["snapshot.json"]},
            }
            for _ in range(100)
        ],
    )
    _write_text(report_root / "outputs" / "live-search-100.exit.txt", "0\n")
    _write_text(report_root / "outputs" / "live-search-100.time.txt", "real 378.27\n")
    _write_json(
        report_root / "outputs" / "date-scan-window.json",
        [
            {
                "status": "experimental",
                "generated_pairs": 100,
                "probed_pairs": 0,
                "coverage_counts": {
                    "fresh_cache": 0,
                    "probed": 0,
                    "unsupported": 0,
                    "skipped": 100,
                },
                "ranked_pairs": [],
                "warnings": ["needs a live probe"] * 100,
            }
        ],
    )
    _write_text(report_root / "outputs" / "date-scan-window.exit.txt", "0\n")
    _write_json(
        report_root / "cdp" / "preflight-pages.json",
        {
            "budget": {
                "tab_count": 5,
                "max_tabs": 25,
                "tabs_over_budget": False,
                "window_count": 1,
                "max_windows": 5,
                "windows_over_budget": False,
                "browser_mode": "headless",
            },
            "ok": True,
        },
    )
    _write_json(
        report_root / "cdp" / "postrun-pages.json",
        {
            "tab_count": 5,
            "max_tabs": 25,
            "tabs_over_budget": False,
            "window_count": 1,
            "max_windows": 5,
            "windows_over_budget": False,
            "browser_mode": "headless",
        },
    )
    close_dir = report_root / "state" / "runs" / "gf-test-run"
    close_dir.mkdir(parents=True)
    _write_json(
        close_dir / "managed-tab-close.json", {"status": "ok", "json_payload": {"ok": True}}
    )
    return report_root


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
