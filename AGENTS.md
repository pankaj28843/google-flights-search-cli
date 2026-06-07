# Agent Router

Use this file as the short router for this repository.

## Source Of Truth

Read in this order:

1. `README.md` for current phase and validation.
2. `docs/detailed-cli-spec.md` for behavior requirements.
3. `docs/schema-and-json-contracts.md` for agent-facing JSON and exit-code contracts.
4. `docs/flight-search-first-principles.md` for durable domain concepts.
5. `docs/query-state-maintenance.md` before changing encoded query/protobuf behavior.
6. `docs/browser-evidence-policy.md` before using `cdp` or live Google Flights.
7. `docs/fixture-contract.md` before adding or changing fixtures.
8. `docs/review-and-cleanup.md` before review, cleanup, or stale-evidence work.
9. `docs/agentic-e2e.md` before writing implementation tests.

## Current Phase

The repository is in early implementation. Red e2e tests, the Python/uv CLI
harness, live-cdp-by-default orchestration, app-state/cache setup, fake live
form planning, live route stop-state handling and visible-text choice
extraction, visible-text result extraction, and bounded live smoke coverage
exist. Keep new behavior evidence-backed and update the docs/contracts before
expanding Google Flights support.

## Validation

Run:

```bash
make validate
```
