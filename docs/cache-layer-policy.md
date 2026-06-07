# Cache Layer Policy

This project uses a local SQLite cache for flight-price observations under the
app-state root. The default app-state root is `~/.gflights-search`; tests and
task-scoped runs may override it with `GFLIGHTS_SEARCH_HOME` or command options.

## Source Surface

Research used for this policy:

- SQLite docs, `sqlite` TechDocs tenant from `https://www.sqlite.org`.
- Python `sqlite3` docs from `https://docs.python.org/3/library/sqlite3.html`.
- SQLAlchemy SQLite dialect docs from `https://docs.sqlalchemy.org`.
- Alembic SQLite batch migration docs from `https://alembic.sqlalchemy.org`.
- SQLModel docs extracted from `https://sqlmodel.tiangolo.com`.

Local dependency surface: the production CLI currently depends on Pydantic and
Typer, not SQLAlchemy, SQLModel, or Alembic.

## Current Decision

Use Python's standard-library `sqlite3` module for the current cache layer.

The cache stores a narrow, local table of sanitized flight-price observations.
The active operations are simple writes, exact-key freshness reads, and
date-window lookup reads. That does not justify adding an ORM or migration
framework yet.

Current implementation requirements:

- `cache.sqlite` lives in the app-state root.
- `config.json` contains `cache_max_age_seconds`.
- Default freshness is six hours (`21600` seconds), within the 6-8 hour default
  window.
- `GFLIGHTS_CACHE_MAX_AGE_SECONDS` may override freshness for the current run.
- Existing `config.json` freshness must be preserved unless the env override is
  present.
- Live search writes sanitized price observations when visible result rows are
  parsed.
- Live/date workflows must treat cached rows as fresh only when
  `age_seconds <= cache_max_age_seconds`.
- The cache must not store raw browser stdout, raw network payloads, cookies,
  storage, or full cdp JSON artifacts.

## SQLite Settings

The cache database should be initialized with:

- `PRAGMA journal_mode=WAL` for local read/write concurrency.
- `PRAGMA busy_timeout = 5000` on connections.
- `PRAGMA user_version = 1` for lightweight schema-version tracking.
- An index matching date-scan lookups:
  `(query_id, departure_date, return_date, currency, price_amount, captured_at)`.

SQLite WAL is appropriate because this is a local app-state database and the
CLI may read cached observations while live search writes new ones. Do not use
WAL for app-state roots on network filesystems.

## Dependency Triggers

Do not add SQLAlchemy just to avoid writing straightforward SQL. Add SQLAlchemy
Core or ORM only when at least one of these becomes true:

- cache queries become complex enough that SQL composition is safer than
  handwritten statements,
- the cache grows into multiple related tables with shared transaction
  boundaries,
- a second database backend becomes a real target, or
- profiling shows handwritten connection/query management is the bottleneck.

Do not add SQLModel for the current cache. SQLModel is useful when one model
definition should serve Pydantic validation and SQLAlchemy table mapping. This
project already has Pydantic domain models, and the cache stores observation
records plus JSON payloads rather than durable domain entities.

Do not add Alembic for additive cache setup. Use `CREATE TABLE IF NOT EXISTS`,
`CREATE INDEX IF NOT EXISTS`, and `PRAGMA user_version` while schema changes are
small and repairable. Add Alembic only when cache migrations need ordered
multi-version upgrades, destructive table changes, or migration review history.
If Alembic is added for SQLite, use batch migrations deliberately because SQLite
has limited `ALTER TABLE` support.

## Cache Key And Payload Rules

Cache keys must include enough search semantics to prevent stale or mismatched
recommendations:

- query id
- route evidence or visible route codes
- departure date
- return date when present
- currency
- cabin
- passenger party
- result identity when storing multiple rows for one search

Payloads may contain normalized result fields needed for ranking and display:
price, currency, carriers, duration, stops, layovers, emissions, baggage
summary, and source run id. Payloads must remain JSON-serializable and sanitized.

## Validation

Run focused cache checks after changing this layer:

```bash
uv run pytest tests/unit/test_app_state.py tests/unit/test_services.py -k cache
uv run pytest tests/unit/test_live_search.py::test_live_search_writes_extracted_result_prices_to_sqlite_cache
uv run pytest tests/e2e/test_agentic_cli_contract.py::test_dates_scan_project_root_uses_fresh_cache_without_live_browser
```

Run the full offline suite before merging:

```bash
make validate
uv run pytest -m 'not live_google_flights and not live_cdp'
```
