.PHONY: validate lint format-check test

validate:
	./scripts/validate-harness.sh

lint:
	uv run ruff check .

format-check:
	uv run ruff format --check .

test:
	uv run pytest
