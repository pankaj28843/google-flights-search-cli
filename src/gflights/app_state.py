"""User-local application state for gflights."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

APP_HOME_ENV = "GFLIGHTS_SEARCH_HOME"
GOOGLE_FLIGHTS_LIVE_ENV = "GFLIGHTS_RUN_GOOGLE_FLIGHTS_LIVE"
CACHE_MAX_AGE_ENV = "GFLIGHTS_CACHE_MAX_AGE_SECONDS"
DEFAULT_CACHE_MAX_AGE_SECONDS = 6 * 60 * 60
CACHE_SCHEMA_VERSION = 1
SQLITE_BUSY_TIMEOUT_MS = 5_000


@dataclass(frozen=True)
class AppState:
    root: Path
    config_path: Path
    cache_root: Path
    database_path: Path
    run_root: Path
    artifact_root: Path
    config: dict[str, Any]
    cache_max_age_seconds: int


class PriceCache:
    def __init__(
        self,
        database_path: Path,
        *,
        max_age_seconds: int = DEFAULT_CACHE_MAX_AGE_SECONDS,
    ) -> None:
        self.database_path = database_path
        self.max_age_seconds = max_age_seconds
        _init_database(database_path)

    def put_price(
        self,
        *,
        cache_key: str,
        query_id: str,
        departure_date: str,
        return_date: str | None,
        currency: str,
        price_amount: int | float,
        price_payload: dict[str, Any],
        captured_at: datetime,
        source_run_id: str,
    ) -> None:
        captured_at_utc = _as_utc(captured_at)
        with _connect_database(self.database_path) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO flight_price_cache (
                    cache_key,
                    query_id,
                    departure_date,
                    return_date,
                    currency,
                    price_amount,
                    price_payload,
                    captured_at,
                    source_run_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cache_key,
                    query_id,
                    departure_date,
                    return_date,
                    currency,
                    price_amount,
                    json.dumps(price_payload, sort_keys=True),
                    captured_at_utc.isoformat(),
                    source_run_id,
                ),
            )

    def get_fresh_price(
        self,
        cache_key: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        now_utc = _as_utc(now or datetime.now(UTC))
        with _connect_database(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT
                    cache_key,
                    query_id,
                    departure_date,
                    return_date,
                    currency,
                    price_amount,
                    price_payload,
                    captured_at,
                    source_run_id
                FROM flight_price_cache
                WHERE cache_key = ?
                """,
                (cache_key,),
            ).fetchone()
        if row is None:
            return None

        captured_at = datetime.fromisoformat(row["captured_at"])
        age_seconds = int((now_utc - _as_utc(captured_at)).total_seconds())
        if age_seconds > self.max_age_seconds:
            return None

        return {
            "cache_key": row["cache_key"],
            "query_id": row["query_id"],
            "departure_date": row["departure_date"],
            "return_date": row["return_date"],
            "currency": row["currency"],
            "price_amount": row["price_amount"],
            "price_payload": json.loads(row["price_payload"]),
            "captured_at": row["captured_at"],
            "source_run_id": row["source_run_id"],
            "age_seconds": age_seconds,
        }

    def get_fresh_prices_for_dates(
        self,
        *,
        query_id: str,
        departure_date: str,
        return_date: str | None,
        currency: str,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        now_utc = _as_utc(now or datetime.now(UTC))
        if return_date is None:
            return_filter = "return_date IS NULL"
            params: tuple[Any, ...] = (query_id, departure_date, currency)
        else:
            return_filter = "return_date = ?"
            params = (query_id, departure_date, currency, return_date)
        with _connect_database(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""
                SELECT
                    cache_key,
                    query_id,
                    departure_date,
                    return_date,
                    currency,
                    price_amount,
                    price_payload,
                    captured_at,
                    source_run_id
                FROM flight_price_cache
                WHERE query_id = ?
                  AND departure_date = ?
                  AND currency = ?
                  AND {return_filter}
                ORDER BY price_amount ASC, captured_at DESC
                """,
                params,
            ).fetchall()

        fresh_rows: list[dict[str, Any]] = []
        for row in rows:
            captured_at = datetime.fromisoformat(row["captured_at"])
            age_seconds = int((now_utc - _as_utc(captured_at)).total_seconds())
            if age_seconds > self.max_age_seconds:
                continue
            fresh_rows.append(
                {
                    "cache_key": row["cache_key"],
                    "query_id": row["query_id"],
                    "departure_date": row["departure_date"],
                    "return_date": row["return_date"],
                    "currency": row["currency"],
                    "price_amount": row["price_amount"],
                    "price_payload": json.loads(row["price_payload"]),
                    "captured_at": row["captured_at"],
                    "source_run_id": row["source_run_id"],
                    "age_seconds": age_seconds,
                }
            )
        return fresh_rows


def init_app_state(root: Path | None = None) -> AppState:
    state_root = (root or default_app_state_root()).expanduser().resolve()
    cache_root = state_root / "cache"
    artifact_root = state_root / "artifacts"
    run_root = state_root / "runs"
    for directory in (cache_root, artifact_root, run_root):
        directory.mkdir(parents=True, exist_ok=True)

    database_path = cache_root / "cache.sqlite"
    config_path = state_root / "config.json"
    _init_database(database_path)
    ensure_live_environment()

    existing_config = _load_existing_config(config_path)
    cache_max_age_seconds = _configured_cache_max_age_seconds(existing_config)
    defaults = {
        "version": 1,
        "browser_default_mode": "headless",
        "live_google_flights_by_default": True,
        "google_flights_live_env": "1",
        "cache_max_age_seconds": DEFAULT_CACHE_MAX_AGE_SECONDS,
    }
    for key in list(existing_config):
        if key.endswith("_root") and key not in defaults:
            existing_config.pop(key)
    config = {
        **defaults,
        **existing_config,
        "version": 1,
        "live_google_flights_by_default": True,
        "google_flights_live_env": "1",
        "cache_max_age_seconds": cache_max_age_seconds,
        "cache_root": str(cache_root),
        "database_path": str(database_path),
        "artifacts_root": str(artifact_root),
        "run_root": str(run_root),
    }
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    return AppState(
        root=state_root,
        config_path=config_path,
        cache_root=cache_root,
        database_path=database_path,
        run_root=run_root,
        artifact_root=artifact_root,
        config=config,
        cache_max_age_seconds=cache_max_age_seconds,
    )


def default_app_state_root() -> Path:
    configured = os.environ.get(APP_HOME_ENV)
    if configured:
        return Path(configured)
    return Path.home() / ".gflights"


def ensure_live_environment() -> None:
    os.environ[GOOGLE_FLIGHTS_LIVE_ENV] = "1"


def price_cache_for_state(state: AppState) -> PriceCache:
    return PriceCache(state.database_path, max_age_seconds=state.cache_max_age_seconds)


def _load_existing_config(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        return {}
    try:
        payload = json.loads(config_path.read_text())
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _configured_cache_max_age_seconds(config: dict[str, Any]) -> int:
    raw_value: Any = os.environ.get(CACHE_MAX_AGE_ENV)
    if raw_value is None:
        raw_value = config.get("cache_max_age_seconds", DEFAULT_CACHE_MAX_AGE_SECONDS)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_CACHE_MAX_AGE_SECONDS
    if value <= 0:
        return DEFAULT_CACHE_MAX_AGE_SECONDS
    return value


def _init_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect_database(database_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS flight_price_cache (
                cache_key TEXT PRIMARY KEY,
                query_id TEXT NOT NULL,
                departure_date TEXT NOT NULL,
                return_date TEXT,
                currency TEXT NOT NULL,
                price_amount REAL NOT NULL,
                price_payload TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                source_run_id TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_flight_price_cache_captured_at
            ON flight_price_cache(captured_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_flight_price_cache_dates
            ON flight_price_cache(
                query_id,
                departure_date,
                return_date,
                currency,
                price_amount,
                captured_at
            )
            """
        )
        connection.execute(f"PRAGMA user_version = {CACHE_SCHEMA_VERSION}")


def _connect_database(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        database_path,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
    )
    connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    return connection


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
