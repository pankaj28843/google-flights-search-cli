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
gflights preflight google-flights --consent-choice accept-all --top-k 5 --min-complete-selections 3 --json
```

`headless-heal` explicitly closes stale Google Flights page targets, runs
`cdp daemon health-check --repair`, opens Google Flights to settle consent, and
records Google consent-cookie plus daemon-health evidence. It is safe to run
before a large crawl and again after a burst of transient `google_page_error`
rows. If the command reports `blocked`, pause the crawl and inspect its
artifact paths before continuing.

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
- command name
- browser mode
- target URL
- opened page target id when available
- close attempt status when cleanup is attempted
- any cleanup warning

Target ids and raw browser artifacts are local evidence. Do not publish them as
checked-in TDD assets.

## Close Policy

Live commands should close CLI-managed tabs after evidence capture on all paths
where a page id is known:

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
browser.

## Boundaries

Do not automate account login, payment, booking, personal-data entry,
provider checkout, unusual-traffic bypass, access-control bypass, or publication
of raw browser/network/storage artifacts.

Do not use `--allow-over-budget` unless the user explicitly asks for a diagnostic
run that accepts additional browser-resource pressure.
