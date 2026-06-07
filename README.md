# Google Flights Search CLI

Agent-first CLI for evidence-backed Google Flights search, itinerary inspection,
and fixture replay.

Current phase: live-cdp-by-default search orchestration, fixture-backed route
resolution, parser-backed live route visible-text extraction, generic
query-codec validation, fake-tested live form interaction planning, and
visible-text primary-result extraction. The behavior spec is written, the
Python project passes the offline agentic contract, and `gflights search` uses
headless cdp by default when `--offline-fixtures` is not provided. `--live-form`
is an additional explicit experimental mode for observed form controls.

Runtime state defaults to `~/.gflights-search`: `config.json`, `cache.sqlite`,
and task-scoped `runs/` evidence bundles live there unless `GFLIGHTS_SEARCH_HOME`
or an explicit state path overrides it. The default config sets
`GFLIGHTS_RUN_GOOGLE_FLIGHTS_LIVE=1` and keeps cached flight-price observations
fresh for at most six hours.

## Validation

```bash
make validate
```

Default validation checks repository harness/docs and fake-adapter behavior.
Normal `gflights search` use opens the live Google Flights path unless
`--offline-fixtures` is supplied.

Python checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Install the CLI as a `uv` tool:

```bash
make install
```

Install it editable for local development:

```bash
make install-editable
```

Both targets install the `gflights` entry point with `uv tool install`; the
editable target uses `--editable --link-mode symlink --force`.

The Python suite covers offline contracts, route autocomplete fixture replay,
app-state/cache behavior, fake live search orchestration, fake live route
stop-state handling and visible-text choice extraction, visible-text result
extraction, local cdp smoke, and Google Flights evidence capture.

Local cdp smoke target:

```bash
make live-cdp
```

Current expected result: 2 `live_cdp` tests pass against local `cdp doctor` and
`cdp pages`. This does not open Google Flights.

Google Flights smoke:

```bash
make live-google-flights
```

Current expected result: opens Google Flights in headless mode and returns
visible-text result rows when extractable, `experimental` evidence capture
output when rows are not yet extractable, or exits `4` with a structured stop
state and headed fallback recommendation. `gflights route resolve` also defaults
to live cdp evidence capture when fixtures are absent and returns either parsed
visible autocomplete choices, explicit live extraction deferral, or a structured
browser stop state.

## Behavior Contract

Read `docs/detailed-cli-spec.md`.
Read `docs/schema-and-json-contracts.md` for JSON Schema, status, confidence,
exit-code, project-state, and stop-state contracts.

The core rule is evidence before support: unsupported, deferred, ambiguous, and
experimental behavior must be reported explicitly rather than guessed.

## Planned Stack

- Python 3
- `uv`
- Typer
- Pydantic
- pytest
- Ruff
- asyncio subprocess adapters for `cdp`
- pandas only in analysis/output adapters where justified

## Repository Layout

```text
AGENTS.md
pyproject.toml
src/gflights/
tests/
docs/
fixtures/
artifacts/
scripts/
```

The checked-in tests cover the agentic CLI contract for `schema`, `intent`,
`project`, `route`, `dates`, `evidence`, `codec`, `doctor`,
unsupported/deferred/ambiguous exit codes, isolated editable `uv tool install
--editable --link-mode symlink .` smoke behavior, domain/service invariants,
generic `tfs`/`tfu` wire decode round trips, fake live form interaction
planning, and cdp adapter command construction and stop-state handling. Live
tests cover local cdp smoke and Google Flights evidence capture.
