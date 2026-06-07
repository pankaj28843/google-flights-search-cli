# Agent Router

Use this file as the short router for this repository.

## Source Of Truth

Read in this order:

1. `README.md` for current phase and validation.
2. `docs/detailed-cli-spec.md` for behavior requirements.
3. `docs/schema-and-json-contracts.md` for agent-facing JSON and exit-code contracts.
4. `docs/cache-layer-policy.md` before changing app-state cache storage,
   freshness, migrations, or SQLite/ORM dependencies.
5. `docs/flight-search-first-principles.md` for durable domain concepts.
6. `docs/query-state-maintenance.md` before changing encoded query/protobuf behavior.
7. `docs/cdp-usage-discipline.md` before using `cdp`, changing live browser
   orchestration, or running live smoke tests.
8. `docs/browser-evidence-policy.md` before using live Google Flights.
9. `docs/fixture-contract.md` before adding or changing fixtures.
10. `docs/review-and-cleanup.md` before review, cleanup, or stale-evidence work.
11. `docs/agentic-e2e.md` before writing implementation tests.

## Current Phase

The repository is in early implementation. Red e2e tests, the Python/uv CLI
harness, live-cdp-by-default orchestration, app-state/cache setup, fake live
form planning, live route stop-state handling and visible-text choice
extraction, visible-text result extraction, configurable SQLite cache freshness,
managed cdp-tab cleanup discipline, and bounded live smoke coverage exist. Keep
new behavior evidence-backed and update the docs/contracts before expanding
Google Flights support.

## Validation

Run:

```bash
make validate
```
