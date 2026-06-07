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

By default, runtime state lives under `~/.gflights-search`. Tests and isolated
runs may override that location with `GFLIGHTS_SEARCH_HOME` or by passing
`--path` to `project init`. The state root contains:

- `config.json`
- `cache.sqlite`
- `artifacts/`
- `fixtures/`
- `runs/`

`config.json` sets `live_google_flights_by_default: true`,
`google_flights_live_env: "1"`, and `cache_max_age_seconds: 21600`. The SQLite
cache stores flight-price observations so a route/date/currency combination can
reuse only data that is at most six hours old. Commands that write files should
return artifact, fixture, database, or run paths in JSON.

## Live Evidence Command

Live evidence capture:

```bash
gflights search --input-json <intent.json> --browser-mode headless --json
```

The command accepts either one `SearchIntent` object or a JSON array of
`SearchIntent` objects. Single-object input returns one JSON object. JSON-array
input returns a JSON array in the same order, with one output object per input
intent. The command opens Google Flights with language and currency context,
writes a state-local `runs/<run-id>/` evidence bundle, and returns one of:

- `ok` with weak confidence and primary result rows when visible text contains
  parseable primary search rows plus a `cache.price_observations_written`
  count for the fresh SQLite observations written from priced rows,
- `experimental` with weak confidence when cdp evidence capture succeeds but
  no primary rows are extractable yet, with zero price observations written, or
- `blocked` with exit code `4` and headed fallback guidance when a browser stop
  state appears.

`search` uses live cdp by default when `--offline-fixtures` is absent.
For concrete, evidence-backed route/date/trip/cabin/passenger/sort inputs, live
search first opens a populated Google Flights URL using fixture-backed `tfs`
and, when needed, short sort `tfu` query state. The output includes
`query_population.status = "encoded"`, confidence, populated parameter names,
source surfaces, and evidence references. Inputs outside the supported encoded
surface still open the Google Flights shell and return
`query_population.status = "unsupported"` with an actionable unsupported field
such as `departure_window`, `destination`, `cabin`, or `trip_type`.
`--offline-fixtures` is the explicit deterministic replay path. `--live-cdp`
remains accepted as a compatibility flag. `--live-form` additionally attempts
fake-tested, evidence-scoped form interactions before capture; live form mode is
experimental, and nested itinerary/provider extraction remains deferred.

SQLite cache payloads contain sanitized price-observation fields derived from
parsed result rows. They must not store raw browser stdout, raw network request
payloads, cookies, storage, or full cdp JSON artifacts.

## Route Resolve Command

Deterministic route autocomplete replay:

```bash
gflights route resolve --input-text <text> --offline-fixtures <fixture-dir> --json
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

When `--offline-fixtures` is omitted, live route probing currently exits `3`
with `status: "unsupported"` / `route.resolve.live: deferred`. That behavior
stays explicit until live autocomplete stop-state handling is fake-tested.

## Date Scan Command

Date-window scans:

```bash
gflights dates scan --input-json <intent.json> --project-root <state-root> --json
```

When `--project-root` is supplied, `dates scan` expands the requested date
windows and checks the state SQLite cache for fresh price observations before
attempting or requiring any live probe. Output includes `coverage_counts` and
`pair_coverage`; every generated date pair is represented as `fresh_cache`,
`probed`, `unsupported`, or `skipped` with evidence. `ranked_pairs` may contain
only pairs backed by a fresh cache row or a probe result with a visible price.

`--offline-fixtures` remains the deterministic bootstrap/replay path for the
older e2e contract. Default validation does not probe live Google Flights from
`dates scan`.

When `ranking_policy` is `comfort_aware_v1`, each ranked pair has a
`scoring_explanation` with `score`, `google_flights_filters_applied: false`,
and component entries for price, duration, stops, preferred-airline status,
senior-comfort weight, and emissions when visible.

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

## Live Itinerary Inspect

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
task-scoped.

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
