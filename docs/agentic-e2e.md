# Agentic E2E Plan

Implementation begins with red e2e and contract tests.

## First Red Tests

Write failing tests for:

- CLI help lists atomic commands
- `schema --model search-intent --json` emits JSON Schema
- `project init` creates project-local config and artifact roots
- `intent parse --input-json` validates a JSON array of dicts
- `dates scan --offline-fixtures` returns JSON array output with explanations
- `evidence replay` parses saved fixtures offline
- browser adapter defaults to headless
- blocked headless fixture recommends headed fallback
- `codec decode` reports raw wire paths and confidence
- deterministic exit codes
- `uv tool install --editable --link-mode symlink .` exposes the CLI entry point

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
