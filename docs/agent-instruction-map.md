# Agent Instruction Map

This document maps durable agent rules in `AGENTS.md` to the repository docs
and validation commands that make those rules executable.

## Map

| Agent Rule Area | Read Before Editing | Validation |
| --- | --- | --- |
| General CLI behavior | `docs/detailed-cli-spec.md`, `docs/schema-and-json-contracts.md` | `make validate` |
| App-state cache and freshness | `docs/cache-layer-policy.md` | `uv run pytest tests/unit/test_app_state.py tests/unit/test_services.py -q` |
| Query-state/protobuf behavior | `docs/query-state-maintenance.md` | `uv run pytest tests/unit/test_query_state.py -q` |
| Live CDP orchestration and tab hygiene | `docs/cdp-usage-discipline.md`, `docs/browser-evidence-policy.md` | `cdp --browser-mode headed pages --json`; focused live-search tests |
| Live Google Flights row discovery, expansion, selection, and extraction | `AGENTS.md` Live Google Flights Changes, `docs/browser-evidence-policy.md` Selector Policy, `docs/cdp-usage-discipline.md` Reuse Policy | `cdp click --help`; `cdp eval --help`; `gflights --help`; `uv run pytest tests/unit/test_live_selection.py tests/unit/test_live_search.py tests/unit/test_result_extraction.py -q` |
| Fixtures and checked evidence | `docs/fixture-contract.md` | fixture-focused pytest plus `make validate` |
| Review and cleanup | `docs/review-and-cleanup.md` | `git diff --check`; `make validate` before commit |

## Captured Learning: Browser Rows

Trigger this rule when changing live Google Flights row discovery, row detail
expansion, row selection, booking-page transition, or row-result extraction.
Do not trigger it for pure cache, query-state, ranking, or schema work unless
that work also changes live browser evidence.

Use first-principles browser evidence before coding selectors. Start from the
human-facing accessibility model Google exposes:

- stage heading such as `Departing flights`, `Returning flights`, or
  `Choose return`
- nearby list container with `role=list`
- row-local link with an accessible name containing `Select flight`
- closest row container as the click target
- row-local `Flight details` button with `aria-expanded=false` when richer row
  details need to be exposed before extraction

Use generated classes, absolute indexes, and body-text snapshots only as
fallback evidence. They may corroborate behavior, but they should not be the
primary live-browser contract.

The practical command contract is:

```bash
cdp --browser-mode headed pages --json
cdp click --help
cdp eval --help
gflights --help
uv run pytest tests/unit/test_live_selection.py tests/unit/test_live_search.py tests/unit/test_result_extraction.py -q
```

For headed debugging, first list targets and then inspect the exact Google
Flights target:

```bash
cdp --browser-mode headed pages --json
cdp eval '<small ARIA/role inspection expression>' --target <page-id> --json --browser-mode headed
```

Session evidence that established this rule: headed inspection of a public
JFK-SFO Google Flights route showed stable row structure through ARIA/role
parents and row-local `Flight details` buttons, while guessed CSS/class
selectors and unverified clicks produced false failures. The focused unit suite
covering live selection, live search, CLI preflight, and result extraction
passed after moving the implementation to ARIA/role row evidence.

## Captured Learning: Async CDP Flow

Trigger this rule when changing live Google Flights orchestration, row
selection, booking-page transition handling, booking-option parsing, or
multi-step crawl fanout. Do not trigger it for pure query-state encoding,
cache, ranking, or report formatting changes unless those changes also alter
live CDP behavior.

Use a layered implementation:

- generic async CDP helpers for command invocation, per-command timeout,
  artifact capture, polling cadence, and assertion timeout evidence
- Google Flights domain helpers for semantic assertions such as stage rows
  rendered, top rows expanded, return stage reached, and booking options visible
- orchestration code that reads as the human flow with
  `async with helper.stage("..."):` blocks

Do not bury long waits in one opaque browser JavaScript expression. Prefer a
Python `asyncio` loop that calls the same small CDP eval every 0.25-1 second,
records each attempt, and returns an assertion artifact with the last observed
DOM state when the condition times out.

Exact command contract:

```bash
cdp --help >/dev/null
cdp eval --help >/dev/null
cdp click --help >/dev/null
cdp wait --help >/dev/null
gflights --help >/dev/null
cdp --browser-mode headed pages --json >/dev/null
uv run pytest tests/unit/test_live_selection.py tests/unit/test_live_search.py tests/unit/test_result_extraction.py -q
git diff --check
```

Live smoke evidence from this session:

```bash
gflights itinerary select --browser-mode headed --search-url '<public JFK-SFO round-trip search URL>' --row-rank 1 --max-tabs 5 --project-root artifacts/headed-manual-inspection-2026-06-10 --json
gflights itinerary select --browser-mode headed --search-url '<public JFK-SFO round-trip search URL>' --row-rank 3 --max-tabs 5 --project-root artifacts/headed-manual-inspection-2026-06-10 --json
```

The row-rank 1 smoke reached a Google Flights booking URL, waited for booking
options instead of stopping at the URL alone, and extracted three American fare
options. The row-rank 3 smoke reached a booking URL and exposed a different
JetBlue/OTA booking-options shape; parser changes should be replayed against
captured booking snapshot text before another live run.

For combinatorial crawls, return choices are nested under each outbound choice.
Use outbound top 5 x return top 3 as the default family-itinerary fanout shape,
not one flat top-k. Increase toward 5 x 5 only when live runtime and booking
option evidence stay healthy. If the user supplies a max budget, normalize all
prices/currencies and date-adjustment costs first, then prune outbound or
return branches that already exceed the budget so the crawler does not spend
tabs on impossible candidates. Treat top-k as ask-aware: the selected rows to
crawl should reflect requested currency, nonstop-only or min/max stops, maximum
layover duration, checked-baggage requirements, cabin facilities, and explicit
ranking objectives instead of blindly following Google's row order. Some
constraints should become Google Flights UI filters when the filter is stable
and human-facing; keep row-wise extraction and booking evidence as the final
source of truth because UI filters reduce search space but do not replace
candidate validation.

## Cleanup Bias

Generating code quickly is not the same as converging on the simplest reliable
implementation. When browser evidence proves a smaller live path, delete
obsolete selector guesses, duplicated parsers, stale docs, and tests that only
protect abandoned behavior. Keep complementary fallback evidence only when a
checked-in contract still uses it.
