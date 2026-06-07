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

## Project State

Per-project setup:

```bash
gflights project init --path <project-root> --json
```

This creates:

- `.gflights/config.json`
- `.gflights/artifacts/`
- `.gflights/fixtures/`
- `.gflights/runs/`

The same globally installed CLI must be usable across projects. Commands that
write files should return artifact, fixture, or run paths in JSON.

## Live Evidence Command

Opt-in live evidence capture:

```bash
gflights search --input-json <intent.json> --live-cdp --project-root <project-root> --browser-mode headless --json
```

The command opens Google Flights with language and currency context, writes a
project-local `.gflights/runs/<run-id>/` evidence bundle, and returns either:

- `experimental` with weak confidence when cdp evidence capture succeeds, or
- `blocked` with exit code `4` and headed fallback guidance when a browser stop
  state appears.

This command must not be run unless `--live-cdp` is explicit. It does not claim
durable itinerary result extraction yet.

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
