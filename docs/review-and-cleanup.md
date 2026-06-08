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
- legacy compatibility paths that no checked-in contract still requires
- obsolete codec hypotheses
- duplicate helpers
- generated artifacts committed by mistake
- checked-in docs, source, tests, fixtures, or agent instructions that cite
  capsule scratch files, temporary research runs, personal home-directory
  projects, or other artifacts not checked into this repository
- selector-driven docs
- docs that no longer match behavior
- validation commands that rely on terminal scrollback
- stale `cdp` tabs left by live smoke or live evidence commands
- missing managed-tab close artifacts for live browser commands

When outside evidence informs a durable rule, copy the relevant conclusion into
a checked-in doc or redacted fixture first, then cite only that repo-relative
file from repo docs, source, tests, and fixture metadata.

## Forward-Only Bug Fixes

This project does not maintain legacy Google Flights behavior for its own sake.
When a bug is caused by current Google Flights DOM, URL, settlement, or visible
text behavior, update the docs/contracts and fixtures to describe today's
behavior, then change the code to match. Delete stale branches, helpers, CLI
options, and fixture assumptions when they no longer describe supported
behavior. Compatibility shims need an explicit checked-in contract; otherwise
keep the codebase nimble and current.

## Safety Boundary

Do not automate login, booking, payment, personal-data entry, access-control
bypass, unusual-traffic bypass, or public release changes without explicit
approval.
