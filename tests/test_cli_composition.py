"""Offline integration tests for CLI composition and persistence modes."""

import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest

from config import get_settings
from domain import Market
from market_data.transport import JSONRecord
from pams.composition import compose_application, resolve_database_path
from services import DuplicateSnapshotError


class StaticProvider:
    """Official-shape deterministic provider for composition tests."""

    def __init__(self, market: Market, records: Sequence[JSONRecord]) -> None:
        self.market = market
        self.source = f"{market.value}-test"
        self.records = records

    def fetch(self) -> Sequence[JSONRecord]:
        return self.records


def providers() -> tuple[StaticProvider, ...]:
    return (
        StaticProvider(
            Market.TWSE,
            [
                {"Date": "1150722", "Code": symbol, "ClosingPrice": "100"}
                for symbol in ("0050", "2027", "2330")
            ],
        ),
        StaticProvider(
            Market.TPEX,
            [
                {
                    "Date": "1150722",
                    "SecuritiesCompanyCode": symbol,
                    "Close": "100",
                }
                for symbol in ("3293", "8299")
            ],
        ),
    )


def row_count(connection: sqlite3.Connection, table: str) -> int:
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_configured_database_path_is_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured = tmp_path / "configured.db"
    monkeypatch.setenv("PAMS_DATABASE_URL", f"sqlite:///{configured.as_posix()}")
    get_settings.cache_clear()
    try:
        assert resolve_database_path() == configured.resolve()
    finally:
        get_settings.cache_clear()


def test_override_database_path_and_bootstrap_are_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "override.db"
    with compose_application(database, providers=providers()) as first:
        assert first.database_path == database.resolve()
        assert first.seeded is True
        assert row_count(first.connection, "holdings") == 5
    with compose_application(database, providers=providers()) as second:
        assert second.seeded is False
        assert row_count(second.connection, "holdings") == 5
        assert row_count(second.connection, "liabilities") == 2


def test_dry_run_validates_but_does_not_write_market_tables(tmp_path: Path) -> None:
    database = tmp_path / "dry-run.db"
    with compose_application(database, providers=providers()) as application:
        result = application.engine.preview(date(2026, 7, 22))
        assert len(result.quotes) == 5
        assert row_count(application.connection, "price_quotes") == 0
        assert row_count(application.connection, "daily_snapshots") == 0
        assert row_count(application.connection, "position_snapshots") == 0


def test_persisted_update_writes_all_grains_and_protects_duplicate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "persisted.db"
    with compose_application(database, providers=providers()) as application:
        application.engine.refresh(date(2026, 7, 22))
        assert row_count(application.connection, "price_quotes") == 5
        assert row_count(application.connection, "daily_snapshots") == 1
        assert row_count(application.connection, "position_snapshots") == 5
        with pytest.raises(DuplicateSnapshotError):
            application.engine.refresh(date(2026, 7, 22))
