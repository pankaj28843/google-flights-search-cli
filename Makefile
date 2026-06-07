.PHONY: validate lint format-check red-test

validate:
	./scripts/validate-harness.sh

lint:
	uv run ruff check .

format-check:
	uv run ruff format --check .

red-test:
	uv run pytest
