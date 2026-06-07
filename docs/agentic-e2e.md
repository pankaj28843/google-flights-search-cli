# Agentic E2E Plan

Implementation begins with red e2e and contract tests.

## First Red Tests

Write failing tests for:

- CLI help lists atomic commands
- `schema --model search-intent --json` emits JSON Schema
- `project init` creates app-state config, SQLite cache, and artifact roots
- `intent parse --input-json` validates a JSON array of dicts
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
- `schema --model search-intent --json` emits the `SearchIntent` JSON Schema
- `project init --path <tmp> --json` creates app-state files under `<tmp>/`
- `intent parse --input-json <array> --json` preserves JSON-array order
- `dates scan --offline-fixtures` returns JSON-array date-scan explanations
- `evidence replay` parses redacted offline fixtures
- `evidence replay` extracts visible-text primary result fixtures into result
  rows
- `doctor --json` reports headless default and live-search-by-default config
- blocked headless replay returns exit `4` and headed fallback guidance
- `codec decode --fixture` reports raw wire paths, verifies fixture hypotheses
  against the generic Python codec, and keeps confidence non-`proven`
- `search` defaults to live cdp when `--offline-fixtures` is absent, with fake
  adapters used in unit tests so default validation stays offline
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
