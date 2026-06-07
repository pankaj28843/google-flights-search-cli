# Detailed Google Flights CLI Specification

Spec version: `0.1-evidence-backed`

Status: Slice 04 detailed behavior spec.

Evidence snapshot: 2026-06-07 Europe/Copenhagen.

This specification defines the behavior of a generic, agent-first Google
Flights CLI. It separates Google Flights behavior claims from implementation
mechanics. The first implementation is planned as Python 3 with `uv`, but the
behavior contract below is language-neutral unless a section is explicitly
marked implementation policy.

## Evidence Baseline

Included evidence:

- `research/google-flights-capability-matrix.md`
- `research/query-parameter-ledger.md`
- `research/network-ledger.md`
- `research/protobuf-ledger.md`
- `research/evidence-to-spec-gates.md`
- `research/reverse-engineering-confidence-report.md`
- `research/spec-readiness-decision.md`
- `research/runs/gf-20260607-073838-capability-surface/`
- `research/runs/gf-20260607-104222-slice02-url-counterexamples/`
- `research/runs/gf-20260607-113947-slice02-populated-trip-cabin/`
- `handoffs/agentic-e2e-tdd-plan.md`
- `handoffs/implementation-harness-plan.md`

No query/protobuf/RPC parser field is `proven`. Strong hypotheses may become
supported behavior only with caveats, fixture replay, and explicit stale-codec
failure behavior.

## Supported, Deferred, And Rejected Scope

Supported with evidence:

- round-trip and one-way searches
- concrete departure and return dates
- route endpoints by airport code or city/city-like autocomplete result
- passenger categories: adult, child aged 2-11, infant in seat, infant on lap
- economy and business cabin classes
- sort choices: top flights, price, departure time, arrival time, duration,
  emissions
- language and currency context
- primary search result rows from the results page DOM
- date grid and price graph as helper surfaces only
- selected-itinerary and booking-summary details observed before provider
  transaction boundaries
- baggage text, flight numbers, layovers, emissions, cabin facilities, and
  baggage-policy links when visible

Deferred or unsupported until focused probes prove them:

- multi-city live search support
- premium economy and first-class live search support
- Google flexible-date control support
- airline filter, airport filter, flight-duration filter, maximum-layover
  filter, and minimum-layover filter
- `GetShoppingResults` nested response parser fields
- `GetBookingResults` nested booking parser fields
- no-results live fixture behavior
- blocked or unusual-traffic live fixture behavior
- user-controlled Google Flights location mutation
- top-flight `tfu` reload equivalence across absent `tfu`, `tfu.2.1 = 0`, and
  `tfu.2.1 = 1`

Rejected from current evidence:

- browser storage as a semantic source for route, dates, passengers, selected
  legs, providers, prices, or booking options
- date-grid or price-graph data as final itinerary truth
- booking-summary RPC data as primary search-result truth
- exact selectors, generated classes, or DOM indexes as durable behavior

## Safety Boundary

The CLI must stop and return a structured stop state before:

- account login
- unusual traffic or access-denied interstitials
- provider `Continue` flows that leave the Google Flights booking-summary
  boundary
- payment, booking confirmation, personal-data entry, or passenger identity
  collection
- attempts to bypass anti-automation, consent, or access controls

Provider links and baggage-policy links may be surfaced as URLs when already
visible or safely decoded from Google redirect parameters. The CLI must not
submit personal data or initiate a booking.

## Command Surface

All data commands must support `--json`. Human-readable help is allowed, but
agents must rely on JSON schemas and stable JSON output, not terminal prose.

| Command | Required behavior | Boundary | Evidence status |
|---|---|---|---|
| `gflights schema` | Emit JSON Schema for input/output models. | functional core + CLI shell | implementation policy |
| `gflights intent parse` | Normalize JSON input and optionally parse text into a `SearchIntent`; ambiguous free text returns `ambiguous`. | service layer | implementation policy |
| `gflights project init` | Create app-state config, cache, and artifact directories. | imperative shell | implementation policy |
| `gflights route resolve` | Resolve airport/city route choices through fixtures or live evidence. | service + shell adapter | observed route evidence |
| `gflights dates scan` | Expand date windows into concrete date pairs, run or replay searches, and rank candidate combinations. | service layer + adapters | date/result evidence |
| `gflights search` | Run one concrete search intent and return primary result rows plus evidence paths. | service + shell adapter | observed result evidence |
| `gflights itinerary inspect` | Inspect one selected itinerary and return booking/detail fields when visible. | service + shell adapter | selected-itinerary evidence |
| `gflights evidence capture` | Capture headed/headless browser evidence for a named scenario. | imperative shell | harness policy |
| `gflights evidence replay` | Replay redacted fixtures offline and return parsed result/evidence summaries. | functional core + file shell | harness policy |
| `gflights codec decode` | Decode captured `tfs`/`tfu` values and report raw wire paths plus confidence. | functional core | strong hypothesis evidence |
| `gflights doctor` | Report toolchain, browser, project, fixture, and codec health. | imperative shell | implementation policy |

Commands must be atomic and composable. A command that cannot satisfy an input
because evidence is weak or missing must return `unsupported`, `deferred`,
`ambiguous`, `blocked`, or `experimental`; it must not silently approximate.

## Exit Codes

| Code | Meaning |
|---:|---|
| 0 | success |
| 2 | invalid or ambiguous user input |
| 3 | unsupported or deferred capability requested |
| 4 | browser blocked, login required, unusual traffic, human required, or safety boundary reached |
| 5 | stale fixture, stale codec hypothesis, or evidence mismatch |
| 6 | toolchain or project configuration failure |

JSON output must include the same status as the exit code class.

## Core JSON Envelope

All search-like commands return:

```json
{
  "query_id": "string",
  "status": "ok",
  "confidence": "strong",
  "results": [],
  "unsupported": [],
  "warnings": [],
  "evidence": {
    "run_id": "string",
    "artifacts": [],
    "source_surfaces": []
  }
}
```

Allowed `status` values:

- `ok`
- `unsupported`
- `deferred`
- `ambiguous`
- `blocked`
- `no_results`
- `experimental`
- `stale_fixture`
- `tool_error`

Allowed `confidence` values:

- `proven`
- `strong`
- `weak`
- `unknown`
- `rejected`

Current Google Flights query/protobuf behavior must not emit `proven`.

## Input Model

### SearchIntent

The canonical input object is:

```json
{
  "query_id": "del-cph-senior-oct-nov",
  "origin": {"text": "Delhi", "kind": "city_or_airport"},
  "destination": {"text": "Copenhagen", "kind": "city_or_airport"},
  "trip_type": "round_trip",
  "departure_window": {"start": "2026-10-01", "end": "2026-10-07"},
  "return_window": {"start": "2026-11-24", "end": "2026-11-30"},
  "passengers": {"adults": 2, "children": 0, "infants_in_seat": 0, "infants_on_lap": 0},
  "traveler_profiles": [{"kind": "senior", "comfort_weight": "high"}],
  "cabin": "economy",
  "airline_preferences": [{"airline": "Air India", "mode": "preferred"}],
  "consider_all_airlines": true,
  "currency": "EUR",
  "language": "en",
  "location": null,
  "sort": "top_flights"
}
```

`search` and `dates scan` accept one `SearchIntent` or a JSON array of
`SearchIntent`-like dicts. Single-object input returns one output object.
JSON-array input returns one output object per input item in input order unless
a command explicitly returns ranked results within each query.

### Trip Type

| Value | Status | Evidence | Behavior |
|---|---|---|---|
| `round_trip` | supported | strong | Requires departure and return date or windows. |
| `one_way` | supported | strong | Requires departure date/window; return date must be absent. |
| `multi_city` | deferred | weak UI only | Return `unsupported`/`deferred` for live search until leg encoding is probed. |

One-way searches must not retain stale return-date state. Evidence: populated
one-way CPH-Lucknow removed the `2026-06-22` return-date anchor.

### Passengers

Fields:

- `adults`
- `children`
- `infants_in_seat`
- `infants_on_lap`

Supported evidence covers adding one of each passenger type against a one-adult
baseline. The codec hypothesis maps observed passenger categories to repeated
top-level `tfs.8` values, but the exact encoder schema is not proven. Counts
beyond observed examples must be validated through fixtures or return
`ambiguous` when the implementation cannot prove support.

### Cabin

| Value | Status | Evidence | Behavior |
|---|---|---|---|
| `economy` | supported | strong | Observed empty and populated `tfs.9 = 1`. |
| `business` | supported | strong | Observed empty and populated `tfs.9 = 3`. |
| `premium_economy` | deferred | weak UI only | Return unsupported/deferred for live search until probed. |
| `first` | deferred | weak UI only | Return unsupported/deferred for live search until probed. |

The CLI must not infer premium/first values by adjacency.

### Route

Route endpoints support:

- airport-code-like choices observed with CPH and DEL
- city/city-like choices observed with Washington and Lucknow
- autocomplete disambiguation outputs with labels and evidence paths

The route resolver must return choices with:

- `text`
- `kind`
- `display_name`
- `code_or_id` when visible or decoded
- `confidence`
- `evidence`

Ambiguous autocomplete input must return `ambiguous` and candidate choices
instead of selecting silently.

### Dates And Date Windows

Concrete departure and return dates are supported for observed one-way and
round-trip behavior. Date windows are a CLI service-layer expansion: the CLI
generates concrete date pairs and probes/replays each pair. This does not imply
support for a Google Flights flexible-date control.

Rules:

- `round_trip` requires `departure_date`/`return_date` or both windows.
- `one_way` requires only departure date/window.
- Date-window scans must state the generated date-pair count and any pruning
  from date grid or price graph helper surfaces.
- Date-window scans must account for every generated pair as `fresh_cache`,
  `probed`, `unsupported`, or `skipped` with evidence. A scan must not rank a
  pair unless a fresh cache observation or probe result supplies a visible price.
- Date grid and price graph can narrow candidate pairs, but final itinerary
  output must come from primary results or selected itinerary evidence.

### Search Context

| Field | Status | Behavior |
|---|---|---|
| `language` | supported | Use observed `hl` URL/request context. |
| `currency` | supported | Use observed `curr` price-display context. |
| `location` | experimental/deferred | Record desired location, but live mutation is not supported until probed. |

### Preferences And Filters

Live Google Flights filters for airline, airport, duration, maximum layover, and
minimum layover are deferred.

The CLI may still support transparent post-result ranking hints when the needed
fields are visible:

- airline preference can score results whose carriers are visible
- senior-comfort scoring can prefer fewer stops, shorter total duration,
  manageable layovers, baggage/facility evidence, and lower schedule risk when
  those fields are available
- duration and layover preferences can rank or filter returned result rows after
  extraction

Every such ranking must return an explanation object and must not claim that a
Google Flights filter was applied.

### Sorting

Supported sort values:

- `top_flights`
- `price`
- `departure_time`
- `arrival_time`
- `duration`
- `emissions`

Short `tfu.2.1` sort hypotheses are strong for observed values, but top flights
has multiple observed representations. The CLI may request top flights, but
must not assert strict reload equivalence among absent `tfu`, `0`, and `1`.

## Output Model

### SearchResult

A result row may include:

- `result_id`
- `source_surface`
- `confidence`
- `origin_airports`
- `destination_airports`
- `departure_times`
- `arrival_times`
- `carriers`
- `flight_numbers`
- `duration_text`
- `duration_minutes` when safely parsed
- `stops`
- `layovers`
- `price`
- `currency`
- `emissions`
- `baggage_summary`
- `warnings`
- `evidence`

Primary source surface: primary-results DOM. `GetShoppingResults` may be stored
as support evidence, but nested response parser fields are deferred.

Absence behavior:

- Missing optional fields must be `null`, empty arrays, or structured
  `unavailable` objects, not guessed.
- Missing required primary row fields make the row `weak` or `ambiguous`.

### DateComboResult

`dates scan` returns one result object per requested intent:

- `query_id`
- `generated_pairs`
- `probed_pairs`
- `coverage_counts`
- `pair_coverage`
- `ranked_pairs`
- `ranking_policy`
- `unsupported`
- `warnings`
- `evidence`

Each ranked pair includes:

- departure date
- return date when round trip
- best observed price when available
- result count when available
- top result summary
- scoring explanation
- evidence references

Each pair coverage entry includes:

- departure date
- return date when round trip
- status: `fresh_cache`, `probed`, `unsupported`, or `skipped`
- observation count and best observed price when available
- unsupported reason or skipped reason when unavailable
- evidence references

### ItineraryDetail

Observed itinerary/detail fields:

- selected outbound and return segments
- booking provider names
- booking platform prices
- baggage included and baggage warnings
- airline and flight number per leg
- layover airport, duration, and overnight/transfer warnings when visible
- emissions estimates
- cabin facilities
- absolute baggage-policy links when decodable from visible link targets

Terminal information was not found in the selected itinerary evidence. The CLI
must output `terminal_info.status = "not_found"` or omit the field with an
explicit absence reason; it must not invent terminal names or terminal-change
warnings.

## Query And Protobuf Policy

URL state:

- `tfs` is treated as a volatile URL-safe-base64 protobuf-like codec over
  search and selected-itinerary state.
- Short sort `tfu` is treated as a volatile sort codec for observed sort states.
- Booking-summary `tfu` is a different shape and remains weak.

Admitted strong hypotheses:

- route/date/selected-leg blocks under repeated `tfs.3[]`
- passenger category list under repeated top-level `tfs.8`
- economy/business cabin behavior at top-level `tfs.9`
- round-trip/one-way behavior at top-level `tfs.19` plus dated leg count
- observed sort choices at nested `tfu.2.1`

Constraints:

- Do not assign stable friendly field names inside generated code without
  evidence comments and fixture links.
- Do not require full encoder support when browser/UI probing can produce the
  state safely.
- Any encoder/decoder promoted into the implementation must have fixture tests
  for raw value, decoded wire paths, confidence, and stale behavior.
- If a fixture contradicts a hypothesis, return `stale_fixture` or
  `unsupported`; do not fall back to guessing.

Implementation scope admitted from the strong evidence set:

- The live search shell may construct populated `tfs` for concrete one-way or
  round-trip searches with one concrete departure date, one concrete return date
  when required, evidence-backed route endpoints, observed passenger category
  values, and observed economy/business cabin values.
- The live search shell may construct short sort `tfu` for observed non-default
  sort values: price, departure time, arrival time, duration, and emissions.
- Top-flights direct live URLs should omit `tfu` until absent `tfu`,
  `tfu.2.1 = 0`, and `tfu.2.1 = 1` reload equivalence is proven.
- Unsupported encoded query-state inputs must stay explicit in JSON through
  `query_population.status = "unsupported"` rather than silently falling back to
  a guessed URL.

Network state:

- `GetShoppingResults` is a primary-results RPC candidate and support surface.
- `GetCalendarPicker` is a cheap-date helper candidate.
- `GetBookingResults` supports selected-itinerary and booking-summary evidence.
- RPC nested arrays are not stable parser contracts yet.

Storage:

- Current evidence rejects storage-backed semantic behavior. Browser storage may
  be captured/redacted as evidence but must not drive search semantics.

## Functional-Core / Service / Shell Boundaries

Functional core:

- value objects for route, trip type, dates, passengers, cabin, search context,
  preferences, result rows, evidence refs, confidence, and stop states
- date-window expansion
- input validation and ambiguity classification
- post-result ranking and explanation
- query/protobuf decode interpretation over fixture bytes
- JSON output shaping and JSON Schema generation

Service layer:

- parse or normalize `SearchIntent` dicts
- orchestrate route resolution, date scans, concrete searches, itinerary
  inspection, evidence replay, and codec diagnostics
- accept primitive dicts and return validated output dicts
- depend on fakeable adapters for browser, filesystem, fixture store, and
  process execution

Imperative shell:

- Typer command adapter
- `cdp` subprocess adapter
- headed/headless browser mode choice
- network capture and storage inspection
- screenshots, DOM snapshots, command logs, and artifact writes
- user-local app-state directory creation
- `uv`, `ruff`, pytest, and `gh` command checks

No browser, network, filesystem, process, or repository side effect belongs in
the pure functional core.

## Browser Mode Policy

Implementation policy:

- Default live browser mode: `headless`.
- Use `headed` only when headless is blocked, ambiguous, missing required visual
  evidence, or explicitly requested.
- Record browser mode, target page id, URL, command, run id, and artifacts for
  every live capture.
- `cdp --browser-mode headed pages --json` is an accepted tab-discovery surface
  for headed exploration.

Default validation must not hit live Google Flights. Normal CLI search may use
the live Google Flights path; repository validation remains offline unless a
live target or marker is selected.

## App State And Artifact Policy

Default use:

- Runtime state lives under `~/.gflights-search` unless
  `GFLIGHTS_SEARCH_HOME` or an explicit init path overrides it.
- `project init` creates `config.json`, `cache.sqlite`, `artifacts/`,
  `fixtures/`, and `runs/` under the app-state root.
- `config.json` sets live Google Flights search as the default and records a
  six-hour maximum age for cached flight prices.
- Runtime evidence uses task-scoped run directories.
- Raw browser/network/storage artifacts must be redacted before they become
  committed fixtures.
- Normal validation replays fixtures offline.

Every command that creates artifacts must return their paths in JSON.

## Error And Stop States

| State | Required behavior |
|---|---|
| `no_results` | Return structured status; fixture pending, so live-specific details stay experimental. |
| `capability_not_found` | Return unsupported/not_found with evidence reference. |
| `blocked` | Stop and record safe evidence without bypassing. |
| `login_required` | Stop; do not attempt login. |
| `payment_or_booking_boundary` | Stop before provider transaction flow. |
| `ambiguous_autocomplete` | Return candidates and exit 2. |
| `unsupported_combination` | Return status and unsupported field list. |
| `weak_codec` | Return unsupported/experimental unless fixture-backed behavior is available. |
| `stale_fixture` | Return exit 5 and point to codec maintenance docs. |
| `tool_error` | Return exit 6 with command, adapter, and repair hints. |

## TDD Acceptance For First Implementation

Slice 07 must write red tests before implementation code for:

- CLI help exposing atomic commands
- JSON Schema export
- app-state init
- JSON array input validation and one-output-per-search-intent behavior
- offline fixture replay
- date-window scan output shape
- headless default and headed fallback recommendation
- codec decode confidence output
- deterministic exit codes
- editable/symlinked `uv tool install` smoke behavior

The first green implementation may use fixtures and fake adapters. Live Google
Flights is the default CLI value path; smoke tests may still be isolated by
marker or task-scoped runs.

## Evidence Appendix

Required references by spec area:

- capability admission: `research/spec-readiness-decision.md`
- capability rows: `research/google-flights-capability-matrix.md`
- query params: `research/query-parameter-ledger.md`
- protobuf hypotheses: `research/protobuf-ledger.md`
- network surfaces: `research/network-ledger.md`
- rough command and e2e seed: `handoffs/rough-agentic-cli-spec-draft.md`
- TDD implementation plan: `handoffs/agentic-e2e-tdd-plan.md`
- harness and safety rules: `handoffs/implementation-harness-plan.md`

Any future spec change that adds support for a deferred capability must add:

- capability matrix update
- probe scenario row or update
- raw evidence path
- one-variable mutation or documented edge/counterexample
- confidence class
- functional-core/service/shell owner
- test or validation signal
