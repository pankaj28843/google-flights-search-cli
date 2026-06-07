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
