from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3

from gflights.app_state import CACHE_MAX_AGE_ENV, CACHE_SCHEMA_VERSION, PriceCache, init_app_state


def test_init_app_state_defaults_to_user_gflights_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("GFLIGHTS_SEARCH_HOME", raising=False)

    state = init_app_state()

    assert state.root == tmp_path / ".gflights"
    assert state.cache_root == tmp_path / ".gflights" / "cache"
    assert state.database_path == tmp_path / ".gflights" / "cache" / "cache.sqlite"
    assert state.run_root == tmp_path / ".gflights" / "runs"


def test_init_app_state_creates_config_and_sqlite_cache(tmp_path: Path) -> None:
    state = init_app_state(tmp_path)

    assert state.root == tmp_path
    assert state.config_path == tmp_path / "config.json"
    assert state.cache_root == tmp_path / "cache"
    assert state.database_path == tmp_path / "cache" / "cache.sqlite"
    assert state.run_root == tmp_path / "runs"
    assert state.config["live_google_flights_by_default"] is True
    assert state.config["google_flights_live_env"] == "1"
    assert state.config["cache_max_age_seconds"] == 6 * 60 * 60
    assert state.config["cache_root"] == str(tmp_path / "cache")
    assert state.config["database_path"] == str(tmp_path / "cache" / "cache.sqlite")

    config = json.loads((tmp_path / "config.json").read_text())
    assert config == state.config
    with sqlite3.connect(state.database_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        index_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert "flight_price_cache" in table_names
    assert "idx_flight_price_cache_dates" in index_names
    assert journal_mode == "wal"
    assert user_version == CACHE_SCHEMA_VERSION


def test_init_app_state_preserves_existing_config_values(tmp_path: Path) -> None:
    state = init_app_state(tmp_path)
    original = json.loads(state.config_path.read_text())
    original["browser_default_mode"] = "headed"
    original["cache_max_age_seconds"] = 8 * 60 * 60
    original["custom_note"] = "keep user configuration"
    state.config_path.write_text(json.dumps(original, indent=2) + "\n")

    reloaded = init_app_state(tmp_path)

    assert reloaded.config["browser_default_mode"] == "headed"
    assert reloaded.config["custom_note"] == "keep user configuration"
    assert reloaded.config["live_google_flights_by_default"] is True
    assert reloaded.config["cache_max_age_seconds"] == 8 * 60 * 60
    assert reloaded.cache_max_age_seconds == 8 * 60 * 60
    assert json.loads(state.config_path.read_text()) == reloaded.config


def test_init_app_state_allows_env_cache_age_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(CACHE_MAX_AGE_ENV, str(7 * 60 * 60))

    state = init_app_state(tmp_path)

    assert state.config["cache_max_age_seconds"] == 7 * 60 * 60
    assert state.cache_max_age_seconds == 7 * 60 * 60


def test_price_cache_returns_only_entries_at_most_six_hours_old(tmp_path: Path) -> None:
    state = init_app_state(tmp_path)
    cache = PriceCache(state.database_path)
    now = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)

    cache.put_price(
        cache_key="fresh-cph-del",
        query_id="cph-del",
        departure_date="2026-10-01",
        return_date="2026-11-24",
        currency="EUR",
        price_amount=702,
        price_payload={"amount": 702, "currency": "EUR"},
        captured_at=now - timedelta(hours=5, minutes=59),
        source_run_id="gf-fresh",
    )
    cache.put_price(
        cache_key="stale-cph-del",
        query_id="cph-del",
        departure_date="2026-10-01",
        return_date="2026-11-24",
        currency="EUR",
        price_amount=699,
        price_payload={"amount": 699, "currency": "EUR"},
        captured_at=now - timedelta(hours=6, minutes=1),
        source_run_id="gf-stale",
    )

    fresh = cache.get_fresh_price("fresh-cph-del", now=now)
    stale = cache.get_fresh_price("stale-cph-del", now=now)

    assert fresh is not None
    assert fresh["price_amount"] == 702
    assert fresh["age_seconds"] == 5 * 60 * 60 + 59 * 60
    assert fresh["price_payload"] == {"amount": 702, "currency": "EUR"}
    assert stale is None
