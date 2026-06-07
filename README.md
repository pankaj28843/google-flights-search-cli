# Google Flights Search CLI

Agent-first CLI for evidence-backed Google Flights search, itinerary inspection,
and fixture replay.

Current phase: first green offline CLI core. The behavior spec is written and
the Python project passes the agentic e2e contract against offline fixtures.
Live Google Flights behavior has not started yet.

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

Current expected result: 11 offline e2e tests pass.

## Behavior Contract

Read `docs/detailed-cli-spec.md`.

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
codes, and isolated editable `uv tool install --editable --link-mode symlink .`
smoke behavior.
