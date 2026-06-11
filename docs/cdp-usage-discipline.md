# CDP Usage Discipline

This repository uses `cdp` as a live browser evidence tool. Default validation
must stay offline, but normal `gflights search` and explicit live smoke tests may
open Google Flights through `cdp`.

## Evidence From Profiling

On 2026-06-07, live-test profiling showed the headless browser at `25/25` tabs
with `tabs_over_budget: true`. A single encoded live probe then returned
`browser_resource_budget_exceeded` in about three seconds. After stale Google
Flights tabs were cleaned with `cdp page cleanup`, headless pages dropped to
`5/25` and the browser budget returned to healthy.

The performance lesson is harness-level: unmanaged live tabs make later live
tests slower or blocked before the CLI has a chance to search.

## Preflight

Before any live `cdp` run, inspect the selected browser mode and tab budget:

```bash
cdp --browser-mode headless daemon health --json
cdp --browser-mode headless pages --json
```

For long Google Flights crawls, use the maintained recovery ceremony instead of
hand-running isolated cleanup commands:

```bash
gflights preflight headless-heal --consent-choice accept-all --json
gflights preflight google-flights --consent-choice accept-all --top-k 5 --return-top-k 3 --min-complete-selections 3 --json
```

`headless-heal` explicitly closes stale Google Flights page targets and stale
`data-cdp-health` diagnostic targets, runs `cdp daemon health-check --repair`,
opens Google Flights to settle consent, and records Google consent-cookie plus
daemon-health evidence. It is safe to run before a large crawl and again after a
burst of transient `google_page_error` rows. If the command reports `blocked`,
pause the crawl and inspect its artifact paths before continuing.

If headless health stays green but Google Flights keeps returning
`google_page_error`, use the heavier runtime reset before the next preflight:

```bash
gflights preflight headless-heal --restart-daemon --consent-choice accept-all --json
```

If `tabs_over_budget` is true, do not use `--allow-over-budget` for routine
testing. First dry-run cleanup for stale Google Flights pages:

```bash
cdp --browser-mode headless page cleanup \
  --include-url google.com/travel/flights \
  --workflow-created \
  --max 25 \
  --json
```

Close only cleanup candidates reported as ready:

```bash
cdp --browser-mode headless page cleanup \
  --include-url google.com/travel/flights \
  --workflow-created \
  --close \
  --max 25 \
  --json
```

## Headed Slow-Cook Debugging

Use headed debugging when live Google Flights row lookup, row detail expansion,
return-row transition, booking-summary settlement, or booking-option extraction
is failing and headless artifacts do not explain the current browser behavior.
The point is to watch the real UI slowly enough to learn the accessibility
contract, then codify the smallest resilient automation rule.

First verify the selected browser mode and command surface:

```bash
cdp --browser-mode headed daemon health --json
cdp --browser-mode headed pages --json
cdp a11y tree --help
cdp eval --help
cdp click --help
cdp wait --help
cdp text --help
```

Open or navigate one Google Flights target and keep its page id for all
inspection. Do not spread one debugging question across multiple tabs.

```bash
cdp --browser-mode headed open '<google-flights-url>' --json --timeout 30s
cdp --browser-mode headed pages --json
```

Inspect human-facing structure before selectors. Prefer the accessibility tree
and small target-scoped DOM probes over generated classes, absolute DOM indexes,
or body-text guesses:

```bash
cdp --browser-mode headed a11y tree --target <page-id> --depth 6 --limit 200 --json
cdp --browser-mode headed eval '<small ARIA/role inspection expression>' --target <page-id> --json --timeout 10s
```

For Google Flights row selection, the current live contract is the stage
heading, its nearby visible `[role=list]`, row-local `[role=link]` whose
accessible name contains `Select flight`, the closest `li`/`[role=listitem]`,
and row-local `Flight details` buttons. Watch one outbound row expansion, one
return row expansion, and the booking-summary transition before changing code.

Codify the observation in two layers:

- generic async CDP helpers for command invocation, bounded retries,
  target-scoped artifact capture, polling cadence, and timeout evidence
- Google Flights domain assertions for rows rendered, top considered rows
  expanded with `aria-expanded=true`, return rows reached, and booking options
  visible

A failure in those assertions should report the last observed terminal
condition, row count, URL, title, and relevant ARIA snippets. Do not replace a
missing condition with a long blind sleep or a single opaque browser JavaScript
block. Be quick between polls, but give Google Flights enough total time for
real rendering: rows, expanded details, and booking options can appear after
URL or load-state signals.

Close the headed target you opened, then confirm headed tab state. In busy
sessions target close can take up to 60 seconds:

```bash
cdp --browser-mode headed page close --target <page-id> --timeout 60s --json
cdp --browser-mode headed pages --json
```

After code changes to this path, run focused preflight tests before live smoke:

```bash
uv run pytest tests/unit/test_live_search.py tests/unit/test_live_selection.py tests/unit/test_preflight.py -q
gflights itinerary select --browser-mode headed --search-url '<public test search URL>' --timeout-seconds 75 --max-tabs 15 --project-root /tmp/gflights-headed-select --json
cdp --browser-mode headed pages --json
```

The smoke is useful only if it proves semantic readiness: rows were rendered,
the selected rows expanded, the return stage transitioned, booking options were
visible, and the CLI-managed headed target was cleaned up afterward.

## Reuse Policy

One live workflow should reuse its own opened page target for all follow-up
steps: `wait`, form interaction, `snapshot`, `network`, and stop-state
inspection. Do not open a fresh tab for each step in a single intent.

Batch searches may open more than one managed page, but only within explicit
`--concurrency` bounds. Default live search concurrency is 3 and the supported
range is 1-5. The batch command must still keep each page task-scoped and close
each CLI-managed page when its evidence capture ends.

Google Flights result pages must use a composite settle policy before DOM/text
extraction: terminal page content first, then the minimum live dwell and network
steadiness metadata. Do not treat `DOMContentLoaded`, `load`, body stability,
network idle, or footer currency text as sufficient result readiness by itself.
Source-backed rationale must be captured in checked-in docs or repository TDD
assets before it is cited by repo docs, source, tests, or TDD asset metadata.

This policy matches current browser-automation guidance: Playwright documents
`networkidle` as discouraged for readiness and points users toward web
assertions; Selenium documents that browser `readyState` can return before
JavaScript-driven UI is ready and recommends explicit waits for concrete
application conditions; Puppeteer exposes network-idle waits as network
quiescence telemetry with configurable idle time/concurrency. For this CLI,
visible Google Flights rows, no-results text, booking-summary text, or browser
stop-state text are the semantic condition. Body stability and network
steadiness only corroborate that late rendering has quieted down.

References:

- https://playwright.dev/docs/api/class-frame
- https://www.selenium.dev/documentation/webdriver/waits/
- https://pptr.dev/api/puppeteer.page.waitfornetworkidle

Do not treat a random existing Google Flights tab as semantic state for a new
search. Existing tabs may be inspected for cleanup and budget health, but route,
date, passenger, cabin, and result semantics must come from the current intent,
cache, repository TDD assets, or captured evidence.

An explicit reuse option may navigate an existing Google Flights target to the
new command URL to reduce headed/profile tab pressure. That target's previous
page state is still not semantic evidence. Because the CLI did not create a
reused target, it records tab-budget before/after evidence and skips target
close.

## Managed Tab Memory

Every live command that opens a page owns that page target until it is closed or
a stop state prevents cleanup. The command must record managed-tab evidence in
task-local artifacts under the app-state `runs/<run-id>/` directory:

- run id
- UUID v4 task trace ids: a root operation id, parent id for fanout children
  when applicable, and a leaf managed-tab task id
- command name
- browser mode
- target URL
- opened page target id when available
- a target-id-to-task-id ownership map for Chrome page targets created or
  recovered by this command
- close attempt status when cleanup is attempted
- any cleanup warning

Target ids and raw browser artifacts are local evidence. Do not publish them as
checked-in TDD assets.

For batch/fanout workflows, every child search or selection attempt must share
the root task id and get its own UUID v4 child task id. Managed tabs are leaf
tasks. Cleanup may close only the target mapped to that leaf task, including
targets recovered from a partially failed `cdp open` result. Reused targets are
not entered in the ownership map and must not be closed by the command.

Every CDP command goes through the repo-local adapter retry envelope before
domain logic sees the result. Retry only transient daemon/connection/target
lookup races, use at most three attempts inside the caller-supplied total
timeout, and record `attempt_count`, `max_attempts`, and per-attempt summaries
in command artifacts. Domain-level retries such as Google page-error search
retries remain separate and must also be bounded.

## Close Policy

Live commands should close CLI-managed tabs after evidence capture on all paths
where a page id is known and mapped to the current managed-tab task:

- success
- experimental evidence capture
- structured browser stop state after open
- tool error after open
- unsupported live form or extraction behavior after open

Closing a page is cleanup, not domain behavior. If close fails, record a warning
and a close artifact; do not replace a valid search, route, or itinerary result
with a cleanup failure.

Use target-specific cleanup when possible:

```bash
cdp --browser-mode headless page close --target <page-id> --json
```

Managed target close is not a one-shot operation. The CLI should retry page
close a few times and allow up to 60 seconds per attempt because overloaded
headless sessions can take that long to close tabs. A timeout must be recorded
as cleanup evidence and followed by a retry or a final Google Flights tab sweep
before the workflow returns.

Preflight cleanup may also close stale `data-cdp-health` diagnostic tabs left by
CDP health probes. These targets are identified by their `data-cdp-health`
URL/title and are recorded as diagnostic cleanup, not as Google Flights domain
behavior.

Headless Chrome must keep at least one neutral page target open so the managed
browser does not close completely. Before a headless cleanup sweep would remove
every page, open or preserve `chrome://newtab/`; if the only page is an inert
detached `about:blank`/new-tab target, preserve it as the keepalive page instead
of closing it. Detached `about:blank` pages may be treated as stale diagnostic
cleanup candidates only in headless mode and only when this keepalive invariant
is preserved.

Use broader `page cleanup` only for stale task leftovers, live-smoke setup, or
manual recovery from resource-budget pressure.

## Live Smoke Discipline

`make live-cdp` checks local `cdp` readiness and pages. It must not open Google
Flights.

`make live-google-flights` is allowed to open Google Flights. Before timing or
profiling it, capture:

- `cdp --browser-mode headless daemon health --json`
- `cdp --browser-mode headless pages --json`
- wall-clock command timing
- resulting run artifacts
- post-run tab budget

If live smoke fails because of browser resource budget, fix the browser harness
state and rerun once. Do not infer Google Flights behavior from an over-budget
browser. The preferred first repair is:

```bash
gflights preflight headless-heal --consent-choice accept-all --max-tabs 25 --json
```

## Boundaries

Do not automate account login, payment, booking, personal-data entry,
provider checkout, unusual-traffic bypass, access-control bypass, or publication
of raw browser/network/storage artifacts.

Do not use `--allow-over-budget` unless the user explicitly asks for a diagnostic
run that accepts additional browser-resource pressure.
