# Fixture Contract

Fixtures make default validation deterministic and offline.

## Fixture Types

Planned fixture families:

- raw URL samples
- query/protobuf decode samples
- visible text snapshots
- DOM snippets for primary result rows
- network request/response metadata
- selected-itinerary snapshots
- booking-summary snapshots
- unsupported, ambiguous, blocked, and stale examples

Checked-in `selected_itinerary_visible_text` fixtures are redacted normalized
visible-text snapshots. They may include separately decoded absolute
baggage-policy links only when the visible link text was observed and the
decoded target was reviewed. They must not include raw browser URLs, storage,
network payloads, target IDs, cookies, or encoded query values.

Checked-in `route_autocomplete_choices` fixtures are redacted normalized route
choice rows from reviewed visible-text notes and decode reports. They include
only agent-facing route choice fields and evidence references; they must not
include raw browser URLs, storage, network payloads, target IDs, cookies, or
encoded query values.

## Fixture Metadata

Every fixture needs:

- run id
- capture date
- source surface
- scenario
- confidence
- redaction status
- linked behavior requirement
- expected parser or stop-state output

## Freshness

Fixtures are evidence snapshots. A stale fixture should fail with a repair
message that points to:

- the stale behavior
- the evidence run
- the relevant docs
- the smallest live refresh needed

## Default Validation

Default validation must replay fixtures without contacting Google Flights.
Live refreshes must be explicit and task-scoped.

## App-State Fixtures

`gflights project init --path <app-state-root> --json` creates a fixture root at
`fixtures/` and a run root at `runs/` under the app-state root. By default that
root is `~/.gflights-search`.

Checked-in fixtures under repository `tests/` or `fixtures/` are for deterministic
development. App-state fixtures are for a user's own evidence refreshes and
must not be published unless redacted and reviewed.
