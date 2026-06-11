# Agent Router

Use this file as the short router for this repository.

## Source Of Truth

Read in this order:

1. `README.md` for current phase and validation.
2. `docs/agent-instruction-map.md` for how durable agent rules map to
   repository docs and validation commands.
3. `docs/detailed-cli-spec.md` for behavior requirements.
4. `docs/schema-and-json-contracts.md` for agent-facing JSON and exit-code contracts.
5. `docs/cache-layer-policy.md` before changing app-state cache storage,
   freshness, migrations, or SQLite/ORM dependencies.
6. `docs/flight-search-first-principles.md` for durable domain concepts.
7. `docs/query-state-maintenance.md` before changing encoded query/protobuf behavior.
8. `docs/cdp-usage-discipline.md` before using `cdp`, changing live browser
   orchestration, or running live smoke tests.
9. `docs/browser-evidence-policy.md` before using live Google Flights.
10. `docs/fixture-contract.md` before adding or changing fixtures.
11. `docs/review-and-cleanup.md` before review, cleanup, or stale-evidence work.
12. `docs/agentic-e2e.md` before writing implementation tests.

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
- When changing Google Flights result-row discovery, row expansion, row
  selection, or extraction, inspect current browser evidence before coding
  selectors. Verify command support with `cdp click --help`, `cdp eval --help`,
  and `gflights --help`; when headed evidence is needed, start from
  `cdp --browser-mode headed pages --json` and target the exact page id. Prefer
  human-facing ARIA/role structure as the live contract: stage heading, nearby
  `[role=list]`, row-local `[role=link]` whose accessible name contains
  `Select flight`, closest row container, and row-local `Flight details` buttons
  for expansion. Treat generated classes, absolute DOM indexes, and stale
  snapshot text as fallback evidence only.
- Expand the top considered result rows before extracting structured row
  details. The default considered set is at least 5 rows and at most 10 rows
  unless a checked-in contract says otherwise. Clicks that should move the
  Google Flights state must verify the next stage with `--wait-text`,
  `--wait-url-contains`, or an equivalent semantic condition; do not replace a
  missing verification with long blind waits. If in-page JavaScript performs
  its own semantic wait, the surrounding CDP timeout must be longer than that
  in-page wait; the row click helper waits up to 5 seconds, so its CDP eval
  timeout must stay above 5 seconds.
- Build live Google Flights automation in two layers: generic async CDP helpers
  for command invocation, timeouts, artifact capture, and polling assertions;
  then Google Flights domain helpers for conditions such as rows rendered,
  rows expanded, return rows reached, and booking options visible. Prefer
  `async with helper.stage("..."):` and assertion artifacts over embedding
  long retry loops inside browser JavaScript. A timed-out assertion should
  return or raise with the last observed CDP evidence so the next code change is
  evidence-based.
- For full round-trip option crawling, remember that return choices are nested
  under each outbound choice. Top-k means an outbound top-k multiplied by a
  return top-k, not a single flat list. Use a practical default of outbound
  top 5 x return top 3 for constrained family itinerary searches, and increase
  toward 5 x 5 only when runtime and booking-option evidence stay healthy. If
  a max budget is configured, normalize prices into the trip currency and add
  date-adjustment costs first, then prune branches that already exceed budget
  instead of doing unnecessary return-row fanout where no acceptable candidates
  can remain. Treat top-k as user-constraint aware, not merely Google's row
  order: filter/rank by requested currency, nonstop-only, min/max stops,
  max layover duration, checked-baggage requirements, cabin facilities, and
  other user objectives before spending crawl budget on more rows. When a
  constraint maps to a stable Google Flights filter, such as stops, airlines,
  bags, times, connecting airports, emissions, or duration, apply that filter
  before row fanout and then verify the resulting rows still satisfy the
  user-level constraint from ARIA/booking evidence. UI filters are search-space
  reducers; row-wise extraction and booking evidence remain the source of truth
  for final candidate inclusion.
- Treat Google Flights text such as `Oops, something went wrong` and `Reload`
  as a transient page error, not real route availability. In live search, a
  provisional page-error signal from the terminal wait must continue to dwell
  and snapshot evidence before returning `tool_error` with
  `stop_state: google_page_error`; in row selection, the same signal should feed
  operation-level retry diagnostics.
- Treat short sort `tfu` as volatile Google Flights state. It may be emitted
  for observed non-default sort values such as price, but final user-facing
  alternatives must still be filtered and ranked from extracted row/booking
  evidence instead of trusting Google's URL sort alone.
