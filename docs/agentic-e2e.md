# Agentic E2E Plan

Implementation begins with red e2e and contract tests.

## First Red Tests

Write failing tests for:

- CLI help lists atomic commands
- `schema --model search-intent --json` emits JSON Schema
- `project init` creates app-state config, SQLite cache, and artifact roots
- `intent parse --input-json` validates a JSON array of dicts
- `route resolve --offline-fixtures` returns route choices or ambiguity from
  reviewed fixtures
- `dates scan --offline-fixtures` returns JSON array output with explanations
- `evidence replay` parses saved fixtures offline
- browser adapter defaults to headless
- blocked headless fixture recommends headed fallback
- `codec decode` reports raw wire paths, round-trip codec metadata, and
  confidence
- deterministic exit codes
- `uv tool install --editable --link-mode symlink .` exposes the CLI entry point

## Current E2E Bootstrap

The current `tests/e2e/test_agentic_cli_contract.py` suite is the offline
agentic contract for the first implementation. It asserts:

- root help lists the atomic command families
- root help is self-contained for installed users, including workflow,
  defaults, environment, examples, and exit codes
- `schema --model search-intent --json` emits the `SearchIntent` JSON Schema
- `project init --path <tmp> --json` creates app-state files under `<tmp>/`
- `intent parse --input-json <array> --json` preserves JSON-array order
- `route resolve --input-text <text> --offline-fixtures <fixtures> --json`
  returns deterministic route choices and exits `2` for ambiguous
  autocomplete inputs instead of selecting silently
- `route resolve --input-text <text> --json` defaults to live cdp evidence
  capture when fixtures are absent, with stop-state behavior fake-adapter tested
  so default validation stays offline
- `evidence replay route_autocomplete_visible_text` extracts route autocomplete
  choices from redacted visible-text snapshots without contacting Google Flights
- `dates scan --offline-fixtures` returns JSON-array date-scan explanations
- `dates scan --project-root` can rank from fresh SQLite cache observations
  without opening a browser in default validation
- `dates scan --live-probe` is visible in command help, requires an explicit
  positive `--max-probes` bound, and is otherwise covered by fake unit adapters
  so default validation stays offline
- `evidence replay` parses redacted offline fixtures
- `evidence replay` extracts visible-text primary result fixtures into result
  rows
- `evidence replay` extracts selected-itinerary visible-text fixtures into
  structured itinerary detail fields without entering provider checkout
- `itinerary inspect` exposes a live cdp command surface whose stop-state
  behavior is fake-adapter tested, including provider checkout and personal-data
  boundaries
- `itinerary select` exposes the row-clicking workflow that turns a search URL
  into a Google Flights booking-summary URL without crossing provider checkout
- `doctor --json` reports headless default and live-search-by-default config
- blocked headless replay returns exit `4` and headed fallback guidance
- `codec decode --fixture` reports raw wire paths, verifies fixture hypotheses
  against the generic Python codec, and keeps confidence non-`proven`
- `search` defaults to live cdp when `--offline-fixtures` is absent, with fake
  adapters used in unit tests so default validation stays offline
- live `route resolve` reports explicit deferred extraction or structured
  browser stop states instead of guessing autocomplete choices from unsupported
  selectors
- live `route resolve` uses collision-resistant run ids, retries transient
  execution-context failures once, tries bounded fill-selector fallbacks, and
  writes managed-tab close artifacts after opened-page failures
- route visible-text replay keeps city IDs null when they are not visible and
  uses airport IATA codes only when they are present in the visible row
- live `search` reports `query_population` and uses fixture-backed encoded
  query state for supported concrete intents
- live `search` retries empty/loading snapshots once after a bounded
  network-idle wait, returns `no_results` for visible no-results text, and
  returns specific `unsupported` output for persistent empty/loading evidence
- `search --offline-fixtures` accepts JSON-array input and preserves input order
- deferred live Google filter requests exit `3`
- an isolated `uv tool install --editable --link-mode symlink . --force`
  exposes `gflights`, then `gflights doctor --json` proves the installed entry
  point behavior

The editable install test sets `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, and
`UV_CACHE_DIR` to temporary directories so it does not write to the user's global
tool install location.

Current expected result:

```bash
uv run pytest
```

Default result includes the offline e2e contract tests plus unit coverage for
app-state/cache behavior, visible-text result extraction, and fake live
orchestration. Live Google Flights is the normal search path; deterministic
tests use fakes or fixtures where needed.

Opt-in local cdp smoke:

```bash
make live-cdp
```

Result: 2 `live_cdp` tests pass against local `cdp doctor` and `cdp pages`.
This smoke does not open Google Flights.

Google Flights smoke:

```bash
make live-google-flights
```

The default config sets `GFLIGHTS_RUN_GOOGLE_FLIGHTS_LIVE=1`. The smoke records
task-scoped run artifacts and accepts visible-text result rows, `experimental`
evidence capture when rows are not extractable, or a structured browser stop
state.

The command path under test is:

```bash
gflights search --input-json <intent.json> --browser-mode headless --json
```

## Test Layers

- Domain tests for value objects and ranking rules.
- Optional analysis adapter tests use injected fake pandas so default
  validation does not require the `analysis` extra.
- Service tests for use cases with fake adapters.
- CLI tests for stdout JSON, stderr diagnostics, and exit codes.
- Fixture replay tests for deterministic evidence.
- Live `cdp` smoke tests behind an explicit marker only.

## Validation After Implementation Starts

Planned full validation:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv tool install --editable --link-mode symlink .
gflights doctor --json
```

Live smoke:

```bash
uv run pytest -m live_cdp
```
