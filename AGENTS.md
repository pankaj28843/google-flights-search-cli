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

## Repo Hygiene

- Checked-in docs, source, tests, fixtures, and agent instructions must not cite
  capsule scratch files, temporary research runs, personal home-directory
  projects, or any other artifact that is not checked into this repository as
  durable evidence. If outside evidence informs a behavior contract, summarize
  it in a checked-in doc or fixture first, then cite that repo-relative file.
- Before committing cleanup or documentation changes, run `make validate`; the
  harness includes a stale-reference scan for non-repo capsule, handoff, and
  personal-path citations.

## Forward-Only Maintenance

- This is a small, fast-moving, agent-facing CLI. Do not preserve backward
  compatibility or legacy behavior unless a checked-in contract explicitly says
  it is still supported. When Google Flights behavior changes, update the
  relevant docs, fixtures, contracts, and implementation for the current
  behavior in the same bug-fix pass.
- Prefer deleting obsolete code, stale fixtures, dead options, and legacy docs
  over adding compatibility layers. It is acceptable to remove code as part of a
  fix when the remaining behavior is evidence-backed and validation stays green.

## Live Google Flights Changes

- When changing live Google Flights orchestration, settlement, or batch
  concurrency, first check CDP health with
  `cdp --browser-mode headless daemon health --json` and command support with
  `cdp wait --help`, `cdp text --help`, and `cdp network --help`. Validate the change with
  `uv run pytest tests/unit/test_live_search.py -q`, plus the relevant
  app-state/help tests when state paths or CLI options change. Use terminal DOM
  content as the readiness gate; do not treat footer currency text, load state,
  body stability, or network idle alone as result readiness. Live runs that use
  the settlement helper must save `settlement.json` with terminal condition,
  dwell, body-stability, and network-steadiness evidence.
