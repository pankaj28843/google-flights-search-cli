from __future__ import annotations

from datetime import UTC, datetime
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from gflights.app_state import PriceCache, init_app_state

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
INTENTS = FIXTURES / "search_intents.json"


def run_cli(
    *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    command = ["uv", "run", "--quiet", "gflights", *args]
    run_env = os.environ.copy()
    run_env.update(env or {})
    run_env.setdefault("PYTHONUTF8", "1")
    return subprocess.run(
        command,
        cwd=cwd or ROOT,
        env=run_env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def assert_json_stdout(result: subprocess.CompletedProcess[str]) -> Any:
    assert result.stdout.strip(), f"expected JSON on stdout, stderr was:\n{result.stderr}"
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"stdout was not valid JSON:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from exc


def test_help_lists_atomic_command_families() -> None:
    result = run_cli("--help")

    assert result.returncode == 0, result.stderr
    help_text = f"{result.stdout}\n{result.stderr}"
    for command in [
        "schema",
        "intent",
        "project",
        "route",
        "dates",
        "search",
        "itinerary",
        "evidence",
        "codec",
        "doctor",
    ]:
        assert command in help_text


def test_schema_search_intent_json_contract() -> None:
    result = run_cli("schema", "--model", "search-intent", "--json")

    assert result.returncode == 0, result.stderr
    schema = assert_json_stdout(result)
    assert schema["title"] == "SearchIntent"
    assert schema["type"] == "object"
    for property_name in [
        "query_id",
        "origin",
        "destination",
        "trip_type",
        "departure_window",
        "return_window",
        "passengers",
        "cabin",
        "currency",
        "language",
        "sort",
    ]:
        assert property_name in schema["properties"]


def test_project_init_creates_project_local_state(tmp_path: Path) -> None:
    result = run_cli("project", "init", "--path", str(tmp_path), "--json")

    assert result.returncode == 0, result.stderr
    payload = assert_json_stdout(result)
    assert payload["status"] == "ok"
    assert payload["project_root"] == str(tmp_path)
    assert (tmp_path / "config.json").is_file()
    assert (tmp_path / "cache.sqlite").is_file()
    assert (tmp_path / "artifacts").is_dir()
    assert (tmp_path / "fixtures").is_dir()
    assert (tmp_path / "runs").is_dir()
    assert payload["fixture_root"] == str(tmp_path / "fixtures")
    assert payload["run_root"] == str(tmp_path / "runs")


def test_intent_parse_accepts_json_array_and_preserves_order() -> None:
    expected_query_ids = [item["query_id"] for item in json.loads(INTENTS.read_text())]

    result = run_cli("intent", "parse", "--input-json", str(INTENTS), "--json")

    assert result.returncode == 0, result.stderr
    payload = assert_json_stdout(result)
    assert isinstance(payload, list)
    assert [item["query_id"] for item in payload] == expected_query_ids
    assert all(item["status"] == "ok" for item in payload)


def test_dates_scan_offline_fixtures_returns_json_array_with_explanations() -> None:
    result = run_cli(
        "dates",
        "scan",
        "--input-json",
        str(INTENTS),
        "--offline-fixtures",
        str(FIXTURES),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = assert_json_stdout(result)
    assert isinstance(payload, list)
    assert [item["query_id"] for item in payload] == [
        "del-cph-senior-oct-nov",
        "cph-lko-oneway-jun",
    ]
    for item in payload:
        assert item["status"] == "ok"
        assert item["generated_pairs"] >= 1
        assert item["ranked_pairs"]
        assert "scoring_explanation" in item["ranked_pairs"][0]
        assert item["evidence"]["source_surfaces"]


def test_dates_scan_project_root_uses_fresh_cache_without_live_browser(tmp_path: Path) -> None:
    state = init_app_state(tmp_path / "state")
    PriceCache(state.database_path).put_price(
        cache_key="cached-date-scan-cph-del",
        query_id="cached-date-scan",
        departure_date="2026-10-01",
        return_date="2026-11-24",
        currency="EUR",
        price_amount=701,
        price_payload={
            "result_id": "cached-date-scan-result",
            "price": {"amount": 701, "currency": "EUR", "text": "EUR 701"},
            "carriers": ["Air India"],
            "duration_minutes": 870,
            "stops": {"count": 1, "text": "1 stop"},
        },
        captured_at=datetime.now(UTC),
        source_run_id="gf-cache-date-scan",
    )
    intent_path = tmp_path / "intent.json"
    intent_path.write_text(
        json.dumps(
            {
                "query_id": "cached-date-scan",
                "origin": {"text": "Delhi", "kind": "city_or_airport"},
                "destination": {"text": "Copenhagen", "kind": "city_or_airport"},
                "trip_type": "round_trip",
                "departure_window": {"start": "2026-10-01", "end": "2026-10-01"},
                "return_window": {"start": "2026-11-24", "end": "2026-11-24"},
                "passengers": {"adults": 2},
                "cabin": "economy",
                "currency": "EUR",
                "language": "en",
                "sort": "price",
            }
        )
    )

    result = run_cli(
        "dates",
        "scan",
        "--input-json",
        str(intent_path),
        "--project-root",
        str(state.root),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = assert_json_stdout(result)
    assert payload[0]["coverage_counts"]["fresh_cache"] == 1
    assert payload[0]["pair_coverage"][0]["status"] == "fresh_cache"
    assert payload[0]["ranked_pairs"][0]["best_observed_price"]["amount"] == 701
    assert payload[0]["ranked_pairs"][0]["evidence"]["source_surfaces"] == ["sqlite-cache"]


def test_evidence_replay_parses_saved_fixture_offline() -> None:
    result = run_cli("evidence", "replay", str(FIXTURES / "offline_results_fixture.json"), "--json")

    assert result.returncode == 0, result.stderr
    payload = assert_json_stdout(result)
    assert payload["status"] == "ok"
    assert payload["confidence"] == "strong"
    assert payload["evidence"]["run_id"] == "gf-20260607-073838-04-result-controls-cheap-dates"
    assert payload["results"][0]["source_surface"] == "primary-results-dom"
    assert payload["results"][0]["carriers"] == ["KLM", "IndiGo"]


def test_evidence_replay_extracts_visible_text_primary_results() -> None:
    result = run_cli(
        "evidence",
        "replay",
        str(FIXTURES / "primary_results_visible_text_fixture.json"),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = assert_json_stdout(result)
    assert payload["status"] == "ok"
    assert payload["confidence"] == "strong"
    assert payload["results"][0]["source_surface"] == "primary-results-visible-text"
    assert payload["results"][0]["carriers"] == ["KLM", "IndiGo"]
    assert payload["results"][0]["price"] == {"amount": 3206, "currency": "EUR", "text": "€3,206"}


def test_evidence_replay_extracts_selected_itinerary_details() -> None:
    result = run_cli(
        "evidence",
        "replay",
        str(FIXTURES / "selected_itinerary_visible_text_fixture.json"),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = assert_json_stdout(result)
    itinerary = payload["itinerary"]
    assert payload["status"] == "ok"
    assert itinerary["segments"][0]["flight_number"] == "KL 1268"
    assert itinerary["segments"][3]["flight_number"] == "6E 6480"
    assert itinerary["baggage"]["included"] == ["1 free carry-on", "1st checked bag free"]
    assert itinerary["booking_options"][0]["provider"] == "KLM"
    assert itinerary["booking_options"][1]["price"] == {
        "amount": 1756,
        "currency": "EUR",
        "text": "EUR 1,756",
    }
    assert itinerary["terminal_info"]["status"] == "not_found"
    assert itinerary["boundary"]["provider_continue_clicked"] is False
    assert itinerary["boundary"]["checkout_entered"] is False
    assert itinerary["baggage_policy_links"][0]["url"].startswith("https://www.klm.co.uk/")


def test_doctor_reports_headless_default_and_live_search_policy(tmp_path: Path) -> None:
    result = run_cli(
        "doctor",
        "--json",
        env={"GFLIGHTS_SEARCH_HOME": str(tmp_path / "app-state")},
    )

    assert result.returncode == 0, result.stderr
    payload = assert_json_stdout(result)
    assert payload["status"] == "ok"
    assert payload["browser"]["default_mode"] == "headless"
    assert payload["browser"]["headed_fallback_allowed"] is True
    assert payload["validation"]["live_google_flights_by_default"] is True
    assert payload["app_state"]["config_path"] == str(tmp_path / "app-state" / "config.json")
    assert payload["app_state"]["database_path"] == str(tmp_path / "app-state" / "cache.sqlite")
    assert payload["app_state"]["cache_max_age_seconds"] == 6 * 60 * 60


def test_blocked_headless_fixture_recommends_headed_fallback() -> None:
    result = run_cli(
        "evidence", "replay", str(FIXTURES / "blocked_headless_fixture.json"), "--json"
    )

    assert result.returncode == 4, result.stderr
    payload = assert_json_stdout(result)
    assert payload["status"] == "blocked"
    assert payload["browser_mode"] == "headless"
    assert payload["fallback"]["recommended_browser_mode"] == "headed"
    assert payload["evidence"]["fixture_id"] == "headless-blocked-policy-20260607"


def test_codec_decode_reports_wire_paths_and_confidence_from_fixture() -> None:
    result = run_cli(
        "codec", "decode", "--fixture", str(FIXTURES / "codec_tfu_price_fixture.json"), "--json"
    )

    assert result.returncode == 0, result.stderr
    payload = assert_json_stdout(result)
    assert payload["status"] == "ok"
    assert payload["key"] == "tfu"
    assert payload["confidence"] == "strong"
    assert payload["confidence"] != "proven"
    assert {"path": "tfu.2.1", "value": 2, "hypothesis": "price sort"} in payload["wire_paths"]
    assert payload["codec"]["round_trip_ok"] is True
    assert {"path": "tfu.2.1", "wire_type": "varint", "value": 2} in payload["codec"][
        "observed_wire_paths"
    ]


def test_deferred_live_google_filter_exits_unsupported() -> None:
    result = run_cli(
        "search",
        "--input-json",
        str(FIXTURES / "unsupported_live_filter_intent.json"),
        "--offline-fixtures",
        str(FIXTURES),
        "--json",
    )

    assert result.returncode == 3, result.stderr
    payload = assert_json_stdout(result)
    assert payload["status"] in {"unsupported", "deferred"}
    assert any(
        item["field"] == "google_filters.maximum_layover_minutes" for item in payload["unsupported"]
    )


def test_search_offline_accepts_json_array_and_preserves_order() -> None:
    result = run_cli(
        "search",
        "--input-json",
        str(INTENTS),
        "--offline-fixtures",
        str(FIXTURES),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = assert_json_stdout(result)
    assert isinstance(payload, list)
    assert [item["query_id"] for item in payload] == [
        "del-cph-senior-oct-nov",
        "cph-lko-oneway-jun",
    ]
    assert all(item["status"] == "ok" for item in payload)


def test_make_install_editable_exposes_agent_entrypoint(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "UV_TOOL_DIR": str(tmp_path / "tools"),
            "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
            "UV_CACHE_DIR": str(tmp_path / "cache"),
            "PYTHONUTF8": "1",
        }
    )
    install = subprocess.run(
        ["make", "install-editable"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert install.returncode == 0, install.stderr

    executable = tmp_path / "bin" / ("gflights.exe" if os.name == "nt" else "gflights")
    assert executable.exists()
    doctor = subprocess.run(
        [str(executable), "doctor", "--json"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert doctor.returncode == 0, doctor.stderr
    payload = assert_json_stdout(doctor)
    assert payload["status"] == "ok"
