# Google Flights Search CLI

Agent-first CLI for evidence-backed Google Flights search, itinerary inspection,
and fixture replay.

Current phase: opt-in live evidence capture. The behavior spec is written, the
Python project passes the offline agentic contract, and `gflights search
--live-cdp` can open Google Flights in headless mode to capture task-scoped cdp
evidence without claiming durable result extraction.

## Validation

```bash
make validate
```

Default validation checks repository harness/docs only and does not contact
Google Flights. Live browser probes must be explicit evidence refresh or smoke
commands.

Python checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Current expected result: 33 tests pass and 3 opt-in live tests skip, including
11 offline e2e contract tests.

Opt-in local cdp smoke:

```bash
make live-cdp
```

Current expected result: 2 `live_cdp` tests pass against local `cdp doctor` and
`cdp pages`. This does not open Google Flights.

Opt-in Google Flights smoke:

```bash
make live-google-flights
```

Current expected result: opens Google Flights in headless mode and returns
`experimental` evidence capture output, or exits `4` with a structured stop
state and headed fallback recommendation.

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
`project`, `dates`, `evidence`, `codec`, `doctor`, unsupported/deferred exit
codes, isolated editable `uv tool install --editable --link-mode symlink .`
smoke behavior, domain/service invariants, and cdp adapter command construction
and stop-state handling. Opt-in tests cover local cdp smoke and Google Flights
evidence capture.
