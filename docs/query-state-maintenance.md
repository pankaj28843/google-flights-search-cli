# Query State Maintenance

Google Flights encoded URL parameters and protobuf-like values are volatile
codecs over stable flight-search intent. Treat them as hypotheses, not a
permanent public API.

## Supported Evidence Classes

Use these confidence classes:

- `proven`
- `strong`
- `weak`
- `unknown`
- `rejected`

Current query/protobuf behavior has no `proven` fields.

The current Python implementation includes a generic URL-safe-base64 and
protobuf-wire decoder for captured `tfs`/`tfu` evidence. It also includes a
small evidence-backed query-state builder for the subset whose encoded bytes
are re-derived from controlled captures. The generic decoder reports numeric
wire paths and printable string anchors only. Semantic labels such as trip
type, cabin, or sort remain TDD-asset hypotheses, not codec-owned truth.

## Required Evidence For Codec Support

Every supported query/protobuf field needs:

- stable domain intent
- raw baseline value
- one-variable mutation value
- counterexample or edge case
- raw changed value
- decoded hypothesis
- confidence level
- checked TDD-asset reference
- capture date
- reviewer decision
- unsupported or deprecated fallback behavior

## Current Strong Hypotheses

Strong but not proven:

- `tfs` carries search and selected-itinerary state
- repeated `tfs.3[]` blocks carry route/date/search-leg and selected-leg anchors
- repeated top-level `tfs.8` values distinguish observed passenger categories
- top-level `tfs.9` distinguishes observed economy/business cabin state
- top-level `tfs.19` plus leg count distinguishes observed round-trip/one-way state
- nested `tfu.2.1` distinguishes observed sort choices in captured URLs

Deferred:

- premium economy and first values
- multi-city encoding
- filter encoding
- broader sort `tfu` reload safety and top-flight reload equivalence
- nested RPC parser fields

Implemented encoded subset:

- one-way and round-trip only
- concrete date values only, not date windows
- evidence-backed airport-code endpoints and a small evidence-backed
  city/autocomplete map
- observed passenger category repeated values
- economy and business cabin values
- non-default sort values with observed short `tfu`; treat this as volatile
  Google Flights state and keep user ranking objectives as local post-row
  extraction logic

Every live output reports this through `query_population`: `encoded` means the
URL was populated from this subset and opened on `/travel/flights/search`;
`unsupported` means the CLI opened the Google Flights shell and recorded why no
evidence-backed encoded query state was available.

## Maintenance Workflow

1. Create a task-scoped evidence run.
2. Capture raw URL, visible state, network evidence when relevant, and command log.
3. Decode values with the generic Python codec tool.
4. Compare baseline, mutation, and counterexample.
5. Update repository TDD assets and confidence ledger.
6. Add or update tests before changing reusable codec behavior.
7. Preserve unsupported/stale behavior for unproven fields.

## Failure Behavior

If evidence cannot re-derive a codec field, the CLI must return `unsupported`,
`ambiguous`, or `stale_evidence`; it must not guess.
