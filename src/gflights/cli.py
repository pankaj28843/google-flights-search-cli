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

app = typer.Typer(
    help="Evidence-backed Google Flights search CLI.",
    no_args_is_help=True,
)
intent_app = typer.Typer(help="Parse and normalize agent search intents.")
project_app = typer.Typer(help="Manage user-local CLI state.")
route_app = typer.Typer(help="Resolve city and airport route choices.")
dates_app = typer.Typer(help="Scan date windows with offline fixtures or live adapters.")
itinerary_app = typer.Typer(help="Inspect selected itinerary evidence.")
evidence_app = typer.Typer(help="Capture or replay evidence artifacts.")
codec_app = typer.Typer(help="Decode encoded query/protobuf-like evidence.")

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
    model: str = typer.Option(..., "--model"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    del json_output
    try:
        emit(services.json_schema_for(model))
    except services.ServiceError as error:
        emit_service_error(error)


@app.command("doctor")
def doctor_command(json_output: bool = typer.Option(False, "--json")) -> None:
    del json_output
    emit(services.doctor_report())


@app.command("search")
def search_command(
    input_json: Path = typer.Option(..., "--input-json"),
    offline_fixtures: Path | None = typer.Option(None, "--offline-fixtures"),
    live_cdp: bool = typer.Option(False, "--live-cdp"),
    live_form: bool = typer.Option(False, "--live-form"),
    browser_mode: BrowserMode = typer.Option("headless", "--browser-mode"),
    project_root: Path | None = typer.Option(None, "--project-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
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
    input_json: Path = typer.Option(..., "--input-json"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    del json_output
    try:
        emit(services.parse_intents(input_json))
    except services.ServiceError as error:
        emit_service_error(error)


@project_app.command("init")
def project_init_command(
    path: Path = typer.Option(..., "--path"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    del json_output
    emit(services.init_project(path))


@route_app.command("resolve")
def route_resolve_command(
    input_text: str = typer.Option(..., "--input-text"),
    offline_fixtures: Path | None = typer.Option(None, "--offline-fixtures"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
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
    input_json: Path = typer.Option(..., "--input-json"),
    offline_fixtures: Path | None = typer.Option(None, "--offline-fixtures"),
    project_root: Path | None = typer.Option(None, "--project-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
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
    booking_url: str = typer.Option(..., "--booking-url"),
    browser_mode: BrowserMode = typer.Option("headless", "--browser-mode"),
    project_root: Path | None = typer.Option(None, "--project-root"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
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
    fixture: Path = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    del json_output
    exit_code, payload = services.replay_fixture(fixture)
    emit(payload, exit_code)


@codec_app.command("decode")
def codec_decode_command(
    fixture: Path = typer.Option(..., "--fixture"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    del json_output
    emit(services.decode_codec_fixture(fixture))


def main() -> None:
    """Run the CLI shell."""
    app()


if __name__ == "__main__":
    main()
