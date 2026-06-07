"""Usefulness gate for the CPH-Lucknow India trip request."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from time import perf_counter
from typing import Any

from gflights.app_state import init_app_state
from gflights.browser import BLOCKED_STOP_STATES, BrowserMode
from gflights.domain import SearchIntent
from gflights.ranking import rank_observed_pairs

DEFAULT_REPORT_ROOT = (
    Path.home() / "Personal" / "Code" / "paternity-leave-research" / "india-trip-plan"
)
WINDOW_INTENT_FILENAME = "cph-lko-window-intent.json"
CONCRETE_INTENTS_FILENAME = "cph-lko-100-concrete-intents.json"
PREFERRED_AIRLINE = "Air India"


def run_india_trip_workflow(
    *,
    report_root: Path = DEFAULT_REPORT_ROOT,
    project_root: Path | None = None,
    browser_mode: BrowserMode = "headless",
    execute_live: bool = False,
    date_scan_max_probes: int = 1,
    date_scan_probe_timeout_seconds: float = 45.0,
    cdp_executable: str = "cdp",
    python_executable: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Write canonical inputs, optionally run live commands, and summarize usefulness."""

    paths = _ensure_report_dirs(report_root)
    state_root = (project_root or paths["state"]).expanduser().resolve()
    init_app_state(state_root)
    input_artifacts = write_india_trip_inputs(paths["root"])
    preflight_blocked_reason = ""

    if execute_live:
        _capture_cdp_state(
            cdp_executable=cdp_executable,
            browser_mode=browser_mode,
            label="preflight",
            output_dir=paths["cdp"],
        )
        preflight_pages = _load_json(paths["cdp"] / "preflight-pages.json")
        preflight_blocked_reason = _browser_budget_block_reason(preflight_pages)
        if not preflight_blocked_reason:
            _run_live_commands(
                output_dir=paths["outputs"],
                state_root=state_root,
                input_artifacts=input_artifacts,
                browser_mode=browser_mode,
                date_scan_max_probes=max(date_scan_max_probes, 0),
                date_scan_probe_timeout_seconds=date_scan_probe_timeout_seconds,
                python_executable=python_executable or sys.executable,
            )
            _capture_cdp_state(
                cdp_executable=cdp_executable,
                browser_mode=browser_mode,
                label="postrun",
                output_dir=paths["cdp"],
            )

    live_outputs_present = execute_live or _saved_live_outputs_present(paths["outputs"])
    summary = summarize_india_trip_report(
        paths["root"],
        project_root=state_root,
        browser_mode=browser_mode,
        live_attempted=live_outputs_present,
        preflight_blocked_reason=preflight_blocked_reason,
    )
    summary_path = paths["analysis"] / "summary.json"
    report_path = paths["root"] / "e2e-report.md"
    _write_json(summary_path, summary)
    report_path.write_text(render_markdown_report(summary) + "\n")

    payload = {
        "status": _status_for_verdict(summary["verdict"]),
        "verdict": summary["verdict"],
        "useful": summary["verdict"] == "useful",
        "reason": summary["reason"],
        "report_root": str(paths["root"]),
        "live_attempted": live_outputs_present,
        "artifacts": {
            **input_artifacts,
            "summary_json": str(summary_path),
            "markdown_report": str(report_path),
        },
        "counts": summary["counts"],
        "airline_preference": summary["airline_preference"],
        "next_actions": summary["next_actions"],
    }
    return _exit_code_for_verdict(summary["verdict"]), payload


def write_india_trip_inputs(report_root: Path) -> dict[str, Any]:
    """Write the canonical window intent and 100 concrete date-pair intents."""

    root = report_root.expanduser().resolve()
    inputs_dir = root / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    window_intent = build_window_intent()
    concrete_intents = build_concrete_intents(window_intent)
    window_path = inputs_dir / WINDOW_INTENT_FILENAME
    concrete_path = inputs_dir / CONCRETE_INTENTS_FILENAME
    _write_json(window_path, window_intent)
    _write_json(concrete_path, concrete_intents)
    return {
        "window_intent": str(window_path),
        "concrete_intents": str(concrete_path),
        "concrete_pair_count": len(concrete_intents),
    }


def build_window_intent() -> dict[str, Any]:
    """Return the exact agent-facing SearchIntent for the user's India trip window."""

    return {
        "query_id": "cph-lko-family-airindia-window",
        "origin": {"text": "CPH", "kind": "airport_code"},
        "destination": {
            "text": "Lucknow",
            "kind": "city_or_airport",
            "selected": {
                "text": "Lucknow, Uttar Pradesh, India",
                "kind": "city",
                "display_name": "Lucknow, Uttar Pradesh, India",
                "code_or_id": "/m/022tq4",
                "confidence": "strong",
                "evidence": {
                    "source_surfaces": [
                        "lucknow-autocomplete-visible-text",
                        "protobuf-decode-report",
                    ],
                    "artifacts": [
                        "route_autocomplete_choices_fixture.json",
                        "decode-report.md",
                    ],
                },
            },
        },
        "trip_type": "round_trip",
        "departure_window": {"start": "2026-11-21", "end": "2026-11-30"},
        "return_window": {"start": "2027-01-01", "end": "2027-01-10"},
        "passengers": {
            "adults": 2,
            "children": 0,
            "infants_in_seat": 0,
            "infants_on_lap": 1,
        },
        "traveler_profiles": [{"kind": "infant_9_months", "comfort_weight": "high"}],
        "cabin": "economy",
        "airline_preferences": [{"airline": PREFERRED_AIRLINE, "mode": "preferred"}],
        "consider_all_airlines": True,
        "currency": "EUR",
        "language": "en",
        "location": None,
        "sort": "top_flights",
    }


def build_concrete_intents(window_intent: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand the canonical 10-by-10 date windows into concrete SearchIntent objects."""

    concrete: list[dict[str, Any]] = []
    for departure_date in _date_values(
        window_intent["departure_window"]["start"],
        window_intent["departure_window"]["end"],
    ):
        for return_date in _date_values(
            window_intent["return_window"]["start"],
            window_intent["return_window"]["end"],
        ):
            item = json.loads(json.dumps(window_intent))
            item["query_id"] = f"cph-lko-family-airindia-{departure_date}-{return_date}"
            item["departure_window"] = {"start": departure_date, "end": departure_date}
            item["return_window"] = {"start": return_date, "end": return_date}
            concrete.append(item)
    return concrete


def summarize_india_trip_report(
    report_root: Path,
    *,
    project_root: Path | None = None,
    browser_mode: BrowserMode = "headless",
    live_attempted: bool = False,
    preflight_blocked_reason: str = "",
) -> dict[str, Any]:
    """Reduce saved command outputs to a truthful trip-planning verdict."""

    root = report_root.expanduser().resolve()
    state_root = (project_root or root / "state").expanduser().resolve()
    window_intent = _load_json(root / "inputs" / WINDOW_INTENT_FILENAME)
    concrete_intents = _load_json(root / "inputs" / CONCRETE_INTENTS_FILENAME)
    live_search_output = _load_json(root / "outputs" / "live-search-100.json")
    date_scan_output = _load_json(root / "outputs" / "date-scan-window.json")
    route_outputs = {
        "cph": _command_summary(root / "outputs", "live-route-cph"),
        "lucknow": _command_summary(root / "outputs", "live-route-lucknow"),
    }

    live_search = _live_search_summary(live_search_output, root / "outputs")
    date_scan = _date_scan_summary(date_scan_output, root / "outputs")
    ranked_pairs = _ranked_pairs(date_scan_output)
    ranking_source = "date_scan" if ranked_pairs else "none"
    if not ranked_pairs:
        ranked_pairs = _ranked_pairs_from_live_search(
            window_intent,
            concrete_intents,
            live_search_output,
        )
        if ranked_pairs:
            ranking_source = "live_search_results"
    ranked_pairs_with_evidence = [pair for pair in ranked_pairs if _ranked_pair_has_evidence(pair)]
    browser_budget = {
        "preflight": _browser_pages_summary(root / "cdp" / "preflight-pages.json"),
        "postrun": _browser_pages_summary(root / "cdp" / "postrun-pages.json"),
    }
    artifact_counts = _artifact_counts(state_root)
    airline_preference = _airline_preference_summary(
        window_intent,
        live_search_output,
        ranked_pairs,
    )

    counts = {
        "concrete_search_intents": len(concrete_intents)
        if isinstance(concrete_intents, list)
        else 0,
        "generated_date_pairs": date_scan["generated_pairs"],
        "parsed_result_rows": live_search["total_result_rows"],
        "date_pairs_with_rows": live_search["pairs_with_rows"],
        "sqlite_price_rows": artifact_counts["sqlite_price_rows"],
        "ranked_pairs": len(ranked_pairs),
        "ranked_pairs_with_evidence": len(ranked_pairs_with_evidence),
        "air_india_result_rows": airline_preference["result_rows_matching_preference"],
        "air_india_ranked_pairs": airline_preference["ranked_pairs_matching_preference"],
        "managed_tab_close_files": artifact_counts["managed_tab_close_files"],
        "managed_tab_close_ok": artifact_counts["managed_tab_close_ok"],
    }
    verdict, reason, next_actions = _verdict(
        preflight_blocked_reason=preflight_blocked_reason,
        live_attempted=live_attempted,
        route_outputs=route_outputs,
        live_search=live_search,
        date_scan=date_scan,
        counts=counts,
        ranked_pairs_with_evidence=ranked_pairs_with_evidence,
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "request": {
            "origin": "CPH",
            "destination": "Lucknow",
            "departure_window": "2026-11-21..2026-11-30",
            "return_window": "2027-01-01..2027-01-10",
            "passengers": {
                "adults": 2,
                "children": 0,
                "infants_in_seat": 0,
                "infants_on_lap": 1,
            },
            "airline_preference": f"{PREFERRED_AIRLINE} preferred; all airlines considered",
        },
        "status": _status_for_verdict(verdict),
        "verdict": verdict,
        "reason": reason,
        "report_root": str(root),
        "state_root": str(state_root),
        "browser_mode": browser_mode,
        "live_attempted": live_attempted,
        "inputs": {
            "window_intent": str(root / "inputs" / WINDOW_INTENT_FILENAME),
            "concrete_intents": str(root / "inputs" / CONCRETE_INTENTS_FILENAME),
        },
        "route_resolution": route_outputs,
        "live_search": live_search,
        "date_scan": date_scan,
        "browser_budget": browser_budget,
        "artifact_counts": artifact_counts,
        "counts": counts,
        "ranking_source": ranking_source,
        "airline_preference": airline_preference,
        "ranked_options": ranked_pairs[:10] if verdict == "useful" else [],
        "next_actions": next_actions,
    }


def render_markdown_report(summary: dict[str, Any]) -> str:
    """Render a readable trip-plan report from a summary JSON object."""

    request = summary["request"]
    counts = summary["counts"]
    lines = [
        "# India Trip Plan E2E Report",
        "",
        f"Generated: `{summary['generated_at']}`",
        "",
        "## Request",
        "",
        f"- Route: `{request['origin']}` to `{request['destination']}` round trip.",
        f"- Departure window: `{request['departure_window']}`.",
        f"- Return window: `{request['return_window']}`.",
        "- Passengers: 2 adults and one 9-month infant (`infants_on_lap = 1`).",
        f"- Airline preference: {request['airline_preference']}.",
        "",
        "## Verdict",
        "",
        f"`{summary['verdict']}`: {summary['reason']}",
        "",
        "## Summary",
        "",
        "| Check | Result |",
        "|---|---:|",
        f"| Concrete search intents | {counts['concrete_search_intents']} |",
        f"| Generated date pairs | {counts['generated_date_pairs']} |",
        f"| Parsed result rows | {counts['parsed_result_rows']} |",
        f"| Date pairs with rows | {counts['date_pairs_with_rows']} |",
        f"| SQLite price rows | {counts['sqlite_price_rows']} |",
        f"| Ranked pairs | {counts['ranked_pairs']} |",
        f"| Ranked pairs with evidence | {counts['ranked_pairs_with_evidence']} |",
        f"| Air India result rows | {counts['air_india_result_rows']} |",
        f"| Air India ranked pairs | {counts['air_india_ranked_pairs']} |",
        f"| Ranking source | {summary.get('ranking_source') or 'none'} |",
        f"| Managed tab close artifacts | {counts['managed_tab_close_files']} |",
        f"| Managed tab closes ok | {counts['managed_tab_close_ok']} |",
        "",
        "## Airline Preference",
        "",
        "- Preferred airlines: "
        f"{', '.join(summary['airline_preference']['preferred_airlines']) or 'none'}.",
        "- Google Flights airline filter applied: "
        f"`{summary['airline_preference']['google_flights_filters_applied']}`.",
        "- Local ranking preference applied: "
        f"`{summary['airline_preference']['local_ranking_preference_applied']}`.",
        "- Preference observation: "
        f"`{summary['airline_preference']['preference_observation_status']}`.",
        "- Result rows matching preference: "
        f"{summary['airline_preference']['result_rows_matching_preference']} of "
        f"{summary['airline_preference']['result_rows_with_visible_carriers']} visible-carrier rows.",
        "- Ranked pairs matching preference: "
        f"{summary['airline_preference']['ranked_pairs_matching_preference']} of "
        f"{summary['airline_preference']['ranked_pairs_with_visible_carriers']} visible-carrier ranked pairs.",
        f"- Preference note: {summary['airline_preference']['message']}",
    ]
    if summary["ranked_options"]:
        lines.extend(["", "## Ranked Options", ""])
        for index, pair in enumerate(summary["ranked_options"], start=1):
            price = pair.get("best_observed_price", {})
            summary_row = pair.get("top_result_summary", {})
            preference_line = _ranked_pair_preference_line(pair)
            lines.extend(
                [
                    f"{index}. `{pair.get('departure_date')}` to `{pair.get('return_date')}`: "
                    f"{price.get('text') or price.get('amount')}",
                    f"   Carriers: {', '.join(summary_row.get('carriers') or []) or 'unknown'}",
                    f"   Preference: {preference_line}",
                    f"   Evidence: {', '.join((pair.get('evidence') or {}).get('artifacts') or [])}",
                    "",
                ]
            )
    else:
        lines.extend(["", "## Why This Is Not A Trip Plan Yet", ""])
        for action in summary["next_actions"]:
            lines.append(f"- {action}")
    return "\n".join(lines)


def _ranked_pair_preference_line(pair: dict[str, Any]) -> str:
    explanation = pair.get("scoring_explanation")
    preference = (
        explanation.get("airline_preference")
        if isinstance(explanation, dict) and isinstance(explanation.get("airline_preference"), dict)
        else None
    )
    if isinstance(preference, dict):
        preferred = ", ".join(_carrier_names(preference.get("preferred_airlines"))) or "none"
        matched = ", ".join(_carrier_names(preference.get("matched_carriers"))) or "none"
        return (
            f"{preference.get('status') or 'unknown'}; preferred {preferred}; "
            f"matched carriers {matched}; Google filter applied "
            f"{bool(preference.get('google_flights_filters_applied'))}"
        )

    component = _preference_component(pair)
    if component is None:
        return "not reported"
    preferred = ", ".join(_carrier_names(component.get("preferred"))) or "none"
    return (
        f"{component.get('value') or 'unknown'}; preferred {preferred}; Google filter applied false"
    )


def _preference_component(pair: dict[str, Any]) -> dict[str, Any] | None:
    explanation = pair.get("scoring_explanation")
    components = explanation.get("components") if isinstance(explanation, dict) else None
    if not isinstance(components, list):
        return None
    for component in components:
        if isinstance(component, dict) and component.get("name") == "preferred_airline":
            return component
    return None


def _ensure_report_dirs(report_root: Path) -> dict[str, Path]:
    root = report_root.expanduser().resolve()
    paths = {
        "root": root,
        "inputs": root / "inputs",
        "outputs": root / "outputs",
        "cdp": root / "cdp",
        "state": root / "state",
        "analysis": root / "analysis",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _saved_live_outputs_present(output_dir: Path) -> bool:
    return any(
        (output_dir / name).is_file()
        for name in (
            "live-search-100.json",
            "date-scan-window.json",
            "live-route-cph.json",
            "live-route-lucknow.json",
        )
    )


def _run_live_commands(
    *,
    output_dir: Path,
    state_root: Path,
    input_artifacts: dict[str, Any],
    browser_mode: BrowserMode,
    date_scan_max_probes: int,
    date_scan_probe_timeout_seconds: float,
    python_executable: str,
) -> None:
    date_scan_args = [
        "dates",
        "scan",
        "--input-json",
        str(input_artifacts["window_intent"]),
        "--project-root",
        str(state_root),
        "--json",
    ]
    if date_scan_max_probes > 0:
        date_scan_args.extend(
            [
                "--live-probe",
                "--max-probes",
                str(date_scan_max_probes),
                "--probe-timeout-seconds",
                str(date_scan_probe_timeout_seconds),
            ]
        )
    commands = [
        (
            "intent-parse-100",
            [
                "intent",
                "parse",
                "--input-json",
                str(input_artifacts["concrete_intents"]),
                "--json",
            ],
        ),
        (
            "intent-parse-window",
            [
                "intent",
                "parse",
                "--input-json",
                str(input_artifacts["window_intent"]),
                "--json",
            ],
        ),
        (
            "live-route-cph",
            [
                "route",
                "resolve",
                "--input-text",
                "CPH",
                "--project-root",
                str(state_root),
                "--browser-mode",
                browser_mode,
                "--json",
            ],
        ),
        (
            "live-route-lucknow",
            [
                "route",
                "resolve",
                "--input-text",
                "Lucknow",
                "--project-root",
                str(state_root),
                "--browser-mode",
                browser_mode,
                "--json",
            ],
        ),
        (
            "live-search-100",
            [
                "search",
                "--input-json",
                str(input_artifacts["concrete_intents"]),
                "--project-root",
                str(state_root),
                "--browser-mode",
                browser_mode,
                "--json",
            ],
        ),
        (
            "date-scan-window",
            date_scan_args,
        ),
    ]
    for label, args in commands:
        _run_command(
            [python_executable, "-m", "gflights.cli", *args],
            output_dir=output_dir,
            label=label,
        )


def _capture_cdp_state(
    *,
    cdp_executable: str,
    browser_mode: BrowserMode,
    label: str,
    output_dir: Path,
) -> None:
    _run_command(
        [cdp_executable, "--browser-mode", browser_mode, "daemon", "health", "--json"],
        output_dir=output_dir,
        label=f"{label}-health",
    )
    _run_command(
        [cdp_executable, "--browser-mode", browser_mode, "pages", "--json"],
        output_dir=output_dir,
        label=f"{label}-pages",
    )


def _run_command(command: list[str], *, output_dir: Path, label: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=None,
            check=False,
        )
    except OSError as exc:
        elapsed = perf_counter() - started
        (output_dir / f"{label}.json").write_text("")
        (output_dir / f"{label}.stderr.txt").write_text(str(exc))
        (output_dir / f"{label}.exit.txt").write_text("6\n")
        (output_dir / f"{label}.time.txt").write_text(f"real {elapsed:.2f}\n")
        return
    elapsed = perf_counter() - started
    (output_dir / f"{label}.json").write_text(completed.stdout)
    (output_dir / f"{label}.stderr.txt").write_text(completed.stderr)
    (output_dir / f"{label}.exit.txt").write_text(f"{completed.returncode}\n")
    (output_dir / f"{label}.time.txt").write_text(f"real {elapsed:.2f}\n")


def _live_search_summary(payload: Any, output_dir: Path) -> dict[str, Any]:
    items = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
    status_counts: Counter[str] = Counter()
    query_population_counts: Counter[str] = Counter()
    unsupported_counts: Counter[str] = Counter()
    result_rows = 0
    pairs_with_rows = 0
    price_observations_written = 0
    blocked_items = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown")
        status_counts[status] += 1
        if _is_blocked_payload(item):
            blocked_items += 1
        query_population = item.get("query_population")
        if isinstance(query_population, dict):
            query_population_counts[str(query_population.get("status") or "unknown")] += 1
        for unsupported in item.get("unsupported") or []:
            if isinstance(unsupported, dict):
                unsupported_counts[str(unsupported.get("field") or "unknown")] += 1
        results = item.get("results")
        if isinstance(results, list):
            result_rows += len(results)
            if results:
                pairs_with_rows += 1
        cache = item.get("cache")
        if isinstance(cache, dict):
            price_observations_written += int(cache.get("price_observations_written") or 0)

    return {
        "exit_code": _read_exit_code(output_dir / "live-search-100.exit.txt"),
        "time": _read_lines(output_dir / "live-search-100.time.txt"),
        "items": len(items),
        "status_counts": dict(status_counts),
        "query_population_counts": dict(query_population_counts),
        "unsupported_counts": dict(unsupported_counts),
        "total_result_rows": result_rows,
        "pairs_with_rows": pairs_with_rows,
        "price_observations_written": price_observations_written,
        "blocked_items": blocked_items,
        "output_path": str(output_dir / "live-search-100.json"),
    }


def _date_scan_summary(payload: Any, output_dir: Path) -> dict[str, Any]:
    item = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(item, dict):
        return {
            "exit_code": _read_exit_code(output_dir / "date-scan-window.exit.txt"),
            "time": _read_lines(output_dir / "date-scan-window.time.txt"),
            "status": "missing",
            "generated_pairs": 0,
            "probed_pairs": 0,
            "coverage_counts": {},
            "ranked_pairs": 0,
            "warnings_count": 0,
            "blocked": False,
            "output_path": str(output_dir / "date-scan-window.json"),
        }
    ranked_pairs = item.get("ranked_pairs")
    warnings = item.get("warnings")
    return {
        "exit_code": _read_exit_code(output_dir / "date-scan-window.exit.txt"),
        "time": _read_lines(output_dir / "date-scan-window.time.txt"),
        "status": str(item.get("status") or "unknown"),
        "generated_pairs": int(item.get("generated_pairs") or 0),
        "probed_pairs": int(item.get("probed_pairs") or 0),
        "coverage_counts": item.get("coverage_counts") or {},
        "ranked_pairs": len(ranked_pairs) if isinstance(ranked_pairs, list) else 0,
        "warnings_count": len(warnings) if isinstance(warnings, list) else 0,
        "blocked": _is_blocked_payload(item),
        "output_path": str(output_dir / "date-scan-window.json"),
    }


def _command_summary(output_dir: Path, label: str) -> dict[str, Any]:
    payload = _load_json(output_dir / f"{label}.json")
    if not isinstance(payload, dict):
        return {
            "exit_code": _read_exit_code(output_dir / f"{label}.exit.txt"),
            "status": "missing",
            "error": "",
            "source_surfaces": [],
            "choices": 0,
            "blocked": False,
            "output_path": str(output_dir / f"{label}.json"),
        }
    evidence = payload.get("evidence")
    source_surfaces = evidence.get("source_surfaces") if isinstance(evidence, dict) else []
    choices = payload.get("choices")
    return {
        "exit_code": _read_exit_code(output_dir / f"{label}.exit.txt"),
        "status": str(payload.get("status") or "unknown"),
        "error": str(payload.get("error") or ""),
        "stop_state": payload.get("stop_state"),
        "source_surfaces": source_surfaces if isinstance(source_surfaces, list) else [],
        "choices": len(choices) if isinstance(choices, list) else 0,
        "blocked": _is_blocked_payload(payload),
        "output_path": str(output_dir / f"{label}.json"),
    }


def _airline_preference_summary(
    intent: Any,
    live_search_payload: Any,
    ranked_pairs: list[dict[str, Any]],
) -> dict[str, Any]:
    preferences = []
    if isinstance(intent, dict):
        preferences = [
            str(item.get("airline"))
            for item in intent.get("airline_preferences") or []
            if isinstance(item, dict) and item.get("mode") == "preferred" and item.get("airline")
        ]
    result_rows_matching = 0
    result_rows_with_visible_carriers = 0
    for result in _result_rows(live_search_payload):
        carriers = _carrier_names(result.get("carriers"))
        if carriers:
            result_rows_with_visible_carriers += 1
        if _matches_preference(preferences, carriers):
            result_rows_matching += 1

    ranked_pairs_matching = 0
    ranked_pairs_with_visible_carriers = 0
    filters_applied_values = []
    for pair in ranked_pairs:
        pair_summary = pair.get("top_result_summary")
        carriers = _carrier_names(
            pair_summary.get("carriers") if isinstance(pair_summary, dict) else None
        )
        if carriers:
            ranked_pairs_with_visible_carriers += 1
        if _matches_preference(preferences, carriers):
            ranked_pairs_matching += 1
        explanation = pair.get("scoring_explanation")
        if isinstance(explanation, dict) and "google_flights_filters_applied" in explanation:
            filters_applied_values.append(bool(explanation["google_flights_filters_applied"]))

    preference_status = _preference_observation_status(
        preferences=preferences,
        matched_rows=result_rows_matching,
        matched_ranked_pairs=ranked_pairs_matching,
        visible_rows=result_rows_with_visible_carriers,
        visible_ranked_pairs=ranked_pairs_with_visible_carriers,
    )
    return {
        "preferred_airlines": preferences,
        "google_flights_filters_applied": any(filters_applied_values),
        "local_ranking_preference_applied": bool(preferences),
        "result_rows_matching_preference": result_rows_matching,
        "result_rows_with_visible_carriers": result_rows_with_visible_carriers,
        "ranked_pairs_matching_preference": ranked_pairs_matching,
        "ranked_pairs_with_visible_carriers": ranked_pairs_with_visible_carriers,
        "ranked_pairs_reporting_filter_policy": len(filters_applied_values),
        "preference_observation_status": preference_status,
        "message": _preference_message(
            preferences=preferences,
            status=preference_status,
            google_filters_applied=any(filters_applied_values),
        ),
    }


def _carrier_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(carrier) for carrier in value if str(carrier)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _matches_preference(preferences: list[str], carriers: list[str]) -> bool:
    preferred_names = {preference.casefold() for preference in preferences}
    return any(carrier.casefold() in preferred_names for carrier in carriers)


def _preference_observation_status(
    *,
    preferences: list[str],
    matched_rows: int,
    matched_ranked_pairs: int,
    visible_rows: int,
    visible_ranked_pairs: int,
) -> str:
    if not preferences:
        return "not_requested"
    if matched_rows or matched_ranked_pairs:
        return "matched"
    if visible_rows or visible_ranked_pairs:
        return "not_matched"
    return "not_observable"


def _preference_message(
    *,
    preferences: list[str],
    status: str,
    google_filters_applied: bool,
) -> str:
    preferred = ", ".join(preferences) or "no airline"
    filter_text = (
        "Google Flights airline filters were applied."
        if google_filters_applied
        else "Google Flights airline filters were not applied; ranking preference was local only."
    )
    if status == "matched":
        return f"{preferred} matched at least one visible row or ranked option. {filter_text}"
    if status == "not_matched":
        return f"No visible row or ranked option matched {preferred}. {filter_text}"
    if status == "not_observable":
        return f"No visible carrier data was available to evaluate {preferred}. {filter_text}"
    return "No preferred airline was requested."


def _verdict(
    *,
    preflight_blocked_reason: str,
    live_attempted: bool,
    route_outputs: dict[str, dict[str, Any]],
    live_search: dict[str, Any],
    date_scan: dict[str, Any],
    counts: dict[str, int],
    ranked_pairs_with_evidence: list[dict[str, Any]],
) -> tuple[str, str, list[str]]:
    if preflight_blocked_reason:
        return (
            "blocked",
            preflight_blocked_reason,
            [
                "Reduce stale cdp tabs or switch browser mode only when a blocked result recommends it."
            ],
        )
    if any(output["blocked"] for output in route_outputs.values()) or live_search["blocked_items"]:
        return (
            "blocked",
            "Google Flights or the browser stopped at a safety boundary.",
            ["Inspect route/search stop_state fields and browser evidence before retrying."],
        )
    if date_scan["blocked"]:
        return (
            "blocked",
            "Date scanning stopped at a browser or safety boundary.",
            ["Inspect date scan output and cdp evidence before retrying."],
        )
    if ranked_pairs_with_evidence:
        return (
            "useful",
            "Ranked flight options with prices and evidence are available.",
            ["Inspect ranked_options and the saved Markdown report before booking manually."],
        )

    outputs_present = live_search["items"] > 0 or date_scan["status"] != "missing"
    if outputs_present:
        missing = []
        if sum(output["choices"] for output in route_outputs.values()) == 0:
            missing.append("no route choices from live route resolution")
        if counts["parsed_result_rows"] == 0:
            missing.append("no parsed live result rows")
        if counts["sqlite_price_rows"] == 0:
            missing.append("no SQLite price observations")
        if counts["ranked_pairs"] == 0:
            missing.append("no ranked date pairs")
        reason = "The CLI did not produce a usable India trip plan: " + ", ".join(missing) + "."
        return (
            "not_useful",
            reason,
            [
                "Improve live result extraction or provide fresh cache rows with prices.",
                "Then rerun gflights trip india --execute-live --json.",
            ],
        )
    if live_attempted:
        return (
            "inconclusive",
            "The live gate was requested but no reducible command outputs were saved.",
            ["Inspect command stderr files and repair the workflow harness before retrying."],
        )
    return (
        "inconclusive",
        "Inputs were written, but no live outputs were available to judge trip usefulness.",
        ["Run with --execute-live when ready to open Google Flights through cdp."],
    )


def _status_for_verdict(verdict: str) -> str:
    return {
        "useful": "ok",
        "blocked": "blocked",
        "not_useful": "unsupported",
        "inconclusive": "tool_error",
    }[verdict]


def _exit_code_for_verdict(verdict: str) -> int:
    return {
        "useful": 0,
        "blocked": 4,
        "not_useful": 3,
        "inconclusive": 6,
    }[verdict]


def _ranked_pairs(payload: Any) -> list[dict[str, Any]]:
    item = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(item, dict):
        return []
    pairs = item.get("ranked_pairs")
    return [pair for pair in pairs if isinstance(pair, dict)] if isinstance(pairs, list) else []


def _ranked_pairs_from_live_search(
    intent_payload: Any,
    concrete_intents_payload: Any,
    live_search_payload: Any,
) -> list[dict[str, Any]]:
    if not isinstance(intent_payload, dict):
        return []
    try:
        intent = SearchIntent.model_validate(intent_payload)
    except ValueError:
        return []

    dates_by_query = _concrete_dates_by_query(concrete_intents_payload)
    pairs: list[dict[str, Any]] = []
    for item in _live_search_items(live_search_payload):
        query_id = str(item.get("query_id") or "")
        dates = dates_by_query.get(query_id)
        if dates is None:
            continue
        priced_results = _priced_live_results(item.get("results"))
        if not priced_results:
            continue
        best_result = priced_results[0]
        pairs.append(
            {
                "departure_date": dates["departure_date"],
                "return_date": dates["return_date"],
                "best_observed_price": _price_object_from_result(
                    best_result,
                    fallback_currency=intent.currency,
                ),
                "result_count": len(priced_results),
                "top_result_summary": _top_result_summary(best_result),
                "source": "live_search_results",
                "evidence": _live_result_evidence(item, best_result),
            }
        )
    return rank_observed_pairs(intent, pairs)


def _concrete_dates_by_query(payload: Any) -> dict[str, dict[str, str | None]]:
    items = payload if isinstance(payload, list) else []
    dates_by_query: dict[str, dict[str, str | None]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        query_id = str(item.get("query_id") or "")
        departure_window = item.get("departure_window")
        return_window = item.get("return_window")
        if not query_id or not isinstance(departure_window, dict):
            continue
        departure_date = departure_window.get("start")
        if not isinstance(departure_date, str):
            continue
        return_date = return_window.get("start") if isinstance(return_window, dict) else None
        dates_by_query[query_id] = {
            "departure_date": departure_date,
            "return_date": str(return_date) if return_date else None,
        }
    return dates_by_query


def _live_search_items(payload: Any) -> list[dict[str, Any]]:
    items = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _priced_live_results(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    priced = [
        result
        for result in payload
        if isinstance(result, dict)
        and isinstance(result.get("price"), dict)
        and isinstance(result["price"].get("amount"), int | float)
    ]
    return sorted(
        priced,
        key=lambda result: (
            result["price"]["amount"],
            int(result.get("duration_minutes") or 999999),
            _live_result_stop_count(result),
        ),
    )


def _live_result_stop_count(result: dict[str, Any]) -> int:
    stops = result.get("stops")
    if isinstance(stops, dict) and isinstance(stops.get("count"), int | float):
        return int(stops["count"])
    return 999999


def _price_object_from_result(
    result: dict[str, Any],
    *,
    fallback_currency: str,
) -> dict[str, Any]:
    price = result.get("price")
    price_dict = dict(price) if isinstance(price, dict) else {}
    currency = str(price_dict.get("currency") or result.get("currency") or fallback_currency)
    amount = price_dict.get("amount")
    text = price_dict.get("text") or (f"{currency} {amount}" if amount is not None else None)
    return {
        "amount": amount,
        "currency": currency,
        "text": text,
    }


def _top_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "result_id": result.get("result_id"),
        "carriers": result.get("carriers", []),
        "departure_times": result.get("departure_times", []),
        "arrival_times": result.get("arrival_times", []),
        "duration_text": result.get("duration_text"),
        "duration_minutes": result.get("duration_minutes"),
        "stops": result.get("stops"),
        "layovers": result.get("layovers", []),
        "emissions": result.get("emissions"),
    }


def _live_result_evidence(item: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    item_evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    result_evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    return {
        "run_id": str(item_evidence.get("run_id") or "live-search-result"),
        "source_surfaces": _dedupe_strings(
            [
                *_string_list(result_evidence.get("source_surfaces")),
                *_string_list(item_evidence.get("source_surfaces")),
            ]
        ),
        "artifacts": _dedupe_strings(
            [
                *_string_list(result_evidence.get("artifacts")),
                *_string_list(item_evidence.get("artifacts")),
            ]
        ),
    }


def _ranked_pair_has_evidence(pair: dict[str, Any]) -> bool:
    price = pair.get("best_observed_price")
    evidence = pair.get("evidence")
    if not isinstance(price, dict) or price.get("amount") is None:
        return False
    if not isinstance(evidence, dict):
        return False
    surfaces = evidence.get("source_surfaces")
    artifacts = evidence.get("artifacts")
    return bool(surfaces) and bool(artifacts)


def _result_rows(payload: Any) -> list[dict[str, Any]]:
    items = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        results = item.get("results")
        if isinstance(results, list):
            rows.extend(result for result in results if isinstance(result, dict))
    return rows


def _artifact_counts(state_root: Path) -> dict[str, int]:
    run_root = state_root / "runs"
    run_dirs = [path for path in run_root.iterdir() if path.is_dir()] if run_root.is_dir() else []
    close_files = list(run_root.glob("*/managed-tab-close.json")) if run_root.is_dir() else []
    close_ok = 0
    for path in close_files:
        payload = _load_json(path)
        if isinstance(payload, dict) and (
            payload.get("status") == "ok"
            or (isinstance(payload.get("json_payload"), dict) and payload["json_payload"].get("ok"))
        ):
            close_ok += 1
    return {
        "run_dirs": len(run_dirs),
        "managed_tab_close_files": len(close_files),
        "managed_tab_close_ok": close_ok,
        "sqlite_price_rows": _sqlite_price_rows(state_root / "cache.sqlite"),
    }


def _sqlite_price_rows(database_path: Path) -> int:
    if not database_path.is_file():
        return 0
    try:
        with sqlite3.connect(database_path) as connection:
            row = connection.execute("SELECT COUNT(*) FROM flight_price_cache").fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row else 0


def _browser_pages_summary(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return {"status": "missing", "path": str(path)}
    budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else payload
    keys = [
        "tab_count",
        "max_tabs",
        "tabs_over_budget",
        "window_count",
        "max_windows",
        "windows_over_budget",
        "browser_mode",
        "connection_mode",
    ]
    return {key: budget.get(key) for key in keys if key in budget} | {"path": str(path)}


def _browser_budget_block_reason(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "CDP preflight pages output was missing or invalid."
    budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else payload
    if budget.get("tabs_over_budget"):
        return (
            "CDP preflight refused the live gate because tabs were over budget "
            f"({budget.get('tab_count')}/{budget.get('max_tabs')})."
        )
    if budget.get("windows_over_budget"):
        return (
            "CDP preflight refused the live gate because windows were over budget "
            f"({budget.get('window_count')}/{budget.get('max_windows')})."
        )
    return ""


def _is_blocked_payload(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status") or "")
    stop_state = str(payload.get("stop_state") or "")
    return status in BLOCKED_STOP_STATES or stop_state in BLOCKED_STOP_STATES


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _read_exit_code(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        return int(path.read_text().strip())
    except ValueError:
        return None


def _read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def _date_values(start: str, end: str) -> list[str]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    days = max((end_date - start_date).days + 1, 1)
    return [date.fromordinal(start_date.toordinal() + offset).isoformat() for offset in range(days)]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
