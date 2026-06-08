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
        "codec",
        "doctor",
    ]:
        assert command in help_text


def test_root_help_is_self_explanatory_without_repo_docs() -> None:
    result = run_cli("--help")

    assert result.returncode == 0, result.stderr
    help_text = f"{result.stdout}\n{result.stderr}"
    for expected in [
        "Default search:",
        "headless cdp",
        "Workflow:",
        "Agent contract:",
        "Environment:",
        "~/.gflights",
        "Examples:",
        "gflights search --input-json intents.json --json",
        "Exit codes:",
        "2 invalid/ambiguous",
        "4 browser/safety stop",
        'Use "gflights <command> --help"',
    ]:
        assert expected in help_text


def test_installed_help_does_not_expose_tdd_fixture_workflows() -> None:
    help_commands = [
        ("--help",),
        ("search", "--help"),
        ("route", "resolve", "--help"),
        ("dates", "scan", "--help"),
        ("project", "init", "--help"),
        ("codec", "decode", "--help"),
    ]
    forbidden = [
        "--offline-fixtures",
        "offline fixture",
        "offline-fixture",
        "fixture",
        "fixture replay",
        "fixtures/",
        "gflights evidence replay",
        "--fixture",
    ]

    for args in help_commands:
        result = run_cli(*args)
        assert result.returncode == 0, f"{args}: {result.stderr}"
        help_text = f"{result.stdout}\n{result.stderr}".casefold()
        for phrase in forbidden:
            assert phrase not in help_text, f"{args}: leaked {phrase!r}\n{help_text}"


def test_installed_package_sources_do_not_ship_tdd_fixture_workflows() -> None:
    package_root = ROOT / "src" / "gflights"
    leaked = [
        path.relative_to(ROOT)
        for path in sorted(package_root.rglob("*.py"))
        if "fixture" in path.read_text().casefold()
    ]

    assert leaked == []


def test_every_command_help_has_examples_and_documented_options() -> None:
    expectations = {
        ("intent", "--help"): ["Examples:", "gflights intent parse"],
        ("project", "--help"): ["Examples:", "gflights project init"],
        ("route", "--help"): ["Examples:", "gflights route resolve"],
        ("dates", "--help"): ["Examples:", "gflights dates scan"],
        ("itinerary", "--help"): [
            "Examples:",
            "gflights itinerary select",
            "gflights itinerary inspect",
        ],
        ("codec", "--help"): ["Examples:", "gflights codec decode"],
        ("schema", "--help"): [
            "Examples:",
            "--model",
            "Schema model name",
            "--json",
            "Emit machine-readable JSON",
        ],
        ("doctor", "--help"): ["Examples:", "--json", "browser defaults"],
        ("search", "--help"): [
            "Examples:",
            "--input-json",
            "JSON array",
            "live Google Flights",
            "--browser-mode",
            "headless",
            "--concurrency",
            "--project-root",
            "~/.gflights",
        ],
        ("intent", "parse", "--help"): ["Examples:", "--input-json", "JSON array"],
        ("project", "init", "--help"): ["Examples:", "--path", "config.json"],
        ("route", "resolve", "--help"): [
            "Examples:",
            "--input-text",
            "Airport code",
            "--browser-mode",
            "--project-root",
        ],
        ("dates", "scan", "--help"): [
            "Examples:",
            "--input-json",
            "date windows",
            "--project-root",
            "cache.sqlite",
            "--live-probe",
            "--max-probes",
            "--probe-timeout-seconds",
            "--browser-mode",
        ],
        ("itinerary", "inspect", "--help"): [
            "Examples:",
            "--booking-url",
            "provider Continue",
            "--browser-mode",
            "headed",
        ],
        ("itinerary", "select", "--help"): [
            "Examples:",
            "--search-url",
            "--preferred-carrier",
            "--require-nonstop",
            "--row-rank",
            "--browser-mode",
            "--project-root",
            "booking-summary",
        ],
        ("codec", "decode", "--help"): ["Examples:", "--key", "--value", "wire paths"],
    }

    for args, expected_items in expectations.items():
        result = run_cli(*args)

        assert result.returncode == 0, f"{args}: {result.stderr}"
        help_text = f"{result.stdout}\n{result.stderr}"
        for expected in expected_items:
            assert expected in help_text, f"{args}: missing {expected!r}\n{help_text}"


def test_itinerary_inspect_help_exposes_live_agent_options() -> None:
    result = run_cli("itinerary", "inspect", "--help")

    assert result.returncode == 0, result.stderr
    help_text = f"{result.stdout}\n{result.stderr}"
    assert "--booking-url" in help_text
    assert "--browser-mode" in help_text
    assert "--project-root" in help_text


def test_route_resolve_help_exposes_agent_options() -> None:
    result = run_cli("route", "resolve", "--help")

    assert result.returncode == 0, result.stderr
    help_text = f"{result.stdout}\n{result.stderr}"
    assert "--input-text" in help_text
    assert "--browser-mode" in help_text
    assert "--project-root" in help_text


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
    for tdd_only_or_removed_name in [
        "traveler_profiles",
        "airline_preferences",
        "consider_all_airlines",
    ]:
        assert tdd_only_or_removed_name not in schema["properties"]
    route_endpoint = schema["$defs"]["RouteEndpoint"]
    route_choice = schema["$defs"]["RouteChoice"]
    assert "selected" in route_endpoint["properties"]
    assert set(route_choice["properties"]) >= {
        "text",
        "kind",
        "display_name",
        "code_or_id",
        "confidence",
        "evidence",
    }


def test_project_init_creates_project_local_state(tmp_path: Path) -> None:
    result = run_cli("project", "init", "--path", str(tmp_path), "--json")

    assert result.returncode == 0, result.stderr
    payload = assert_json_stdout(result)
    assert payload["status"] == "ok"
    assert payload["project_root"] == str(tmp_path)
    assert (tmp_path / "config.json").is_file()
    assert (tmp_path / "cache").is_dir()
    assert (tmp_path / "cache" / "cache.sqlite").is_file()
    assert (tmp_path / "artifacts").is_dir()
    assert not (tmp_path / "fixtures").exists()
    assert (tmp_path / "runs").is_dir()
    assert payload["cache_root"] == str(tmp_path / "cache")
    assert payload["database_path"] == str(tmp_path / "cache" / "cache.sqlite")
    assert "fixture_root" not in payload
    assert payload["run_root"] == str(tmp_path / "runs")


def test_intent_parse_accepts_json_array_and_preserves_order() -> None:
    expected_query_ids = [item["query_id"] for item in json.loads(INTENTS.read_text())]

    result = run_cli("intent", "parse", "--input-json", str(INTENTS), "--json")

    assert result.returncode == 0, result.stderr
    payload = assert_json_stdout(result)
    assert isinstance(payload, list)
    assert [item["query_id"] for item in payload] == expected_query_ids
    assert all(item["status"] == "ok" for item in payload)


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
    assert payload["app_state"]["cache_root"] == str(tmp_path / "app-state" / "cache")
    assert payload["app_state"]["database_path"] == str(
        tmp_path / "app-state" / "cache" / "cache.sqlite"
    )
    assert "fixture_root" not in payload["app_state"]
    assert payload["app_state"]["cache_max_age_seconds"] == 6 * 60 * 60


def test_codec_decode_reports_generic_wire_paths_from_raw_value() -> None:
    result = run_cli(
        "codec",
        "decode",
        "--key",
        "tfu",
        "--value",
        "EgYIAhAAGAA",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = assert_json_stdout(result)
    assert payload["status"] == "ok"
    assert payload["key"] == "tfu"
    assert payload["confidence"] == "weak"
    assert payload["confidence"] != "proven"
    assert {"path": "tfu.2.1", "wire_type": "varint", "value": 2} in payload["wire_paths"]
    assert payload["codec"]["round_trip_ok"] is True
    assert {"path": "tfu.2.1", "wire_type": "varint", "value": 2} in payload["codec"][
        "observed_wire_paths"
    ]


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
