.PHONY: validate lint format-check test live-cdp live-google-flights

validate:
	./scripts/validate-harness.sh

lint:
	uv run ruff check .

format-check:
	uv run ruff format --check .

test:
	uv run pytest

live-cdp:
	GFLIGHTS_RUN_LIVE_CDP=1 uv run pytest -m live_cdp

live-google-flights:
	GFLIGHTS_RUN_GOOGLE_FLIGHTS_LIVE=1 uv run pytest -m live_google_flights
