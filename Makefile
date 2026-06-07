UV ?= uv
UV_TOOL_PACKAGE ?= .
UV_TOOL_LINK_MODE ?= symlink

.PHONY: validate lint format-check test install install-tool install-editable install-tool-editable live-cdp live-google-flights india-trip-plan live-india-trip-plan

validate:
	./scripts/validate-harness.sh

lint:
	$(UV) run ruff check .

format-check:
	$(UV) run ruff format --check .

test:
	$(UV) run pytest

install: install-tool

install-tool:
	$(UV) tool install --force $(UV_TOOL_PACKAGE)

install-editable: install-tool-editable

install-tool-editable:
	$(UV) tool install --force --editable --link-mode $(UV_TOOL_LINK_MODE) $(UV_TOOL_PACKAGE)

live-cdp:
	GFLIGHTS_RUN_LIVE_CDP=1 $(UV) run pytest -m live_cdp

live-google-flights:
	$(UV) run pytest -m live_google_flights

india-trip-plan:
	$(UV) run gflights trip india --json

live-india-trip-plan:
	$(UV) run gflights trip india --execute-live --json
