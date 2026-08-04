"""SQLite repository integration tests."""

import sqlite3
from datetime import date, datetime
from decimal import Decimal

import pytest

from domain import (
    Currency,
    DailySnapshot,
    Dividend,
    Holding,
    Liability,
    LiabilityType,
    Market,
    PriceQuote,
    Transaction,
    TransactionType,
)
from repositories import (
    SQLiteDividendRepository,
    SQLiteHoldingRepository,
    SQLiteLiabilityRepository,
    SQLitePriceQuoteRepository,
    SQLiteReportDeliveryRepository,
    SQLiteSnapshotRepository,
    SQLiteTransactionRepository,
)
from services import TransactionEngine


def make_holding(holding_id: str = "h1", symbol: str = "2330") -> Holding:
    return Holding(
        id=holding_id,
        symbol=symbol,
        name="TSMC",
        market=Market.TWSE,
        currency=Currency.TWD,
        quantity=Decimal("10.125"),
        average_cost=Decimal("500.025"),
    )


def test_holding_insert_lookup_and_decimal_persistence(
    connection: sqlite3.Connection,
) -> None:
    repository = SQLiteHoldingRepository(connection)
    repository.upsert(make_holding())
    loaded = repository.get_by_id("h1")
    assert loaded is not None
    assert loaded.quantity == Decimal("10.125")
    assert loaded.average_cost == Decimal("500.025")


def test_holding_upsert_updates_existing(connection: sqlite3.Connection) -> None:
    repository = SQLiteHoldingRepository(connection)
    repository.upsert(make_holding())
    repository.upsert(make_holding().model_copy(update={"quantity": Decimal("20")}))
    assert repository.get_by_id("h1").quantity == Decimal("20")  # type: ignore[union-attr]


def test_holding_delete(connection: sqlite3.Connection) -> None:
    repository = SQLiteHoldingRepository(connection)
    repository.upsert(make_holding())
    repository.delete("h1")
    assert repository.get_by_id("h1") is None


def test_holding_symbol_is_unique(connection: sqlite3.Connection) -> None:
    repository = SQLiteHoldingRepository(connection)
    repository.upsert(make_holding("h1"))
    with pytest.raises(sqlite3.IntegrityError):
        repository.upsert(make_holding("h2"))


def test_transaction_list_by_symbol_preserves_dates(
    connection: sqlite3.Connection,
) -> None:
    repository = SQLiteTransactionRepository(connection)
    transaction = Transaction(
        id="t1",
        symbol="2330",
        market=Market.TWSE,
        transaction_type=TransactionType.BUY,
        trade_date=date(2026, 1, 2),
        settlement_date=date(2026, 1, 4),
        quantity=Decimal("2"),
        price=Decimal("100.01"),
        currency=Currency.TWD,
    )
    repository.upsert(transaction)
    loaded = repository.list_by_symbol(" 2330 ")[0]
    assert loaded.trade_date == date(2026, 1, 2)
    assert loaded.price == Decimal("100.01")


def test_transaction_repository_uses_same_day_buy_before_sell_order(
    connection: sqlite3.Connection,
) -> None:
    repository = SQLiteTransactionRepository(connection)
    common = {
        "symbol": "2330",
        "market": Market.TWSE,
        "trade_date": date(2026, 1, 2),
        "settlement_date": date(2026, 1, 2),
        "quantity": Decimal("1"),
        "price": Decimal("100"),
        "currency": Currency.TWD,
    }
    repository.upsert(
        Transaction(id="a-sell", transaction_type=TransactionType.SELL, **common)
    )
    repository.upsert(
        Transaction(id="z-buy", transaction_type=TransactionType.BUY, **common)
    )

    expected = ["z-buy", "a-sell"]
    assert [item.id for item in repository.list_all()] == expected
    assert [item.id for item in repository.list_by_symbol("2330")] == expected
    assert [item.id for item in repository.list_filtered()] == expected
    persisted = repository.list_all()
    assert TransactionEngine().build_ledger(
        persisted
    ) == TransactionEngine().build_ledger(list(reversed(persisted)))


def test_quote_repository_gets_latest_on_or_before_cutoff(
    connection: sqlite3.Connection,
) -> None:
    repository = SQLitePriceQuoteRepository(connection)
    repository.upsert_many(
        [
            PriceQuote(
                symbol="2330",
                market=Market.TWSE,
                trade_date=trade_date,
                close_price=price,
                currency=Currency.TWD,
                source="test",
            )
            for trade_date, price in (
                (date(2026, 7, 1), Decimal("90")),
                (date(2026, 7, 10), Decimal("120")),
            )
        ]
    )

    quote = repository.get_latest_on_or_before("2330", "TWSE", date(2026, 7, 5))
    assert quote is not None
    assert quote.trade_date == date(2026, 7, 1)
    assert quote.close_price == Decimal("90")
    assert repository.get_latest_on_or_before("2330", "TWSE", date(2026, 6, 30)) is None


def test_transaction_delete(connection: sqlite3.Connection) -> None:
    repository = SQLiteTransactionRepository(connection)
    transaction = Transaction(
        id="t1",
        symbol="2330",
        market=Market.TWSE,
        transaction_type=TransactionType.SELL,
        trade_date=date(2026, 1, 2),
        settlement_date=date(2026, 1, 2),
        quantity=Decimal("1"),
        price=Decimal("1"),
        currency=Currency.TWD,
    )
    repository.upsert(transaction)
    repository.delete("t1")
    assert repository.list_all() == []


def test_dividend_round_trip(connection: sqlite3.Connection) -> None:
    repository = SQLiteDividendRepository(connection)
    dividend = Dividend(
        id="d1",
        symbol="2330",
        market=Market.TWSE,
        ex_dividend_date=date(2026, 1, 2),
        payment_date=date(2026, 1, 5),
        amount_per_share=Decimal("2.5"),
        currency=Currency.TWD,
        shares_eligible=Decimal("10"),
        gross_amount=Decimal("25"),
        withholding_tax=Decimal("2.5"),
        net_amount=Decimal("22.5"),
    )
    repository.upsert(dividend)
    assert repository.get_by_id("d1") == dividend


def test_liability_round_trip_with_optional_rate(
    connection: sqlite3.Connection,
) -> None:
    repository = SQLiteLiabilityRepository(connection)
    liability = Liability(
        id="l1",
        liability_type=LiabilityType.STOCK_PLEDGE,
        principal=Decimal("998000.50"),
        annual_interest_rate=None,
        currency=Currency.TWD,
    )
    repository.upsert(liability)
    assert repository.get_by_id("l1") == liability


def make_snapshot(snapshot_date: date) -> DailySnapshot:
    return DailySnapshot(
        snapshot_date=snapshot_date,
        total_market_value=Decimal("100"),
        total_cost_basis=Decimal("80"),
        total_unrealized_pnl=Decimal("20"),
        total_liabilities=Decimal("10"),
        net_asset_value=Decimal("90"),
        leverage_ratio=Decimal("0.1"),
        high_water_mark=Decimal("90"),
        drawdown=Decimal("0"),
    )


def test_snapshot_latest_and_date_range(connection: sqlite3.Connection) -> None:
    repository = SQLiteSnapshotRepository(connection)
    repository.add(make_snapshot(date(2026, 1, 1)))
    repository.add(make_snapshot(date(2026, 1, 2)))
    assert repository.get_latest().snapshot_date == date(2026, 1, 2)  # type: ignore[union-attr]
    assert len(repository.list_between_dates(date(2026, 1, 1), date(2026, 1, 2))) == 2


def test_snapshot_date_is_unique(connection: sqlite3.Connection) -> None:
    repository = SQLiteSnapshotRepository(connection)
    snapshot = make_snapshot(date(2026, 1, 1))
    repository.add(snapshot)
    with pytest.raises(sqlite3.IntegrityError):
        repository.add(snapshot)


def test_report_delivery_sent_failed_and_retry_state(
    connection: sqlite3.Connection,
) -> None:
    repository = SQLiteReportDeliveryRepository(connection)
    report_date = date(2026, 7, 22)
    recipient = "recipient@example.com"
    repository.mark_failed("daily", report_date, recipient, "temporary")
    assert repository.claim("daily", report_date, recipient) is True
    assert repository.claim("daily", report_date, recipient) is False
    repository.mark_sent(
        "daily",
        report_date,
        recipient,
        datetime.fromisoformat("2026-07-22T10:00:00+00:00"),
    )
    assert repository.claim("daily", report_date, recipient) is False
    count = connection.execute("SELECT COUNT(*) FROM report_deliveries").fetchone()[0]
    assert count == 1
