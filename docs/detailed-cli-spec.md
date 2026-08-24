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

Included evidence is limited to checked-in repository files:

- `docs/fixture-contract.md`
- `docs/browser-evidence-policy.md`
- `docs/query-state-maintenance.md`
- `docs/flight-search-first-principles.md`
- `tests/e2e/fixtures/route_autocomplete_choices_fixture.json`
- `tests/e2e/fixtures/route_autocomplete_visible_text_fixture.json`
- `tests/e2e/fixtures/primary_results_visible_text_fixture.json`
- `tests/e2e/fixtures/selected_itinerary_visible_text_fixture.json`

No query/protobuf/RPC parser field is `proven`. Strong hypotheses may become
supported behavior only with caveats, repository TDD replay, and explicit
stale-codec failure behavior.

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
- no-results live scenario behavior
- blocked or unusual-traffic live scenario behavior
- user-controlled Google Flights location mutation
- broader sort `tfu` reload safety, including top-flight equivalence across
  absent `tfu`, `tfu.2.1 = 0`, and `tfu.2.1 = 1`

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
| `gflights route resolve` | Resolve airport/city route choices through live evidence. | service + shell adapter | observed route evidence |
| `gflights dates scan` | Expand date windows into concrete date pairs, use fresh cache, optionally live-probe bounded misses, and rank candidate combinations. | service layer + adapters | date/result evidence |
| `gflights search` | Run one concrete search intent and return primary result rows plus evidence paths. | service + shell adapter | observed result evidence |
| `gflights itinerary select` | Select explicit visible Google Flights outbound/return rows from a search URL and return a Google booking-summary URL. | service + shell adapter | selected-itinerary evidence |
| `gflights itinerary inspect` | Inspect one selected itinerary and return booking/detail fields when visible. | service + shell adapter | selected-itinerary evidence |
| `gflights preflight google-flights` | Run synthetic public live smoke across route fallbacks, outbound x return row selection combinations, booking-summary evidence, and optional multiple future date ranges. | shell workflow + service adapters | live preflight evidence |
| `gflights preflight headless-heal` | Repair/check headless CDP, close stale Google Flights and CDP diagnostic tabs, settle consent, and record health evidence. | shell workflow | browser harness evidence |
| `gflights codec decode` | Decode captured `tfs`/`tfu` values and report raw wire paths plus confidence. | functional core | strong hypothesis evidence |
| `gflights doctor` | Report toolchain, browser, project, and codec health. | imperative shell | implementation policy |

Commands must be atomic and composable. A command that cannot satisfy an input
because evidence is weak or missing must return `unsupported`, `deferred`,
`ambiguous`, `blocked`, or `experimental`; it must not silently approximate.

Live browser command shape is deliberately simple: open an evidence-backed
Google Flights URL, wait for a terminal page state, query visible DOM/text for
rows or booking-summary details, click only explicit Google Flights result rows
when a command needs selection, then repeat the same settle/query cycle. The
CLI must stop before login, unusual-traffic bypass, provider checkout, payment,
booking, or personal-data entry.

`gflights itinerary select` is the row-clicking workflow. It accepts
`--search-url`, `--preferred-carrier`, `--require-nonstop`, `--row-rank`,
`--outbound-row-rank`, `--return-row-rank`, `--outbound-match-text`, and
`--return-match-text`, opens the search URL, selects one visible outbound row,
selects one visible return row when Google presents return choices, and returns
a Google Flights booking-summary URL plus selected row summaries and visible
booking options when parseable. For one-way search URLs, the outbound click may
transition directly to the booking summary; in that case `selection.return` and
`selected_return` are `null`. It must not click a provider `Continue` button or
enter provider checkout.

For JSON-array live search, `--concurrency` controls bounded parallel managed
tabs. The default is 3, allowed range is 1-5, and outputs must preserve input
order. This is resource discipline and polite pacing, not anti-automation
bypass. If Google or the browser presents a stop state, the CLI stops and saves
evidence.

For headed/profile workflows, `gflights search --managed-tab-policy reuse
--max-tabs <N>` and `gflights itinerary select --reuse-target google-flights
--max-tabs <N>` may record `cdp pages` tab-budget evidence before and after the
workflow. Reuse means navigating an explicit existing Google Flights target for
the supplied command URL; existing tab state is not semantic evidence for route,
date, passenger, cabin, or result fields. Reused tabs are not closed by the CLI.

## Exit Codes

| Code | Meaning |
|---:|---|
| 0 | success |
| 2 | invalid or ambiguous user input |
| 3 | unsupported or deferred capability requested |
| 4 | browser blocked, login required, unusual traffic, human required, or safety boundary reached |
| 5 | stale evidence, stale codec hypothesis, or evidence mismatch |
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
- `stale_evidence`
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
  "query_id": "del-cph-window-oct-nov",
  "origin": {"text": "Delhi", "kind": "city_or_airport"},
  "destination": {"text": "Copenhagen", "kind": "city_or_airport"},
  "trip_type": "round_trip",
  "departure_window": {"start": "2026-10-01", "end": "2026-10-07"},
  "return_window": {"start": "2026-11-24", "end": "2026-11-30"},
  "passengers": {"adults": 2, "children": 0, "infants_in_seat": 0, "infants_on_lap": 0},
  "cabin": "economy",
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
beyond observed examples must be validated through repository TDD assets or
return `ambiguous` when the implementation cannot prove support.

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

When a route choice is supplied back in `SearchIntent.origin.selected` or
`SearchIntent.destination.selected`, the selected object must use the same
normalized `RouteChoice` shape. `evidence.source_surfaces` and
`evidence.artifacts` must be non-empty. Airport choices may use a visible
three-letter IATA code. City choices may use a Google Flights city id such as
`/m/...` only when the evidence names a reviewed decode/protobuf surface; visible
autocomplete text alone is not enough to support direct query-state encoding.

Ambiguous autocomplete input must return `ambiguous` and candidate choices
instead of selecting silently.

`gflights route resolve` opens the live Google Flights shell through cdp, fills
the route autocomplete field, records task-scoped route evidence, and stops on
browser safety boundaries. Each live run must use a collision-resistant run id,
wait past `about:blank`, retry recoverable execution-context loss once, and
record every bounded fill attempt as an artifact. Selector labels are
implementation evidence; if the exact observed label fails, the command may try
alternate labels before returning a structured `tool_error`. Any opened page
must still get a `managed-tab-close.json` artifact on success, unsupported,
blocked, or tool-error paths. When parser support covers the autocomplete
surface, successful evidence capture returns `ok` or `ambiguous` with route
choices. Airport rows may use a visible IATA code as `code_or_id`; city rows
must leave `code_or_id` null unless a separate reviewed decode surface supplies
a stable ID. If no supported choices are visible, the command returns exit `3`
with `status: unsupported`, `selected: null`, empty `choices`, and
`route.resolve.live_autocomplete_extraction: deferred`. Browser stop states
return exit `4` with a structured `stop_state` and safe diagnostic guidance.

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
- Date-window scans must return `generated_pairs: 0` with `unsupported` or
  `ambiguous` status when route preflight cannot produce an evidence-backed
  route choice.
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

- visible price can rank lower observed prices first
- visible duration, stop count, and emissions can adjust ranking when they are
  present in result rows or fresh cache observations

Every such ranking must return an explanation object and must not claim that a
Google Flights filter was applied.

The maintained post-result ranking policy is `price_duration_v1`. It is a pure
service-layer policy over visible or cached fields, not a browser adapter
feature. Its explanation includes price, duration, stops, emissions when
visible, max layover when visible, denied transit airports when configured, a
numeric score, and `google_flights_filters_applied: false`.

`gflights search --rank cheapest,fastest,least-layover,balanced --top-k <N>`
may emit `top_cheapest`, `top_fastest`, `top_least_layover`, and `top_balanced`
arrays while preserving the raw `results` array. `--allow-transit` and
`--deny-transit` are local post-result ranking hints over visible layover
airport codes only; they do not apply Google Flights filters. Explicit large
`--top-k` searches may expand and extract up to 50 visible rows for high-volume
routes; default live extraction remains smaller for routine searches.

Optional tabular analysis may use pandas through an adapter outside the pure
domain core. The core date-scan JSON remains the stable contract; pandas-backed
tables are derived views for agents that need tabular comparison or export.
Default validation must pass without pandas installed.

### Sorting

Supported sort values:

- `top_flights`
- `price`
- `departure_time`
- `arrival_time`
- `duration`
- `emissions`

Short `tfu.2.1` sort hypotheses are strong for observed non-default sort
values. Treat sort `tfu` as volatile Google Flights state: it may seed the
Google row ordering, but requested ranking objectives are still applied after
row extraction.

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
- Visible no-results text returns `no_results`.
- Empty snapshots or snapshots that still show `Loading results` after bounded
  evidence waits return `unsupported` with a specific
  `live_result_extraction.*` field, not a useful zero-row success.

### DateComboResult

`dates scan` returns one result object per requested intent:

- `query_id`
- `generated_pairs`
- `probed_pairs`
- `coverage_counts`
- `pair_coverage`
- `ranked_pairs`
- `ranking_policy`
- `objective`
- `top_k`
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

`--objective cheapest|fastest|least-layover|balanced` chooses the local
post-result sort for `ranked_pairs`; `--top-k <N>` limits returned ranked pairs
without removing generated-pair coverage entries.

Each pair coverage entry includes:

- departure date
- return date when round trip
- status: `fresh_cache`, `probed`, `unsupported`, or `skipped`
- observation count and best observed price when available
- unsupported reason or skipped reason when unavailable
- evidence references

Live probing from `dates scan` is never implicit. Agents must pass
`--live-probe --max-probes <N>` to open Google Flights for cache misses, and
may further bound each probe with `--probe-timeout-seconds` and choose
`--browser-mode`. When the probe limit is exhausted, remaining pairs stay in
`pair_coverage` as `skipped` with `reason: "max_live_probes_reached"` instead
of being guessed or omitted.

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

`itinerary select` may return top-level `selected_outbound`, `selected_return`,
`booking_options`, and `itinerary` fields after it reaches a Google Flights
booking-summary URL. These fields come from selected row text and the visible
booking-summary snapshot; missing optional fields remain absent/null rather
than guessed. `selected_return` is also `null` for one-way selections where
Google transitions directly from the selected outbound row to booking summary.

Terminal information was not found in the selected itinerary evidence. The CLI
must output `terminal_info.status = "not_found"` or omit the field with an
explicit absence reason; it must not invent terminal names or terminal-change
warnings.

Current default-validation support includes service-level replay over redacted
TDD evidence plus fake-adapter stop-state tests for live `itinerary inspect`.
Actual live inspection remains an explicit browser-orchestration step and must
keep checkout, login, payment, and personal-data flows as stop boundaries.

## Task-Specific Reports

The maintained CLI must stay generic. Trip-specific reports belong in external
project scripts that stitch together the stable command families, the same way
an agent would compose `gh --help`, `cdp --help`, or `docsearch --help`.

A report script may:

- generate one window intent and a concrete date-pair JSON array;
- run `gflights search --input-json <intents.json> --project-root ~/.gflights
  --concurrency 3 --json` for bounded live probing;
- run `gflights dates scan` for cache-first date-window coverage;
- run `gflights itinerary select --search-url <url> --preferred-carrier
  "<carrier>" --json` only when an evidence-backed booking-summary URL is
  needed;
- run `gflights itinerary inspect --booking-url <url> --json` for visible
  selected-itinerary details.

The report reducer is outside this repository. It must not be added as a
first-party CLI command unless a future checked-in contract proves the workflow
is generic and reusable. Reports must show Google Flights search URLs whenever
the current query state can derive them. Google Flights booking-summary URLs
are evidence-only and must be shown as `not captured` unless row selection
actually reached `/travel/flights/booking`.

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
  evidence comments and TDD evidence links.
- Do not require full encoder support when browser/UI probing can produce the
  state safely.
- Any encoder/decoder promoted into the implementation must have TDD tests for
  raw value, decoded wire paths, confidence, and stale behavior.
- If checked evidence contradicts a hypothesis, return `stale_evidence` or
  `unsupported`; do not fall back to guessing.

Implementation scope admitted from the strong evidence set:

- The live search encoded path may construct populated `tfs` and open
  `/travel/flights/search` for concrete one-way or round-trip searches with one
  concrete departure date, one concrete return date when required,
  evidence-backed route endpoints, observed passenger category values, and
  observed economy/business cabin values.
- The live search shell may construct short sort `tfu` for observed non-default
  sort values: price, departure time, arrival time, duration, and emissions.
  This is a volatile Google ordering hint, not the final ranking contract.
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
- query/protobuf decode interpretation over checked TDD bytes
- JSON output shaping and JSON Schema generation

Service layer:

- parse or normalize `SearchIntent` dicts
- orchestrate route resolution, date scans, concrete searches, itinerary
  inspection, service-level TDD replay, and codec diagnostics
- accept primitive dicts and return validated output dicts
- depend on fakeable adapters for browser, filesystem, checked test data, and
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

- Default live browser mode: `headed`.
- Default browser budget: five total page tabs. Refuse to open another target
  when doing so would exceed that budget; prefer exact-target reuse.
- `headless` is available only for explicitly requested maintenance or recovery
  work and is not the normal-search fallback.
- Record browser mode, target page id, URL, command, run id, and artifacts for
  every live capture.
- Record UUID v4 task trace evidence for live browser work. Batch/fanout
  commands use one root task id, one child task id per search or selection
  attempt, and one managed-tab leaf task id for each owned Chrome target.
- Close CLI-managed page targets after evidence capture and record
  `managed-tab-close.json` only when the target id maps to the current
  managed-tab task; cleanup failures are warnings/artifacts, not replacements
  for valid domain results.
- Preflight may close stale `data-cdp-health` diagnostic tabs by exact diagnostic
  URL/title match and records those closes separately from Google Flights domain
  cleanup.
- `cdp --browser-mode headed pages --json` is an accepted tab-discovery surface
  for headed exploration.
- When an explicit reuse option navigates an existing target, record tab-budget
  evidence and skip target close because the CLI did not create that tab.

Default validation must not hit live Google Flights. Normal CLI search may use
the live Google Flights path; repository validation remains offline unless a
live target or marker is selected.

## App State And Artifact Policy

Default use:

- Runtime state lives under `~/.gflights` unless
  `GFLIGHTS_SEARCH_HOME` or an explicit init path overrides it.
- `project init` creates `config.json`, `cache/cache.sqlite`, `artifacts/`,
  and `runs/` under the app-state root.
- `config.json` sets live Google Flights search as the default and records a
  configurable maximum age for cached flight prices. The default is six hours;
  `GFLIGHTS_CACHE_MAX_AGE_SECONDS` may override it for a run.
- `cache/cache.sqlite` uses the policy in `docs/cache-layer-policy.md`: SQLite WAL
  mode, a busy timeout, schema version tracking, and date-scan lookup indexes.
- Runtime evidence uses task-scoped run directories.
- Raw browser/network/storage artifacts must not be committed without redaction
  and review.
- Normal validation uses fake adapters and repository TDD replay assets, not
  installed replay commands.

Every command that creates artifacts must return their paths in JSON.

## Error And Stop States

| State | Required behavior |
|---|---|
| `no_results` | Return structured status; scenario evidence pending, so live-specific details stay experimental. |
| `capability_not_found` | Return unsupported/not_found with evidence reference. |
| `blocked` | Stop and record safe evidence without bypassing. |
| `login_required` | Stop; do not attempt login. |
| `payment_or_booking_boundary` | Stop before provider transaction flow. |
| `ambiguous_autocomplete` | Return candidates and exit 2. |
| `unsupported_combination` | Return status and unsupported field list. |
| `weak_codec` | Return unsupported/experimental unless checked evidence supports the behavior. |
| `stale_evidence` | Return exit 5 and point to codec maintenance docs. |
| `tool_error` | Return exit 6 with command, adapter, and repair hints. |

## TDD Acceptance For First Implementation

Slice 07 must write red tests before implementation code for:

- CLI help exposing atomic commands
- JSON Schema export
- app-state init
- JSON array input validation and one-output-per-search-intent behavior
- service-level replay over repository TDD assets
- date-window scan output shape
- headed default and five-tab browser budget
- codec decode confidence output
- deterministic exit codes
- self-contained production and editable/symlinked `uv tool install` smoke behavior

The first green implementation may use repository TDD assets and fake adapters.
Live Google Flights is the default CLI value path; smoke tests may still be
isolated by marker or task-scoped runs.

## Evidence Appendix

Required references by spec area:

- capability admission and rows: `docs/detailed-cli-spec.md`
- query params and protobuf hypotheses: `docs/query-state-maintenance.md`
- browser surfaces and safety rules: `docs/browser-evidence-policy.md`
- repository TDD replay and redaction rules: `docs/fixture-contract.md`
- implementation test contracts: `docs/agentic-e2e.md`

Any future spec change that adds support for a deferred capability must add:

- capability matrix update
- probe scenario row or update
- checked-in redacted TDD asset or checked-in evidence summary
- one-variable mutation or documented edge/counterexample
- confidence class
- functional-core/service/shell owner
- test or validation signal
