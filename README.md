# Google Flights Search CLI

Agent-first CLI for evidence-backed Google Flights search, itinerary inspection,
and fixture replay.

Current phase: red Python e2e bootstrap. The behavior spec is written and the
Python project skeleton exists, but production CLI behavior has not started yet.

## Validation

```bash
make validate
```

Default validation checks repository harness/docs only and does not contact
Google Flights. Live browser probes must be explicit evidence refresh or smoke
commands.

Python bootstrap checks:

```bash
uv run ruff check .
uv run ruff format --check .
```

The first e2e suite is intentionally red until implementation starts:

```bash
uv run pytest
```

Expected current result: 11 failures for missing public CLI command behavior and
JSON contracts.

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

The checked-in tests cover the red agentic CLI contract for `schema`, `intent`,
`project`, `dates`, `evidence`, `codec`, `doctor`, unsupported/deferred exit
codes, and isolated editable `uv tool install --editable --link-mode symlink .`
smoke behavior.
