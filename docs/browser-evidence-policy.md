# Browser Evidence Policy

Live Google Flights probing is an explicit evidence workflow, not part of
normal validation.

## Browser Modes

Implemented live probes default to headless mode. Headed mode is allowed when:

- headless is blocked
- visual confirmation is required
- selector or state evidence is ambiguous
- a user explicitly requests headed mode

Record browser mode for every run.

## Adapter Validation

Default validation uses fake subprocess tests for the cdp adapter and must not
start browser sessions or contact Google Flights.

Opt-in local cdp smoke is available through:

```bash
make live-cdp
```

That smoke checks local `cdp doctor` and `cdp pages` through the adapter in
headless mode. It does not open Google Flights. Google Flights live evidence
refreshes require separate task-scoped runs and artifacts.

Opt-in Google Flights smoke is available through:

```bash
make live-google-flights
```

This opens Google Flights in headless mode, writes project-local
`.gflights/runs/<run-id>/` artifacts, and returns either `experimental` evidence
capture output or a structured stop state. It must not be part of default
validation.

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
