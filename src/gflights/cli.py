"""Typer adapter for the agentic Google Flights CLI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import typer

from gflights import services
from gflights.browser import BrowserMode
from gflights.date_scan_live import LiveDatePairProbe
from gflights.live_itinerary import run_live_itinerary_inspection
from gflights.live_route import run_live_route_resolution
from gflights.live_search import run_live_search
from gflights.live_selection import run_live_itinerary_selection
from gflights.preflight import run_google_flights_preflight

ROOT_HELP = """Agent-first Google Flights CLI for live searches and evidence-backed JSON.

Workflow:
  gflights schema --model search-intent --json        Inspect the input contract
  gflights route resolve --input-text CPH --json      Resolve an airport or city
  gflights preflight google-flights --json            Verify live browser crawl readiness
  gflights search --input-json intents.json --json    Run live Google Flights
  gflights search --input-json intents.json --url-only --json
  gflights itinerary select --search-url URL --json   Select visible rows to a booking URL
  gflights dates scan --input-json intents.json --json Rank date combinations
  gflights itinerary inspect --booking-url URL --json Inspect visible itinerary details

Default search:
  gflights search opens live Google Flights through headless cdp by default.
  Live flow is URL open -> page settle -> visible DOM/text query -> explicit Google Flights row click only when a command needs selection.
  Use --browser-mode headed only when a blocked result recommends headed fallback.

Environment:
  GFLIGHTS_SEARCH_HOME overrides the state root.
  The default state root is ~/.gflights with config.json, cache/cache.sqlite, artifacts/, and runs/.

Agent contract:
  Data commands emit JSON. Unsupported, ambiguous, blocked, and stale behavior is explicit instead of guessed.
  Runtime evidence is task-scoped under the configured state root.

Examples:
  gflights doctor --json
  gflights search --input-json intents.json --concurrency 3 --rank balanced --top-k 10 --json
  gflights dates scan --input-json intents.json --project-root ~/.gflights --objective balanced --top-k 10 --json

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

PROJECT_HELP = """Create and inspect user-local state roots such as ~/.gflights.

State roots contain config.json, cache/cache.sqlite, artifacts/, and runs/ so
live searches and evidence can be task-scoped and inspectable.

Examples:
  gflights project init --path ~/.gflights --json
"""

PREFLIGHT_HELP = """Run browser and Google Flights readiness ceremonies.

Preflight commands use public synthetic data only. They are intended to prove
that consent, live search, row selection, return selection, and booking-summary
inspection work before a task-specific crawler uses real itinerary constraints.

Examples:
  gflights preflight google-flights --json
  gflights preflight google-flights --browser-mode headless --top-k 5 --json
"""

ROUTE_HELP = """Resolve city and airport route choices from reviewed evidence.

Use route resolution before search when text such as Washington DC or Lucknow
could map to multiple city or airport choices. This opens the live Google
Flights shell through cdp and records task-scoped route
autocomplete evidence, returning visible choices when parseable and stopping on
browser safety boundaries.

Examples:
  gflights route resolve --input-text CPH --json
  gflights route resolve --input-text "Washington DC" --json
"""

DATES_HELP = """Scan date windows using cache and bounded live probes.

This command expands date windows into concrete date pairs, uses fresh cache
observations when available, and returns ranked pairs with evidence. Live probes
are opt-in and require an explicit --max-probes limit.

Examples:
  gflights dates scan --input-json intents.json --project-root ~/.gflights --json
  gflights dates scan --input-json intents.json --project-root ~/.gflights --objective balanced --top-k 10 --json
  gflights dates scan --input-json intents.json --project-root ~/.gflights --live-probe --max-probes 5 --probe-timeout-seconds 30 --json
"""

ITINERARY_HELP = """Inspect selected itinerary evidence without entering checkout.

Use select to click explicit visible Google Flights result rows and stop at the
Google booking-summary URL. Use inspect to read visible itinerary details from a
Google booking-summary URL. Both commands stop before provider checkout,
payment, login, or personal-data entry.

Examples:
  gflights itinerary select --search-url URL --preferred-carrier "Preferred Carrier" --outbound-row-rank 2 --return-row-rank 1 --json
  gflights itinerary inspect --booking-url URL --browser-mode headless --json
"""

CODEC_HELP = """Decode encoded query/protobuf-like values with confidence metadata.

Use this for captured tfs/tfu values; decoded wire paths are evidence, not proof
of stable Google Flights semantics by themselves.

Examples:
  gflights codec decode --key tfu --value EgYIAhAAGAA --json
"""

app = typer.Typer(
    help=ROOT_HELP,
    no_args_is_help=True,
)
intent_app = typer.Typer(help=INTENT_HELP)
project_app = typer.Typer(help=PROJECT_HELP)
preflight_app = typer.Typer(help=PREFLIGHT_HELP)
route_app = typer.Typer(help=ROUTE_HELP)
dates_app = typer.Typer(help=DATES_HELP)
itinerary_app = typer.Typer(help=ITINERARY_HELP)
codec_app = typer.Typer(help=CODEC_HELP)

app.add_typer(intent_app, name="intent")
app.add_typer(project_app, name="project")
app.add_typer(preflight_app, name="preflight")
app.add_typer(route_app, name="route")
app.add_typer(dates_app, name="dates")
app.add_typer(itinerary_app, name="itinerary")
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
    live_form: bool = typer.Option(
        False,
        "--live-form",
        help="Also attempt evidence-scoped form interaction before result capture.",
    ),
    url_only: bool = typer.Option(
        False,
        "--url-only",
        help="Only encode Google Flights search URLs from SearchIntent JSON; do not open CDP.",
    ),
    browser_mode: BrowserMode = typer.Option(
        "headless",
        "--browser-mode",
        help="Browser mode for live cdp runs; use headed only for explicit fallback/debugging.",
    ),
    concurrency: int = typer.Option(
        3,
        "--concurrency",
        min=1,
        max=5,
        help="Maximum parallel live Google Flights tabs for JSON-array search input.",
    ),
    rank: str = typer.Option(
        "",
        "--rank",
        help="Comma-separated post-result objectives: cheapest, fastest, least-layover, balanced.",
    ),
    top_k: int = typer.Option(
        0,
        "--top-k",
        min=0,
        help="Emit top-K post-result alternatives for requested ranking objectives.",
    ),
    allow_transit: list[str] | None = typer.Option(
        None,
        "--allow-transit",
        help="Allowed visible transit airport code for local ranking; repeat for multiple.",
    ),
    deny_transit: list[str] | None = typer.Option(
        None,
        "--deny-transit",
        help="Disallowed visible transit airport code for local ranking; repeat for multiple.",
    ),
    managed_tab_policy: str = typer.Option(
        "new",
        "--managed-tab-policy",
        help="Managed tab policy: new or reuse. Reuse navigates an existing Google Flights tab when visible.",
    ),
    max_tabs: int = typer.Option(
        0,
        "--max-tabs",
        min=0,
        help="Pass a cdp tab budget and record tab-budget evidence when greater than zero.",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="State root for config, cache/cache.sqlite, artifacts, and runs; defaults to ~/.gflights.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Run live Google Flights by default.

    Input may be one SearchIntent object or a JSON array. This opens live
    Google Flights through headless cdp and writes task-scoped run evidence
    under the state root. The live flow is URL open -> page settle -> visible
    DOM/text query; itinerary selection commands repeat that cycle after each
    explicit Google Flights row click.

    Examples:
      gflights search --input-json intents.json --json
      gflights search --input-json concrete-intents.json --url-only --json
      gflights search --input-json intents.json --rank cheapest,fastest,least-layover,balanced --top-k 10 --json
      gflights search --input-json intents.json --browser-mode headed --managed-tab-policy reuse --max-tabs 3 --json
      gflights search --input-json concrete-intents.json --concurrency 3 --json
      gflights search --input-json intents.json --browser-mode headed --json
    """
    del json_output
    if url_only:
        try:
            payload = services.encode_search_urls(input_json)
        except services.ServiceError as error:
            emit_service_error(error)
            return
        emit(payload, services.exit_code_for_payload(payload))
        return
    try:
        exit_code, payload = asyncio.run(
            run_live_search(
                input_json=input_json,
                project_root=project_root,
                browser_mode=browser_mode,
                interact_with_form=live_form,
                batch_concurrency=concurrency,
                managed_tab_policy=managed_tab_policy.replace("-", "_"),
                max_tabs=max_tabs,
                rank_objectives=_split_csv(rank),
                top_k=top_k,
                allow_transit=allow_transit,
                deny_transit=deny_transit,
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
        help="State directory to create with config.json, cache/cache.sqlite, artifacts, and runs.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Create a state root for cache and task-scoped run artifacts.

    Examples:
      gflights project init --path ~/.gflights --json
    """
    del json_output
    emit(services.init_project(path))


@preflight_app.command("google-flights")
def preflight_google_flights_command(
    browser_mode: BrowserMode = typer.Option(
        "headless",
        "--browser-mode",
        help="Browser mode for the public synthetic Google Flights smoke test.",
    ),
    consent_choice: str = typer.Option(
        "reject-all",
        "--consent-choice",
        help="Consent action when Google asks: reject-all, accept-all, or skip.",
    ),
    top_k: int = typer.Option(
        5,
        "--top-k",
        min=1,
        max=5,
        help="Number of synthetic route row ranks to select through booking-summary pages.",
    ),
    selection_concurrency: int = typer.Option(
        3,
        "--selection-concurrency",
        min=1,
        max=7,
        help="Concurrent managed tabs for selecting synthetic route rows through booking.",
    ),
    max_tabs: int = typer.Option(
        8,
        "--max-tabs",
        min=0,
        help="Pass a cdp tab budget and record tab-budget evidence when greater than zero.",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="State root for preflight artifacts; defaults to ~/.gflights.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Verify consent, search, selection, return selection, and booking evidence.

    The route is synthetic and public: JFK to SFO, one adult, future dates.
    The command stops at Google booking-summary pages and never clicks provider
    checkout.
    """
    del json_output
    if consent_choice not in {"reject-all", "accept-all", "skip"}:
        emit(
            {
                "status": "tool_error",
                "warnings": [],
                "error": "--consent-choice must be reject-all, accept-all, or skip",
            },
            6,
        )
        return
    exit_code, payload = asyncio.run(
        run_google_flights_preflight(
            project_root=project_root,
            browser_mode=browser_mode,
            consent_choice=consent_choice,  # type: ignore[arg-type]
            top_k=top_k,
            selection_concurrency=selection_concurrency,
            max_tabs=max_tabs,
        )
    )
    emit(payload, exit_code)


@route_app.command("resolve")
def route_resolve_command(
    input_text: str = typer.Option(
        ...,
        "--input-text",
        help="Airport code, city, or city-like route text to resolve, for example CPH.",
    ),
    browser_mode: BrowserMode = typer.Option(
        "headless",
        "--browser-mode",
        help="Browser mode for live cdp route evidence; use headed only for fallback/debugging.",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="State root for config, cache/cache.sqlite, artifacts, and runs; defaults to ~/.gflights.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return one route choice or explicit ambiguous candidates from evidence.

    This command opens Google Flights through live cdp, fills the route
    autocomplete field, records evidence under the state root, and returns
    visible choices when parser support is available. If no choices are
    parseable, it returns explicit unsupported/deferred output.

    Examples:
      gflights route resolve --input-text CPH --json
      gflights route resolve --input-text CPH --browser-mode headed --json
      gflights route resolve --input-text "Washington DC" --json
    """
    del json_output
    exit_code, payload = asyncio.run(
        run_live_route_resolution(
            input_text=input_text,
            browser_mode=browser_mode,
            project_root=project_root,
        )
    )
    emit(payload, exit_code)


@dates_app.command("scan")
def dates_scan_command(
    input_json: Path = typer.Option(
        ...,
        "--input-json",
        help="Path to one SearchIntent object or a JSON array with date windows.",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="State root with cache/cache.sqlite; defaults to ~/.gflights.",
    ),
    live_probe: bool = typer.Option(
        False,
        "--live-probe",
        help="Opt in to live Google Flights probes for cache misses.",
    ),
    max_probes: int = typer.Option(
        0,
        "--max-probes",
        min=0,
        help="Maximum cache-miss date pairs to live-probe; required with --live-probe.",
    ),
    probe_timeout_seconds: float = typer.Option(
        30.0,
        "--probe-timeout-seconds",
        min=1.0,
        help="Timeout in seconds for each live date-pair probe.",
    ),
    browser_mode: BrowserMode = typer.Option(
        "headless",
        "--browser-mode",
        help="Browser mode for opt-in live date-pair probes.",
    ),
    objective: str = typer.Option(
        "balanced",
        "--objective",
        help="Date-pair ranking objective: cheapest, fastest, least-layover, or balanced.",
    ),
    top_k: int = typer.Option(
        0,
        "--top-k",
        min=0,
        help="Limit ranked date-pair alternatives; 0 keeps all observed pairs.",
    ),
    allow_transit: list[str] | None = typer.Option(
        None,
        "--allow-transit",
        help="Allowed visible transit airport code for local ranking; repeat for multiple.",
    ),
    deny_transit: list[str] | None = typer.Option(
        None,
        "--deny-transit",
        help="Disallowed visible transit airport code for local ranking; repeat for multiple.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Expand date windows, use fresh cache/probes, and rank candidate date pairs.

    Examples:
      gflights dates scan --input-json intents.json --project-root ~/.gflights --json
      gflights dates scan --input-json intents.json --objective balanced --top-k 10 --json
      gflights dates scan --input-json intents.json --live-probe --max-probes 5 --probe-timeout-seconds 30 --json
    """
    del json_output
    if live_probe and max_probes <= 0:
        emit(
            {
                "status": "tool_error",
                "warnings": [],
                "error": "--live-probe requires --max-probes greater than zero",
            },
            6,
        )
        return
    date_pair_probe = (
        LiveDatePairProbe(
            project_root=project_root or Path.home() / ".gflights",
            browser_mode=browser_mode,
            max_probes=max_probes,
            timeout_seconds=probe_timeout_seconds,
        )
        if live_probe
        else None
    )
    try:
        payload = services.scan_dates(
            input_json,
            project_root=project_root,
            date_pair_probe=date_pair_probe,
            objective=objective.replace("-", "_"),
            top_k=top_k,
            allow_transit=allow_transit,
            deny_transit=deny_transit,
        )
        emit(payload, services.exit_code_for_payload(payload))
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
        help="State root for task-scoped run artifacts; defaults to ~/.gflights.",
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


@itinerary_app.command("select")
def itinerary_select_command(
    search_url: str = typer.Option(
        ...,
        "--search-url",
        help="Google Flights search URL whose visible outbound and return rows should be selected.",
    ),
    preferred_carrier: str = typer.Option(
        "",
        "--preferred-carrier",
        help="Optional visible carrier text to prefer when selecting rows.",
    ),
    require_nonstop: bool = typer.Option(
        False,
        "--require-nonstop",
        help="Select only rows whose visible text says Nonstop.",
    ),
    row_rank: int = typer.Option(
        1,
        "--row-rank",
        min=1,
        help="1-based matching row rank to select after preferred-carrier/nonstop filtering.",
    ),
    outbound_row_rank: int | None = typer.Option(
        None,
        "--outbound-row-rank",
        min=1,
        help="1-based outbound row rank; falls back to --row-rank when omitted.",
    ),
    return_row_rank: int | None = typer.Option(
        None,
        "--return-row-rank",
        min=1,
        help="1-based return row rank; falls back to --row-rank when omitted.",
    ),
    outbound_match_text: str = typer.Option(
        "",
        "--outbound-match-text",
        help="Visible text that must appear in the outbound row before selection.",
    ),
    return_match_text: str = typer.Option(
        "",
        "--return-match-text",
        help="Visible text that must appear in the return row before selection.",
    ),
    reuse_target: str = typer.Option(
        "",
        "--reuse-target",
        help="Reuse target id/prefix or google-flights to navigate an existing Google Flights tab.",
    ),
    max_tabs: int = typer.Option(
        0,
        "--max-tabs",
        min=0,
        help="Pass a cdp tab budget and record tab-budget evidence when greater than zero.",
    ),
    allow_over_budget: bool = typer.Option(
        False,
        "--allow-over-budget",
        help="Pass cdp --allow-over-budget for externally bounded fanout runs.",
    ),
    operation_retries: int = typer.Option(
        2,
        "--operation-retries",
        min=0,
        help="Retry the whole outbound/return/booking selection after transient CDP disconnects.",
    ),
    browser_mode: BrowserMode = typer.Option(
        "headless",
        "--browser-mode",
        help="Browser mode for live cdp selection; headed is an explicit fallback.",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="State root for task-scoped run artifacts; defaults to ~/.gflights.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Select visible Google Flights rows and return a Google booking-summary URL.

    The command opens a search URL, waits for visible fare rows, clicks a matching
    outbound row, waits again, clicks a matching return row, and stops at the
    Google Flights booking-summary page. It must not click provider Continue or
    enter checkout.

    Examples:
      gflights itinerary select --search-url URL --preferred-carrier "Preferred Carrier" --require-nonstop --json
      gflights itinerary select --search-url URL --outbound-row-rank 2 --return-row-rank 1 --outbound-match-text "7:40 AM" --return-match-text "1:00 PM" --json
      gflights itinerary select --search-url URL --browser-mode headed --reuse-target google-flights --max-tabs 3 --json
      gflights itinerary select --search-url URL --allow-over-budget --json
    """
    del json_output
    exit_code, payload = asyncio.run(
        run_live_itinerary_selection(
            search_url=search_url,
            browser_mode=browser_mode,
            project_root=project_root,
            preferred_carrier=preferred_carrier,
            require_nonstop=require_nonstop,
            row_rank=row_rank,
            outbound_row_rank=outbound_row_rank,
            return_row_rank=return_row_rank,
            outbound_match_text=outbound_match_text,
            return_match_text=return_match_text,
            reuse_target=reuse_target,
            max_tabs=max_tabs,
            allow_over_budget=allow_over_budget,
            operation_retries=operation_retries,
        )
    )
    emit(payload, exit_code)


@codec_app.command("decode")
def codec_decode_command(
    key: str = typer.Option(
        ...,
        "--key",
        help="Query parameter key, for example tfs or tfu.",
    ),
    value: str = typer.Option(
        ...,
        "--value",
        help="Raw unpadded URL-safe-base64 query value to decode.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Decode a raw query value and report generic wire paths.

    Examples:
      gflights codec decode --key tfu --value EgYIAhAAGAA --json
    """
    del json_output
    payload = services.decode_codec_value(key, value)
    emit(payload, services.exit_code_for_payload(payload))


def main() -> None:
    """Run the CLI shell."""
    app()


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    main()
