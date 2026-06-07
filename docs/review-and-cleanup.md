# Review And Cleanup

## Review Policy

Classify findings as:

- blocker
- high
- medium
- low
- question

Block on:

- unsupported Google Flights behavior presented as supported
- query/protobuf fields promoted without evidence
- browser side effects inside pure domain code
- raw unredacted browser/network/storage artifacts
- live Google Flights dependency in default validation
- exact selectors presented as durable behavior

## Pushback Protocol

An author may reject a finding only with evidence:

- spec citation
- fixture output
- validation command
- source document
- explicit user scope change

## Cleanup Targets

Regular cleanup should look for:

- stale fixtures
- obsolete codec hypotheses
- duplicate helpers
- generated artifacts committed by mistake
- selector-driven docs
- docs that no longer match behavior
- validation commands that rely on terminal scrollback
- stale `cdp` tabs left by live smoke or live evidence commands
- missing managed-tab close artifacts for live browser commands

## Safety Boundary

Do not automate login, booking, payment, personal-data entry, access-control
bypass, unusual-traffic bypass, or public release changes without explicit
approval.
