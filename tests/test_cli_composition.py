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


def test_operational_status_reports_database_state(tmp_path: Path) -> None:
    database = tmp_path / "status.db"
    with compose_application(database, providers=providers()) as application:
        application.engine.refresh(date(2026, 7, 22))
        status = application.status.read(application.calendar.market_availability())
        assert status.database_path == database.resolve()
        assert status.latest_quote_date == date(2026, 7, 22)
        assert status.latest_daily_snapshot == date(2026, 7, 22)
        assert status.latest_position_snapshot == date(2026, 7, 22)
        assert status.holdings_count == 5
        assert status.liabilities_count == 2
        assert status.schema_version == 3
        assert status.database_size_bytes > 0


def test_offline_operational_verification_passes(tmp_path: Path) -> None:
    database = tmp_path / "verify.db"
    with compose_application(database, providers=providers()) as application:
        report = application.verification.run()
        assert report.failed is False
        assert {check.name for check in report.checks} >= {
            "Configuration",
            "Database",
            "Schema",
            "Holdings",
            "Liabilities",
            "TWSE endpoint",
            "TPEx endpoint",
            "Market Calendar",
            "Market Data Engine",
        }


def test_offline_verification_fails_when_official_data_is_empty(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verify-failure.db"
    failing_providers = (
        StaticProvider(Market.TWSE, []),
        StaticProvider(
            Market.TPEX,
            [{"Date": "1150722", "SecuritiesCompanyCode": "8299", "Close": "1"}],
        ),
    )
    with compose_application(database, providers=failing_providers) as application:
        report = application.verification.run()
        assert report.failed is True
        twse = next(check for check in report.checks if check.name == "TWSE endpoint")
        assert twse.level.value == "FAIL"


def test_verification_warns_when_official_market_dates_disagree(
    tmp_path: Path,
) -> None:
    database = tmp_path / "verify-warning.db"
    mixed_providers = (
        StaticProvider(
            Market.TWSE,
            [{"Date": "1150721", "Code": "2330", "ClosingPrice": "1"}],
        ),
        StaticProvider(
            Market.TPEX,
            [{"Date": "1150722", "SecuritiesCompanyCode": "8299", "Close": "1"}],
        ),
    )
    with compose_application(database, providers=mixed_providers) as application:
        report = application.verification.run()
        calendar = next(
            check for check in report.checks if check.name == "Market Calendar"
        )
        assert report.failed is False
        assert calendar.level.value == "WARN"
        assert "waiting for synchronization" in calendar.detail
