"""Application service behavior tests."""

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from domain import (
    Currency,
    Holding,
    Liability,
    LiabilityType,
    Market,
    PortfolioSummary,
    PriceQuote,
)
from repositories import (
    SQLiteHoldingRepository,
    SQLiteLiabilityRepository,
    SQLiteSnapshotRepository,
)
from services import (
    BootstrapService,
    DuplicateSnapshotError,
    MissingPriceQuoteError,
    PortfolioService,
    SnapshotService,
)


def make_holding(symbol: str = "2330") -> Holding:
    return Holding(
        id=f"holding-{symbol}",
        symbol=symbol,
        name=symbol,
        market=Market.TWSE,
        currency=Currency.TWD,
        quantity=Decimal("10"),
        average_cost=Decimal("80"),
    )


def make_quote(symbol: str = "2330", close: str = "100") -> PriceQuote:
    return PriceQuote(
        symbol=symbol,
        market=Market.TWSE,
        trade_date=date(2026, 1, 2),
        close_price=Decimal(close),
        previous_close=Decimal("90"),
        currency=Currency.TWD,
        source="manual-test",
    )


def value_summary() -> PortfolioSummary:
    liability = Liability(
        liability_type=LiabilityType.OTHER,
        principal=Decimal("200"),
        currency=Currency.TWD,
    )
    return PortfolioService().value_portfolio(
        [make_holding()], [make_quote()], [liability], date(2026, 1, 2)
    )


def test_portfolio_calculates_position_values() -> None:
    position = value_summary().positions[0]
    assert position.cost_basis == Decimal("800")
    assert position.market_value == Decimal("1000")
    assert position.unrealized_pnl == Decimal("200")
    assert position.unrealized_return == Decimal("0.25")


def test_portfolio_calculates_weight_and_daily_change() -> None:
    position = value_summary().positions[0]
    assert position.portfolio_weight == Decimal("1")
    assert position.daily_value_change == Decimal("100")


def test_portfolio_calculates_nav_and_leverage() -> None:
    summary = value_summary()
    assert summary.net_asset_value == Decimal("800")
    assert summary.leverage_ratio == Decimal("0.2")


def test_portfolio_raises_for_missing_quote() -> None:
    with pytest.raises(MissingPriceQuoteError):
        PortfolioService().value_portfolio([make_holding()], [], [], date(2026, 1, 2))


def test_portfolio_weights_sum_to_one() -> None:
    holdings = [make_holding("2330"), make_holding("2317")]
    quotes = [make_quote("2330", "100"), make_quote("2317", "300")]
    summary = PortfolioService().value_portfolio(holdings, quotes, [], date(2026, 1, 2))
    assert sum(position.portfolio_weight for position in summary.positions) == Decimal(
        "1"
    )


def test_first_snapshot_sets_high_water_mark(connection: sqlite3.Connection) -> None:
    service = SnapshotService(SQLiteSnapshotRepository(connection))
    snapshot = service.create(value_summary())
    assert snapshot.high_water_mark == Decimal("800")
    assert snapshot.drawdown == Decimal("0")


def test_snapshot_records_new_high(connection: sqlite3.Connection) -> None:
    service = SnapshotService(SQLiteSnapshotRepository(connection))
    service.create(value_summary())
    higher = value_summary().model_copy(
        update={"valuation_date": date(2026, 1, 3), "net_asset_value": Decimal("900")}
    )
    assert service.create(higher).high_water_mark == Decimal("900")


def test_snapshot_calculates_drawdown(connection: sqlite3.Connection) -> None:
    service = SnapshotService(SQLiteSnapshotRepository(connection))
    service.create(value_summary())
    lower = value_summary().model_copy(
        update={"valuation_date": date(2026, 1, 3), "net_asset_value": Decimal("600")}
    )
    snapshot = service.create(lower)
    assert snapshot.high_water_mark == Decimal("800")
    assert snapshot.drawdown == Decimal("-0.25")


def test_snapshot_rejects_duplicate_date(connection: sqlite3.Connection) -> None:
    service = SnapshotService(SQLiteSnapshotRepository(connection))
    service.create(value_summary())
    with pytest.raises(DuplicateSnapshotError):
        service.create(value_summary())


def test_bootstrap_seeds_empty_database(connection: sqlite3.Connection) -> None:
    holdings = SQLiteHoldingRepository(connection)
    liabilities = SQLiteLiabilityRepository(connection)
    assert BootstrapService(connection, holdings, liabilities).initialize() is True
    assert len(holdings.list_all()) == 5
    assert len(liabilities.list_all()) == 2


def test_bootstrap_does_not_overwrite_existing_data(
    connection: sqlite3.Connection,
) -> None:
    holdings = SQLiteHoldingRepository(connection)
    liabilities = SQLiteLiabilityRepository(connection)
    custom = make_holding("CUSTOM")
    holdings.upsert(custom)
    assert BootstrapService(connection, holdings, liabilities).initialize() is False
    assert holdings.list_all() == [custom]
    assert liabilities.list_all() == []
