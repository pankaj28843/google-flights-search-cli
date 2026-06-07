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

The repository is in harness bootstrap. Production CLI implementation starts
only after red e2e tests are written in the next implementation slice.

## Validation

Run:

```bash
make validate
```

Default validation must not hit live Google Flights.

## Safety

Do not automate account login, payment, booking, personal-data entry, provider
checkout, unusual-traffic bypass, access-control bypass, or publication of raw
browser/network/storage artifacts.
