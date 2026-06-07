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
from gflights.live_route import run_live_route_resolution
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

INTENT_HELP = """Parse and normalize SearchIntent JSON before search or date scans.

Use this when an agent has one intent object or a JSON array and needs to
validate the contract before opening a browser or scanning date windows.

Examples:
  gflights intent parse --input-json intents.json --json
"""

PROJECT_HELP = """Create and inspect user-local state roots such as ~/.gflights-search.

State roots contain config.json, cache.sqlite, fixtures/, and runs/ so live
searches and replay evidence can be task-scoped and inspectable.

Examples:
  gflights project init --path ~/.gflights-search --json
"""

ROUTE_HELP = """Resolve city and airport route choices from reviewed evidence.

Use route resolution before search when text such as Washington DC or Lucknow
could map to multiple city or airport choices. Without --offline-fixtures this
opens the live Google Flights shell through cdp and records task-scoped route
autocomplete evidence, returning visible choices when parseable and stopping on
browser safety boundaries.

Examples:
  gflights route resolve --input-text CPH --json
  gflights route resolve --input-text CPH --offline-fixtures fixtures --json
  gflights route resolve --input-text "Washington DC" --offline-fixtures fixtures --json
"""

DATES_HELP = """Scan date windows using cache, probes, or deterministic fixtures.

This command expands date windows into concrete date pairs, uses fresh cache
observations when available, and returns ranked pairs with evidence.

Examples:
  gflights dates scan --input-json intents.json --project-root ~/.gflights-search --json
  gflights dates scan --input-json intents.json --offline-fixtures fixtures --json
"""

ITINERARY_HELP = """Inspect selected itinerary evidence without entering checkout.

The command reads visible itinerary detail from Google Flights and stops before
provider checkout, payment, login, or personal-data entry.

Examples:
  gflights itinerary inspect --booking-url URL --browser-mode headless --json
"""

EVIDENCE_HELP = """Replay redacted fixtures or capture task-scoped evidence.

Replay is the deterministic offline path for default validation. Captures should be redacted before publication or fixture use.

Examples:
  gflights evidence replay fixtures/primary_results_visible_text_fixture.json --json
"""

CODEC_HELP = """Decode encoded query/protobuf-like evidence with confidence metadata.

Use this for fixture-backed tfs/tfu values; decoded wire paths are evidence, not
proof of stable Google Flights semantics by themselves.

Examples:
  gflights codec decode --fixture fixtures/codec_tfu_price_fixture.json --json
"""

app = typer.Typer(
    help=ROOT_HELP,
    no_args_is_help=True,
)
intent_app = typer.Typer(help=INTENT_HELP)
project_app = typer.Typer(help=PROJECT_HELP)
route_app = typer.Typer(help=ROUTE_HELP)
dates_app = typer.Typer(help=DATES_HELP)
itinerary_app = typer.Typer(help=ITINERARY_HELP)
evidence_app = typer.Typer(help=EVIDENCE_HELP)
codec_app = typer.Typer(help=CODEC_HELP)

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
    """Emit JSON Schema for agent-facing contracts.

    Examples:
      gflights schema --model search-intent --json
    """
    del json_output
    try:
        emit(services.json_schema_for(model))
    except services.ServiceError as error:
        emit_service_error(error)


@app.command("doctor")
def doctor_command(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Report browser defaults, state paths, cache policy, and package health.

    Examples:
      gflights doctor --json
    """
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
    """Run live Google Flights by default, or replay fixtures when supplied.

    Input may be one SearchIntent object or a JSON array. Without
    --offline-fixtures, this opens live Google Flights through headless cdp and
    writes task-scoped run evidence under the state root.

    Examples:
      gflights search --input-json intents.json --json
      gflights search --input-json intents.json --offline-fixtures fixtures --json
      gflights search --input-json intents.json --browser-mode headed --json
    """
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
    """Validate and normalize SearchIntent JSON without opening a browser.

    Examples:
      gflights intent parse --input-json intents.json --json
    """
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
    """Create a state root for cache, fixtures, and task-scoped run artifacts.

    Examples:
      gflights project init --path ~/.gflights-search --json
    """
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
    browser_mode: BrowserMode = typer.Option(
        "headless",
        "--browser-mode",
        help="Browser mode for live cdp route evidence; use headed only for fallback/debugging.",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="State root for config, cache.sqlite, fixtures, and runs; defaults to ~/.gflights-search.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return one route choice or explicit ambiguous candidates from evidence.

    Without --offline-fixtures, this command opens Google Flights through live
    cdp, fills the route autocomplete field, records evidence under the state
    root, and returns visible choices when parser fixtures support them. If no
    choices are parseable, it returns explicit unsupported/deferred output.

    Examples:
      gflights route resolve --input-text CPH --json
      gflights route resolve --input-text CPH --offline-fixtures fixtures --json
      gflights route resolve --input-text CPH --browser-mode headed --json
      gflights route resolve --input-text "Washington DC" --offline-fixtures fixtures --json
    """
    del json_output
    if offline_fixtures is None:
        exit_code, payload = asyncio.run(
            run_live_route_resolution(
                input_text=input_text,
                browser_mode=browser_mode,
                project_root=project_root,
            )
        )
        emit(payload, exit_code)
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
    """Expand date windows, use fresh cache/probes, and rank candidate date pairs.

    Examples:
      gflights dates scan --input-json intents.json --project-root ~/.gflights-search --json
      gflights dates scan --input-json intents.json --offline-fixtures fixtures --json
    """
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
    """Inspect visible itinerary details and stop before provider checkout.

    The command must not click provider Continue, enter checkout, enter payment
    or personal data, or attempt account login.

    Examples:
      gflights itinerary inspect --booking-url URL --browser-mode headless --json
    """
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
    """Replay a redacted fixture offline and emit parsed evidence-backed JSON.

    Examples:
      gflights evidence replay fixtures/primary_results_visible_text_fixture.json --json
    """
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
    """Decode a fixture-backed query value and report wire paths plus confidence.

    Examples:
      gflights codec decode --fixture fixtures/codec_tfu_price_fixture.json --json
    """
    del json_output
    emit(services.decode_codec_fixture(fixture))


def main() -> None:
    """Run the CLI shell."""
    app()


if __name__ == "__main__":
    main()
