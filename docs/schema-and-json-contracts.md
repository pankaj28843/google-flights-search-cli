# Schema And JSON Contracts

Agents should treat JSON Schema and JSON output as the stable interface. Help
text is for humans.

## Schema Commands

Current schema export:

```bash
gflights schema --model search-intent --json
```

The command returns a JSON Schema object for the canonical `SearchIntent`
contract. Future schemas should be added as new model names rather than by
changing the meaning of `search-intent`.

## App State

User-local setup:

```bash
gflights project init --path <app-state-root> --json
```

By default, runtime state lives under `~/.gflights`. Tests and isolated
runs may override that location with `GFLIGHTS_SEARCH_HOME` or by passing
`--path` to `project init`. The state root contains:

- `config.json`
- `cache/cache.sqlite`
- `artifacts/`
- `fixtures/`
- `runs/`

`config.json` sets `live_google_flights_by_default: true`,
`google_flights_live_env: "1"`, and `cache_max_age_seconds: 21600`. The SQLite
cache stores flight-price observations so a route/date/currency combination can
reuse only data that is at most six hours old. Commands that write files should
return artifact, fixture, cache, database, or run paths in JSON.
Users may set `cache_max_age_seconds` in `config.json` or
`GFLIGHTS_CACHE_MAX_AGE_SECONDS` in the environment to use a different freshness
window for the current run.

## SearchIntent Route Endpoints

`SearchIntent.origin` and `SearchIntent.destination` are route endpoint objects:

```json
{
  "text": "Lucknow",
  "kind": "city_or_airport",
  "selected": {
    "text": "Lucknow, Uttar Pradesh, India",
    "kind": "city",
    "display_name": "Lucknow, Uttar Pradesh, India",
    "code_or_id": "/m/022tq4",
    "confidence": "strong",
    "evidence": {
      "source_surfaces": ["route-autocomplete-visible-text", "protobuf-decode-report"],
      "artifacts": ["route_autocomplete_choices_fixture.json", "decode-report.md"]
    }
  }
}
```

`selected` is optional for bare airport-code endpoints whose `text` is a
visible IATA code. It is required when a city/city-like endpoint must be encoded
into query state or when `search`/`dates scan` would otherwise need to guess
from ambiguous route text.

`RouteChoice` fields are stable JSON contract fields: `text`, `kind`,
`display_name`, `code_or_id`, `confidence`, and `evidence`. Airport choices must
carry a visible three-letter IATA `code_or_id`. Every selected `RouteChoice`
must carry non-empty `evidence.source_surfaces` and `evidence.artifacts`.
City choices may carry a reviewed decoded Google Flights city id only when that
id comes from a separate decode/protobuf evidence surface; otherwise the choice
remains usable for form interaction but not for direct query-state encoding.

## Live Evidence Command

Live evidence capture:

```bash
gflights search --input-json <intent.json> --browser-mode headless --json
gflights search --input-json <intents.json> --concurrency 3 --json
```

The command accepts either one `SearchIntent` object or a JSON array of
`SearchIntent` objects. Single-object input returns one JSON object. JSON-array
input returns a JSON array in the same order, with one output object per input
intent. Real CDP JSON-array live search may open up to `--concurrency` parallel
managed tabs, clamped to 1-5 and defaulting to 3. The command opens Google
Flights with language and currency context,
writes a state-local `runs/<run-id>/` evidence bundle, records
`managed-tab-close.json` when it owns a page target, and returns one of:

- `ok` with weak confidence and primary result rows when visible text contains
  parseable primary search rows plus a `cache.price_observations_written`
  count for the fresh SQLite observations written from priced rows,
- `no_results` with weak confidence when visible evidence explicitly reports no
  result rows,
- `unsupported` with exit code `3` when bounded evidence waits still produce an
  empty snapshot or `Loading results` instead of visible primary rows,
- `experimental` with weak confidence when cdp evidence capture succeeds but
  the no-row state is not classifiable yet, with zero price observations
  written, or
- `blocked` with exit code `4` and headed fallback guidance when a browser stop
  state appears.

`search` uses live cdp by default when `--offline-fixtures` is absent.
For concrete, evidence-backed route/date/trip/cabin/passenger/sort inputs, live
search first opens the Google Flights results surface
`/travel/flights/search` with fixture-backed `tfs` and, when needed, short sort
`tfu` query state. The output includes
`query_population.status = "encoded"`, confidence, populated parameter names,
source surfaces, and evidence references. City/city-like route inputs need a
`selected` route choice before this encoded path is allowed. Inputs outside the
supported encoded surface still open the Google Flights shell and return
`query_population.status = "unsupported"` with an actionable unsupported field
such as `departure_window`, `destination`, `cabin`, or `trip_type`.
`--offline-fixtures` is the explicit deterministic replay path. `--live-cdp`
remains accepted as a compatibility flag. `--live-form` additionally attempts
fake-tested, evidence-scoped form interactions before capture; live form mode is
experimental, and nested itinerary/provider extraction remains deferred.

Before snapshot extraction, live search waits for a terminal Google Flights page
condition such as visible fare rows, booking-summary text, no-results text, or a
browser stop state. It then enforces the configured minimum dwell for real CDP
runs and records network steadiness metadata. Footer-only currency text is not a
terminal result condition.

SQLite cache payloads contain sanitized price-observation fields derived from
parsed result rows. They must not store raw browser stdout, raw network request
payloads, cookies, storage, or full cdp JSON artifacts.

## Route Resolve Command

Route autocomplete resolution:

```bash
gflights route resolve --input-text <text> --offline-fixtures <fixture-dir> --json
gflights route resolve --input-text <text> --browser-mode headless --json
```

When a `route_autocomplete_choices` fixture contains a single reviewed match,
the command exits `0` with:

- `status: "ok"`
- `input_text`
- `selected`
- `choices`
- `confidence`
- `unsupported: []`
- `warnings: []`
- `evidence`

When a reviewed match has multiple candidates, the command exits `2` with
`status: "ambiguous"`, `selected: null`, `ambiguity_reason`, and ordered
candidate `choices`. Each choice must carry `text`, `kind`, `display_name`,
`code_or_id`, `confidence`, and `evidence`.

`search` and `dates scan` may call the same route-resolution service before
probing. If a `city_or_airport` endpoint has no `selected` choice and the
resolver finds multiple candidates, the command exits `2` with
`status: "ambiguous"` and `route_ambiguities`. Each ambiguity item names the
input field (`origin` or `destination`), input text, ambiguity reason, ordered
choices, and evidence. JSON-array search input preserves item order and reports
ambiguity per affected item instead of failing the whole batch.
If route fixtures cannot resolve a `city_or_airport` endpoint at all, `search`
and `dates scan` exit `3` with `status: "unsupported"` and the route resolver's
`unsupported` entries. They must not continue with date-pair generation or
offline search bootstrap behavior for an unproven route.

When `--offline-fixtures` is omitted, route resolution opens the live Google
Flights shell through cdp, fills the route autocomplete field, writes
task-scoped route evidence under the state root, and stops on browser safety
boundaries. When it owns a page target, its run bundle includes
`managed-tab-close.json`. Live route run ids include subsecond time plus a
random suffix so rapid route resolves do not share artifact directories.
The command waits past `about:blank` before filling route controls. Transient
`Cannot find default execution context` failures are retried once for route
wait/snapshot/fill steps. Route fill starts with the observed exact `Where to?`
label and then falls back to bounded alternate label attempts before returning a
structured `tool_error`.

If visible autocomplete evidence contains parser-backed choices, the command
returns the same `ok` or `ambiguous` shape as offline route resolution. Airport
rows may set `code_or_id` from a visible IATA code. City rows keep
`code_or_id: null` unless a separate reviewed decode surface supplies a stable
ID.

If live evidence is captured but no durable choice extraction is possible, the
command exits `3` with:

- `status: "unsupported"`
- `live_mode: true`
- `browser_mode`
- `target_url`
- `selected: null`
- `choices: []`
- `unsupported[0].field: "route.resolve.live_autocomplete_extraction"`
- `evidence.run_id`
- `evidence.artifacts`
- `evidence.source_surfaces`

If cdp reports a blocked/login/personal-data/payment/human stop state or a
browser resource-budget boundary, the command exits `4` with `stop_state`, empty
`choices`, and optional `fallback.recommended_browser_mode`.

## Date Scan Command

Date-window scans:

```bash
gflights dates scan --input-json <intent.json> --project-root <state-root> --json
gflights dates scan --input-json <intent.json> --project-root <state-root> --live-probe --max-probes <N> --json
```

When `--project-root` is supplied, `dates scan` expands the requested date
windows and checks the state SQLite cache for fresh price observations before
attempting or requiring any live probe. Output includes `coverage_counts` and
`pair_coverage`; every generated date pair is represented as `fresh_cache`,
`probed`, `unsupported`, or `skipped` with evidence. `ranked_pairs` may contain
only pairs backed by a fresh cache row or a probe result with a visible price.

Live Google Flights probing is opt-in. `--live-probe` requires `--max-probes`
greater than zero and may also use `--probe-timeout-seconds` and
`--browser-mode`. Each cache-miss pair is probed at most once until the limit is
exhausted. Remaining pairs are represented as `pair_coverage.status = "skipped"`
with `reason: "max_live_probes_reached"`, and generated live-search intent
artifacts are written under the app-state
`artifacts/date-scan-live-probes/` directory with concrete date keys to avoid
collisions.

`--offline-fixtures` remains the deterministic bootstrap/replay path for the
older e2e contract. Default validation does not probe live Google Flights from
`dates scan`.

When `dates scan` receives route fixtures and a route endpoint is ambiguous
without a selected route choice, it returns exit `2`, `status: "ambiguous"`,
and `route_ambiguities` before generating ranked pairs. This keeps date
recommendations from being built on unresolved Google Flights route semantics.
When route fixtures cannot resolve a route endpoint, `dates scan` returns exit
`3`, `status: "unsupported"`, `generated_pairs: 0`, and the resolver's
`unsupported` entries.

When `ranking_policy` is `comfort_aware_v1`, each ranked pair has a
`scoring_explanation` with `score`, `google_flights_filters_applied: false`,
and component entries for price, duration, stops, preferred-airline status,
senior-comfort weight, and emissions when visible. The same explanation also
contains `airline_preference` with `preferred_airlines`, `status`,
`matched_carriers`, `matched_carrier_count`, `visible_carriers`,
`local_ranking_preference_applied`, and
`google_flights_filters_applied: false` until a future Google Flights airline
filter implementation is proven.

The optional analysis adapter can flatten `dates scan` output into table rows.
If pandas is unavailable, it returns `status: "unavailable"` with an install
hint instead of importing pandas from the pure domain core or failing default
validation.

## Selected Itinerary Replay

Deterministic replay:

```bash
gflights evidence replay <selected_itinerary_visible_text_fixture.json> --json
```

For `fixture_type: "selected_itinerary_visible_text"`, replay returns
`status: "ok"` and an `itinerary` object when the redacted visible-text fixture
contains parseable selected-itinerary details. The current replay contract may
include:

- `summary` with route, trip type, cabin, passenger count, and total price
- `segments` with direction, airport codes, airline, flight number, aircraft,
  and duration text
- `layovers` with airport, city, duration, direction, and overnight flag when
  visible
- `baggage` with included baggage and warnings
- `emissions` from itinerary-level and per-segment visible text
- `cabin_facilities`
- `booking_options` with provider names, visible prices, and `Continue` as a
  booking-boundary control
- `baggage_policy_links` only when a visible policy link is safely decoded to
  an absolute URL in the redacted fixture metadata
- `terminal_info.status = "not_found"` when terminal text is absent
- `boundary` flags proving provider checkout, payment, login, and personal-data
  flows were not entered

Replay fixtures must not include raw browser URLs, raw network payloads, raw
storage payloads, cookies, target IDs, or unredacted cdp artifacts.

## Task-Specific Report Reducers

Task-specific reducers are not part of the maintained CLI JSON contract. Agents
should build reports by composing stable command outputs from `search`, `dates
scan`, `itinerary select`, and `itinerary inspect`.

External report scripts should preserve the generic command JSON envelopes and
write their own project-local summary files. A useful report may include
ranked date pairs, visible prices, source command paths, local airline
preference counts, and user-supplied travel context. It must include a Google
Flights search URL for each ranked option when query state can encode one.
Google Flights booking-summary URLs are evidence-only and must be reported as
`not captured` unless `gflights itinerary select` actually reached
`/travel/flights/booking`.

## Live Itinerary Select And Inspect

Selection from a search URL:

```bash
gflights itinerary select --search-url <google-flights-search-url> --preferred-carrier "<carrier>" --require-nonstop --json
```

The command returns `status`, `confidence`, `live_mode`, `browser_mode`,
`search_url`, `booking_url`, `selection.outbound`, `selection.return`,
`unsupported`, `warnings`, and `evidence`. It opens the search URL, waits for
visible fare rows, clicks only explicit Google Flights rows matching the
requested visible criteria, waits for the Google booking-summary state, and
returns a `booking_url` only when the current URL remains under
`/travel/flights/booking`. It stops before provider checkout, payment, login,
or personal-data entry.

Live selected-itinerary inspection:

```bash
gflights itinerary inspect --booking-url <google-flights-booking-url> --json
```

The command opens the supplied Google Flights booking URL, waits for the page,
captures a visible-text snapshot, and returns either:

- `ok` with weak confidence and an `itinerary` object when selected-itinerary
  detail fields are parseable from visible text,
- `experimental` with weak confidence when the snapshot is captured but no
  detail segments are parseable, or
- a stop-state status with exit code `4` when cdp reports checkout, payment,
  login, personal-data, access-control, unusual-traffic, or human-required
  boundaries.

The command must not click provider `Continue` controls, enter provider
checkout, enter payment or personal data, or attempt account login. Default
validation covers this with fake cdp adapters; live runs remain explicit and
task-scoped. When the command owns a page target, its run bundle includes
`managed-tab-close.json`.

## Status Values

Allowed command status values:

- `ok`
- `unsupported`
- `deferred`
- `ambiguous`
- `blocked`
- `no_results`
- `experimental`
- `stale_fixture`
- `tool_error`

Unsupported, deferred, ambiguous, blocked, stale, and experimental behavior must
be explicit in JSON. Commands must not silently approximate a weak Google
Flights capability.

## Confidence Values

Allowed confidence values:

- `proven`
- `strong`
- `weak`
- `unknown`
- `rejected`

Current Google Flights query/protobuf behavior must not emit `proven`.

`gflights codec decode --fixture <fixture.json> --json` returns the
fixture-backed hypothesis paths plus a `codec` object with generic observed wire
paths, decoded byte length, string anchors, and URL-safe-base64 round-trip
status. The generic decoder may validate `tfs`/`tfu` wire structure, but it must
not promote semantic field names by itself.

## Exit Codes

Stable exit-code classes:

- `0`: success
- `2`: invalid or ambiguous user input
- `3`: unsupported or deferred capability requested
- `4`: browser blocked, login required, unusual traffic, human required, or
  safety boundary reached
- `5`: stale fixture, stale codec hypothesis, or evidence mismatch
- `6`: toolchain or project configuration failure

## Stop States

Stop and return structured JSON before:

- `blocked`
- `access_denied`
- `login_required`
- `unusual_traffic`
- `human_required`
- `payment_or_booking_boundary`
- `personal_data_required`
- `permission_required`

The browser adapter may recommend headed mode after a headless stop state. It
must not bypass the stop state.
