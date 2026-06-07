# Google Flights Search CLI

Agent-first CLI for evidence-backed Google Flights search, itinerary inspection,
and fixture replay.

Current phase: harness bootstrap. The behavior spec is written, but production
CLI implementation has not started yet.

## Validation

```bash
make validate
```

Default validation checks repository harness/docs only and does not contact
Google Flights. Live browser probes must be explicit evidence refresh or smoke
commands.

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
docs/
fixtures/
artifacts/
scripts/
```

Implementation package and tests are added in the red e2e slice.
