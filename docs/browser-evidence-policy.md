# Browser Evidence Policy

Live Google Flights probing is the default value path for `gflights search`.
It is still not part of normal validation.

Read `docs/cdp-usage-discipline.md` before live `cdp` work. That document owns
tab-budget preflight, target reuse, managed-tab memory, and close/cleanup rules.

## Browser Modes

Implemented live probes default to headed cdp mode with a 50-tab Chrome/CDP
capacity and an 8 GiB minimum free-memory guard. Google Flights search fanout
has its own five-tab limit; it is not the browser-wide capacity. Headless mode
is limited to the explicitly named maintenance and recovery workflow; it is
not a fallback for normal searches.

Record browser mode for every run.

## Adapter Validation

Default validation uses fake subprocess tests for the cdp adapter, fake live
form tests, and service-level replay over checked TDD assets. It must not start
browser sessions or contact Google Flights.

Opt-in local cdp smoke is available through:

```bash
make live-cdp
```

That smoke checks local `cdp doctor` and `cdp pages` through the adapter. It does
not open Google Flights. Google Flights live evidence
refreshes require separate task-scoped runs and artifacts.

Google Flights smoke is available through:

```bash
make live-google-flights
```

This opens Google Flights in headed mode through the default live search path,
writes state-local `runs/<run-id>/` artifacts under `~/.gflights` or
`GFLIGHTS_SEARCH_HOME`, and returns visible-text result rows when extractable,
`experimental` evidence output when rows are not extractable, or a structured
stop state. It must not be part of default validation.

`--live-form` is an additional explicit experimental mode that uses observed
accessible labels and visible concepts, records one artifact per form step, and
stops on browser safety boundaries instead of bypassing them.

`gflights itinerary inspect --booking-url <url> --json` is the live
selected-itinerary path. It opens an existing Google Flights booking URL,
captures visible selected-itinerary text, and must not click provider
`Continue` controls or enter checkout. Default validation uses fake cdp adapter
tests for this command.

`gflights route resolve --input-text <text> --json` is the live route evidence
path. It opens the Google Flights shell, fills the route autocomplete field,
captures route-autocomplete evidence, and returns parser-backed visible choices
when available. If no supported choices are visible, it returns explicit
deferred output. Default validation uses fake cdp adapter tests for stop states
and does not open Google Flights.

## Required Artifacts

Each live run must capture:

- run id
- command log
- browser mode
- target URL and page id when available
- managed-tab close artifact when the CLI opened a page target
- visible text or DOM snapshot
- network request/response metadata when relevant
- screenshots when layout matters
- stop state
- redaction notes

## Selector Policy

Selectors are evidence implementation details. Durable docs must not make exact
selectors, generated classes, or DOM indexes the source of truth.

Accessible labels and visible concepts may be cited as evidence examples, but
domain behavior must be expressed in first-principles terms.

## Redaction

Never commit raw cookies, auth tokens, unredacted storage, personal data, or
raw browser artifacts that could expose account state.

## Stop States

Stop and record safe evidence on:

- unusual traffic
- access denied
- login required
- browser resource budget exceeded
- payment or booking boundary
- personal-data prompts
- permission or human-required states

Do not bypass.
