# Google Flights Search CLI

Agent-first CLI for evidence-backed Google Flights search, itinerary inspection,
and reusable deep links.

Current phase: live-cdp-by-default search orchestration, parser-backed live
route visible-text extraction, generic query-codec validation, fake-tested live
form interaction planning, and visible-text primary-result extraction. The
behavior spec is written, the Python project passes the offline agentic
contract, and `gflights search` uses headless cdp by default. Post-result
ranking objectives, top-K alternatives, independent itinerary row selection,
structured booking-summary extraction, and opt-in tab-budget evidence now exist
for the live workflows. `--live-form` is an additional explicit experimental
mode for observed form controls.

Runtime state defaults to `~/.gflights`: `config.json`,
`cache/cache.sqlite`, and task-scoped `runs/` evidence bundles live there unless
`GFLIGHTS_SEARCH_HOME` or an explicit state path overrides it. The default config sets
`GFLIGHTS_RUN_GOOGLE_FLIGHTS_LIVE=1` and keeps cached flight-price observations
fresh for at most six hours. Set `cache_max_age_seconds` in `config.json` or
`GFLIGHTS_CACHE_MAX_AGE_SECONDS` for a run-specific freshness override.

## Validation

```bash
make validate
```

Default validation checks repository harness/docs, TDD replay assets, and
fake-adapter behavior. Normal `gflights search` use opens the live Google
Flights path.

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

The Python suite covers offline contracts, route autocomplete replay through
service tests, app-state/cache behavior, fake live search orchestration, fake
live route stop-state handling and visible-text choice extraction, visible-text
result extraction, local cdp smoke, and command-scoped Google Flights live
evidence.

Local cdp smoke target:

```bash
make live-cdp
```

Current expected result: 2 `live_cdp` tests pass against local `cdp doctor` and
`cdp pages`. This does not open Google Flights.

Google Flights smoke:

```bash
gflights preflight headless-heal --consent-choice accept-all --json
gflights preflight google-flights --top-k 5 --min-complete-selections 3 --json
make live-google-flights
```

Current expected result: opens Google Flights in headless mode and returns
visible-text result rows when extractable, `experimental` evidence output when
a no-row state is not classifiable, `unsupported` when bounded
evidence waits still show empty/loading results, `no_results` when visible text
explicitly says there are no flights, or exits `4` with a structured stop state
and headed fallback recommendation. `gflights route resolve` also opens live
cdp evidence and returns either parsed visible autocomplete choices, explicit
live extraction deferral, or a structured browser stop state.
Long crawls should request top 5 rows for coverage but may set
`--min-complete-selections 3` so a valid top-3 booking smoke test does not
block on flaky lower-ranked synthetic rows.
The preflight can rotate across public synthetic route candidates after
transient search failures; inspect `diagnostics.route_attempts` to see whether
fallback was used.
Synthetic search has a preflight-level deadline and reports
`preflight_search_timeout` in route diagnostics instead of relying on the caller
to kill a hung command.

Date-window scans are cache-first. They rank fresh SQLite price observations by
default and open Google Flights for missing date pairs only when explicitly
bounded:

```bash
gflights dates scan --input-json intents.json --project-root ~/.gflights --json
gflights dates scan --input-json intents.json --project-root ~/.gflights --objective balanced --top-k 10 --json
gflights dates scan --input-json intents.json --project-root ~/.gflights --live-probe --max-probes 5 --json
```

Live browser commands follow one simple shape: open an evidence-backed Google
Flights URL, wait for a terminal page state, query visible DOM/text for rows,
click only selected Google Flights rows when a command explicitly needs a
booking-summary URL, then repeat the same settle/query cycle. They stop before
login, unusual-traffic bypass, provider checkout, payment, booking, or personal
data.

Use `gflights itinerary select --search-url <url> --preferred-carrier "<carrier>"
--outbound-row-rank 2 --return-row-rank 1 --json` when an agent needs to turn a
Google Flights search URL into a Google booking-summary URL before running
`gflights itinerary inspect`.

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
artifacts/
scripts/
```

The checked-in tests cover the agentic CLI contract for `schema`, `intent`,
`project`, `route`, `dates`, `codec`, `doctor`, unsupported/deferred/ambiguous
exit codes, isolated editable `uv tool install --editable --link-mode symlink .`
smoke behavior, domain/service invariants, generic `tfs`/`tfu` wire decode round
trips, fake live form interaction planning, and cdp adapter command
construction and stop-state handling. Live tests cover local cdp smoke and
command-scoped Google Flights live evidence.
