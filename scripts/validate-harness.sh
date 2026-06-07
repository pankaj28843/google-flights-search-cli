#!/usr/bin/env bash
set -euo pipefail

required_files=(
  "AGENTS.md"
  "README.md"
  "docs/detailed-cli-spec.md"
  "docs/schema-and-json-contracts.md"
  "docs/cache-layer-policy.md"
  "docs/flight-search-first-principles.md"
  "docs/query-state-maintenance.md"
  "docs/cdp-usage-discipline.md"
  "docs/browser-evidence-policy.md"
  "docs/fixture-contract.md"
  "docs/review-and-cleanup.md"
  "docs/agentic-e2e.md"
  "fixtures/README.md"
  "artifacts/README.md"
)

for path in "${required_files[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "missing required harness file: $path" >&2
    exit 1
  fi
done

selector_scan=/tmp/gflights-selector-scan.txt
: > "$selector_scan"
grep -RInF "querySelector(" docs README.md AGENTS.md >> "$selector_scan" || true
grep -RInF "nth-child" docs README.md AGENTS.md >> "$selector_scan" || true
grep -RInE '(\\.[A-Za-z0-9_-]{12,}|#[A-Za-z0-9_-]{12,}|\\[[A-Za-z-]+=[^]]+\\])' docs README.md AGENTS.md >> "$selector_scan" || true
if [[ -s "$selector_scan" ]]; then
  echo "durable docs contain selector-like authoritative language:" >&2
  cat "$selector_scan" >&2
  exit 1
fi

if ! grep -RIn "no booking\\|booking boundary\\|payment" docs README.md AGENTS.md >/dev/null; then
  echo "missing booking/payment safety boundary in docs" >&2
  exit 1
fi

if ! grep -RIn "default validation must not\\|Default validation must not" docs README.md AGENTS.md >/dev/null; then
  echo "missing no-live-default-validation rule" >&2
  exit 1
fi

if ! grep -RIn "gflights schema --model search-intent --json" docs/schema-and-json-contracts.md >/dev/null; then
  echo "schema contract doc must include the search-intent schema command" >&2
  exit 1
fi

for confidence in proven strong weak unknown rejected; do
  if ! grep -RIn "$confidence" docs/schema-and-json-contracts.md docs/query-state-maintenance.md >/dev/null; then
    echo "missing confidence class in schema/query docs: $confidence" >&2
    exit 1
  fi
done

for status in unsupported deferred ambiguous blocked stale_fixture tool_error; do
  if ! grep -RIn "$status" docs/schema-and-json-contracts.md docs/detailed-cli-spec.md >/dev/null; then
    echo "missing status contract in schema/spec docs: $status" >&2
    exit 1
  fi
done

for stop_state in access_denied login_required unusual_traffic payment_or_booking_boundary personal_data_required permission_required; do
  if ! grep -RIn "$stop_state" docs/schema-and-json-contracts.md docs/browser-evidence-policy.md docs/detailed-cli-spec.md >/dev/null; then
    echo "missing stop-state contract in docs: $stop_state" >&2
    exit 1
  fi
done

echo "harness validation passed"
