"""Typer adapter for the agentic Google Flights CLI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import typer

from gflights import services
from gflights.browser import BrowserMode
from gflights.live_itinerary import run_live_itinerary_inspection
from gflights.live_search import run_live_search

ROOT_HELP = """Agent-first Google Flights CLI for live searches, offline replay, and evidence-backed JSON.

Workflow:
  gflights schema --model search-intent --json        Inspect the input contract
  gflights route resolve --input-text CPH --json      Resolve an airport or city
  gflights search --input-json intents.json --json    Run live Google Flights
  gflights dates scan --input-json intents.json --json Rank date combinations
  gflights itinerary inspect --booking-url URL --json Inspect visible itinerary details

Default search:
  gflights search opens live Google Flights through headless cdp unless --offline-fixtures is supplied.
  Use --browser-mode headed only when a blocked result recommends headed fallback.

Environment:
  GFLIGHTS_SEARCH_HOME overrides the state root.
  The default state root is ~/.gflights-search with config.json, cache.sqlite, fixtures/, and runs/.

Agent contract:
  Data commands emit JSON. Unsupported, ambiguous, blocked, and stale behavior is explicit instead of guessed.
  Use --offline-fixtures for deterministic replay without opening Google Flights.

Examples:
  gflights doctor --json
  gflights search --input-json intents.json --json
  gflights search --input-json intents.json --offline-fixtures fixtures --json
  gflights route resolve --input-text "Washington DC" --offline-fixtures fixtures --json
  gflights dates scan --input-json intents.json --project-root ~/.gflights-search --json

Exit codes:
  0 ok
  2 invalid/ambiguous input
  3 unsupported/deferred
  4 browser/safety stop
  5 stale evidence
  6 tool/config failure

Use "gflights <command> --help" for command-specific inputs and examples.
"""

app = typer.Typer(
    help=ROOT_HELP,
    no_args_is_help=True,
)
intent_app = typer.Typer(help="Parse and normalize SearchIntent JSON before search or date scans.")
project_app = typer.Typer(
    help="Create and inspect user-local state roots such as ~/.gflights-search."
)
route_app = typer.Typer(help="Resolve city and airport route choices from reviewed evidence.")
dates_app = typer.Typer(help="Scan date windows using cache, probes, or deterministic fixtures.")
itinerary_app = typer.Typer(help="Inspect selected itinerary evidence without entering checkout.")
evidence_app = typer.Typer(help="Replay redacted fixtures or capture task-scoped evidence.")
codec_app = typer.Typer(
    help="Decode encoded query/protobuf-like evidence with confidence metadata."
)

app.add_typer(intent_app, name="intent")
app.add_typer(project_app, name="project")
app.add_typer(route_app, name="route")
app.add_typer(dates_app, name="dates")
app.add_typer(itinerary_app, name="itinerary")
app.add_typer(evidence_app, name="evidence")
app.add_typer(codec_app, name="codec")


def emit(payload: Any, exit_code: int = 0) -> None:
    typer.echo(json.dumps(payload, indent=2))
    if exit_code:
        raise typer.Exit(exit_code)


def emit_service_error(error: services.ServiceError) -> None:
    emit(error.payload, error.exit_code)


@app.command("schema")
def schema_command(
    model: str = typer.Option(..., "--model", help="Schema model name, for example search-intent."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Emit JSON Schema for agent-facing contracts."""
    del json_output
    try:
        emit(services.json_schema_for(model))
    except services.ServiceError as error:
        emit_service_error(error)


@app.command("doctor")
def doctor_command(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Report browser defaults, state paths, cache policy, and package health."""
    del json_output
    emit(services.doctor_report())


@app.command("search")
def search_command(
    input_json: Path = typer.Option(
        ...,
        "--input-json",
        help="Path to one SearchIntent object or a JSON array of SearchIntent objects.",
    ),
    offline_fixtures: Path | None = typer.Option(
        None,
        "--offline-fixtures",
        help="Replay deterministic fixtures instead of opening live Google Flights.",
    ),
    live_cdp: bool = typer.Option(
        False,
        "--live-cdp",
        help="Compatibility flag; live cdp is already the default without --offline-fixtures.",
    ),
    live_form: bool = typer.Option(
        False,
        "--live-form",
        help="Also attempt evidence-scoped form interaction before result capture.",
    ),
    browser_mode: BrowserMode = typer.Option(
        "headless",
        "--browser-mode",
        help="Browser mode for live cdp runs; use headed only for explicit fallback/debugging.",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="State root for config, cache.sqlite, fixtures, and runs; defaults to ~/.gflights-search.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Run live Google Flights by default, or replay fixtures when supplied."""
    del json_output
    del live_cdp
    if offline_fixtures is not None:
        if live_form:
            emit(
                {
                    "status": "tool_error",
                    "warnings": [],
                    "error": "--live-form cannot be combined with --offline-fixtures",
                },
                6,
            )
            return
        try:
            exit_code, payload = services.search_offline(input_json, offline_fixtures)
        except services.ServiceError as error:
            emit_service_error(error)
            return
        emit(payload, exit_code)
        return

    try:
        exit_code, payload = asyncio.run(
            run_live_search(
                input_json=input_json,
                project_root=project_root,
                browser_mode=browser_mode,
                interact_with_form=live_form,
            )
        )
    except services.ServiceError as error:
        emit_service_error(error)
        return
    emit(payload, exit_code)


@intent_app.command("parse")
def intent_parse_command(
    input_json: Path = typer.Option(
        ...,
        "--input-json",
        help="Path to one SearchIntent object or a JSON array to validate and normalize.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Validate and normalize SearchIntent JSON without opening a browser."""
    del json_output
    try:
        emit(services.parse_intents(input_json))
    except services.ServiceError as error:
        emit_service_error(error)


@project_app.command("init")
def project_init_command(
    path: Path = typer.Option(
        ...,
        "--path",
        help="State directory to create with config.json, cache.sqlite, fixtures, and runs.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Create a state root for cache, fixtures, and task-scoped run artifacts."""
    del json_output
    emit(services.init_project(path))


@route_app.command("resolve")
def route_resolve_command(
    input_text: str = typer.Option(
        ...,
        "--input-text",
        help="Airport code, city, or city-like route text to resolve, for example CPH.",
    ),
    offline_fixtures: Path | None = typer.Option(
        None,
        "--offline-fixtures",
        help="Directory or file with reviewed route_autocomplete_choices fixtures.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return one route choice or explicit ambiguous candidates from evidence."""
    del json_output
    if offline_fixtures is None:
        emit(
            {
                "status": "unsupported",
                "input_text": input_text,
                "selected": None,
                "choices": [],
                "unsupported": [{"field": "route.resolve.live", "status": "deferred"}],
                "warnings": [],
            },
            3,
        )
        return
    exit_code, payload = services.resolve_route(input_text, offline_fixtures)
    emit(payload, exit_code)


@dates_app.command("scan")
def dates_scan_command(
    input_json: Path = typer.Option(
        ...,
        "--input-json",
        help="Path to one SearchIntent object or a JSON array with date windows.",
    ),
    offline_fixtures: Path | None = typer.Option(
        None,
        "--offline-fixtures",
        help="Use deterministic fixture bootstrap behavior instead of cache/probes.",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="State root with cache.sqlite; defaults to ~/.gflights-search.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Expand date windows, use fresh cache/probes, and rank candidate date pairs."""
    del json_output
    try:
        emit(
            services.scan_dates(
                input_json,
                offline_fixtures,
                project_root=project_root,
            )
        )
    except services.ServiceError as error:
        emit_service_error(error)


@itinerary_app.command("inspect")
def itinerary_inspect_command(
    booking_url: str = typer.Option(
        ...,
        "--booking-url",
        help="Google Flights itinerary/booking URL to inspect without clicking provider Continue.",
    ),
    browser_mode: BrowserMode = typer.Option(
        "headless",
        "--browser-mode",
        help="Browser mode for live cdp inspection; headed is an explicit fallback.",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="State root for task-scoped run artifacts; defaults to ~/.gflights-search.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Inspect visible itinerary details and stop before provider checkout."""
    del json_output
    exit_code, payload = asyncio.run(
        run_live_itinerary_inspection(
            booking_url=booking_url,
            browser_mode=browser_mode,
            project_root=project_root,
        )
    )
    emit(payload, exit_code)


@evidence_app.command("replay")
def evidence_replay_command(
    fixture: Path = typer.Argument(..., help="Redacted fixture JSON file to replay offline."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Replay a redacted fixture offline and emit parsed evidence-backed JSON."""
    del json_output
    exit_code, payload = services.replay_fixture(fixture)
    emit(payload, exit_code)


@codec_app.command("decode")
def codec_decode_command(
    fixture: Path = typer.Option(
        ...,
        "--fixture",
        help="Codec fixture with a captured tfs/tfu value and expected wire-path hypotheses.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Decode a fixture-backed query value and report wire paths plus confidence."""
    del json_output
    emit(services.decode_codec_fixture(fixture))


def main() -> None:
    """Run the CLI shell."""
    app()


if __name__ == "__main__":
    main()
