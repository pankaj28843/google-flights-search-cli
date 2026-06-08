# Fixture Contract

Fixtures are repository TDD assets. They make default validation deterministic
and offline, but installed `gflights` commands and package sources must not
expose fixture paths, fixture replay commands, fixture replay helpers, or
app-state fixture directories.

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

Checked-in `route_autocomplete_visible_text` fixtures are redacted normalized
visible-text snapshots for parser replay. They may include only the text needed
to identify autocomplete labels, city descriptors, and visible airport IATA
codes. They must not include raw browser URLs, storage, network payloads, target
IDs, cookies, account labels, or encoded query values. City `code_or_id` values
must remain `null` unless a decoded evidence report is explicitly joined by a
separate reviewed fixture.

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

Default validation may exercise parsers and codecs against fixtures through
TDD tests without contacting Google Flights. Installed commands must use
live/cache/run evidence surfaces instead of fixture arguments. Live refreshes
must be explicit and task-scoped.

## App State Boundary

`gflights project init --path <app-state-root> --json` creates runtime state
only: `config.json`, `cache/cache.sqlite`, `artifacts/`, and `runs/` under the
app-state root. By default that root is `~/.gflights`.

Checked-in fixtures under repository `tests/` or `fixtures/` are for
deterministic development and TDD only. User-local evidence refreshes belong in
`runs/` and `artifacts/`; they must not become checked-in fixtures unless
redacted, reviewed, and kept in the repository test-data area.
