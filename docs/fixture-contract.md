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
