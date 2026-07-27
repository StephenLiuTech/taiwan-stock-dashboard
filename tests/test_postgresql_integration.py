"""Real PostgreSQL integration tests.

Set ``PAMS_TEST_POSTGRESQL_URL`` to a disposable, empty test database to run.
"""

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from database.provider import open_database
from domain import Market
from pams.application import AddTransactionCommand, MigrateDatabaseUseCase
from pams.composition import compose_application, compose_daily_report

POSTGRESQL_URL = os.getenv("PAMS_TEST_POSTGRESQL_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRESQL_URL,
    reason="PAMS_TEST_POSTGRESQL_URL is not configured",
)

TABLES = (
    "report_deliveries",
    "position_snapshots",
    "daily_snapshots",
    "price_quotes",
    "dividends",
    "transactions",
    "liabilities",
    "holdings",
)


class StaticProvider:
    def __init__(self, market: Market) -> None:
        self.market = market
        self.source = f"{market.value}-integration"

    def fetch(self) -> list[dict[str, str]]:
        if self.market is Market.TWSE:
            return [
                {"Date": "1150722", "Code": symbol, "ClosingPrice": "100"}
                for symbol in ("0050", "2027", "2330")
            ]
        return [
            {
                "Date": "1150722",
                "SecuritiesCompanyCode": symbol,
                "Close": "100",
            }
            for symbol in ("3293", "8299")
        ]


def _clear_postgresql() -> None:
    assert POSTGRESQL_URL is not None
    database = open_database(POSTGRESQL_URL)
    try:
        database.initialize_schema()
        for table in TABLES:
            database.connection.execute(f"DELETE FROM {table}")
        database.connection.commit()
    finally:
        database.connection.close()


def test_sqlite_to_postgresql_and_operational_workflows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert POSTGRESQL_URL is not None
    _clear_postgresql()
    sqlite_path = tmp_path / "source.db"
    providers = (StaticProvider(Market.TWSE), StaticProvider(Market.TPEX))
    with compose_application(sqlite_path, providers=providers) as source:
        assert source.add_transaction is not None
        source.add_transaction.execute(
            AddTransactionCommand(
                symbol="0050",
                market="TWSE",
                transaction_type="buy",
                trade_date=date(2026, 7, 22),
                settlement_date=date(2026, 7, 22),
                quantity=Decimal("10"),
                price=Decimal("90"),
                fees=Decimal("0"),
                taxes=Decimal("0"),
                currency="TWD",
            )
        )
        source.update_portfolio.execute(date(2026, 7, 22))

    migration = MigrateDatabaseUseCase(
        f"sqlite:///{sqlite_path.as_posix()}", POSTGRESQL_URL
    ).execute()
    assert migration.total_rows > 0

    monkeypatch.setenv("PAMS_DATABASE_URL", POSTGRESQL_URL)
    from config import get_settings

    get_settings.cache_clear()
    try:
        with compose_application(providers=providers) as destination:
            assert destination.verify_system.execute().failed is False
            assert destination.add_transaction is not None
            destination.add_transaction.execute(
                AddTransactionCommand(
                    symbol="0050",
                    market="TWSE",
                    transaction_type="buy",
                    trade_date=date(2026, 7, 22),
                    settlement_date=date(2026, 7, 22),
                    quantity=Decimal("1"),
                    price=Decimal("100"),
                    fees=Decimal("0"),
                    taxes=Decimal("0"),
                    currency="TWD",
                )
            )
            rebuilt = destination.update_portfolio.execute(
                date(2026, 7, 22), force=True
            )
            assert rebuilt.totals is not None

        monkeypatch.setenv("PAMS_EMAIL_FROM", "sender@example.com")
        monkeypatch.setenv("PAMS_EMAIL_TO", "recipient@example.com")
        get_settings.cache_clear()
        with compose_daily_report(dry_run=True, providers=providers) as report_context:
            assert report_context.send_daily_report is not None
            report = report_context.send_daily_report.execute(
                date(2026, 7, 22), dry_run=True
            )
            assert report.status == "dry_run"
    finally:
        get_settings.cache_clear()
        _clear_postgresql()
