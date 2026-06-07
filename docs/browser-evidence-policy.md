# Browser Evidence Policy

Live Google Flights probing is the default value path for `gflights search`
when offline fixtures are not supplied. It is still not part of normal
validation.

## Browser Modes

Implemented live probes default to headless cdp mode. Headed mode is allowed
when:

- headless is blocked
- visual confirmation is required
- selector or state evidence is ambiguous
- a user explicitly requests headed mode

Record browser mode for every run.

## Adapter Validation

Default validation uses fake subprocess tests for the cdp adapter, fake live
form tests, and fixture replay. It must not start browser sessions or contact
Google Flights.

Opt-in local cdp smoke is available through:

```bash
make live-cdp
```

That smoke checks local `cdp doctor` and `cdp pages` through the adapter in
headless mode. It does not open Google Flights. Google Flights live evidence
refreshes require separate task-scoped runs and artifacts.

Google Flights smoke is available through:

```bash
make live-google-flights
```

This opens Google Flights in headless mode through the default live search path,
writes state-local `runs/<run-id>/` artifacts under `~/.gflights-search` or
`GFLIGHTS_SEARCH_HOME`, and returns visible-text result rows when extractable,
`experimental` evidence capture output when rows are not extractable, or a
structured stop state. It must not be part of default validation.

`--offline-fixtures` is the explicit deterministic replay path. `--live-form`
is an additional explicit experimental mode that uses observed accessible labels
and visible concepts, records one artifact per form step, and stops on browser
safety boundaries instead of bypassing them.

## Required Artifacts

Each live run must capture:

- run id
- command log
- browser mode
- target URL and page id when available
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
- payment or booking boundary
- personal-data prompts
- permission or human-required states

Do not bypass.
