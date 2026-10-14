"""Application tests for transaction entry and filtered queries."""

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from domain import Currency, Market, Transaction, TransactionType
from pams.application import (
    AddTransactionCommand,
    AddTransactionUseCase,
    DuplicateTransactionError,
    ListTransactionsUseCase,
)
from repositories import SQLiteHoldingRepository, SQLiteTransactionRepository
from repositories.holding_rebuild_uow import SQLiteHoldingRebuildUnitOfWork


def command(identifier: str = "tx-1") -> AddTransactionCommand:
    return AddTransactionCommand(
        transaction_id=identifier,
        symbol=" 2330 ",
        market="TWSE",
        transaction_type="buy",
        trade_date=date(2026, 7, 1),
        settlement_date=date(2026, 7, 3),
        quantity=Decimal("100.125"),
        price=Decimal("1800.1234"),
        fees=Decimal("20.05"),
        taxes=Decimal("0"),
        currency="TWD",
        notes="manual entry",
    )


class FakeTransactionRepository:
    def __init__(self, records: list[Transaction] | None = None) -> None:
        self.records = {item.id: item for item in records or []}

    def exists(self, transaction_id: str) -> bool:
        return transaction_id in self.records

    def add(self, transaction: Transaction) -> None:
        self.records[transaction.id] = transaction

    def list_filtered(
        self,
        *,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Transaction]:
        return [
            item
            for item in sorted(
                self.records.values(),
                key=lambda value: (
                    value.trade_date,
                    value.settlement_date,
                    value.id,
                ),
            )
            if (symbol is None or item.symbol == symbol.strip().upper())
            and (start_date is None or item.trade_date >= start_date)
            and (end_date is None or item.trade_date <= end_date)
        ]


def test_add_transaction_uses_domain_validation_and_exact_decimals() -> None:
    repository = FakeTransactionRepository()
    result = AddTransactionUseCase(repository).execute(command())  # type: ignore[arg-type]
    assert result.id == "tx-1"
    assert result.symbol == "2330"
    assert result.quantity == Decimal("100.125")
    assert result.price == Decimal("1800.1234")
    assert repository.records["tx-1"].notes == "manual entry"


def test_omitted_settlement_date_defaults_to_trade_date() -> None:
    repository = FakeTransactionRepository()
    omitted = command()
    omitted = AddTransactionCommand(
        **{
            **vars(omitted),
            "settlement_date": None,
        }
    )

    result = AddTransactionUseCase(repository).execute(omitted)  # type: ignore[arg-type]

    assert result.settlement_date == result.trade_date
    assert repository.records["tx-1"].settlement_date == date(2026, 7, 1)


def test_explicit_settlement_date_remains_compatible() -> None:
    repository = FakeTransactionRepository()

    result = AddTransactionUseCase(repository).execute(command())  # type: ignore[arg-type]

    assert result.trade_date == date(2026, 7, 1)
    assert result.settlement_date == date(2026, 7, 3)


def test_us_transaction_requires_and_preserves_usd() -> None:
    repository = FakeTransactionRepository()
    us = AddTransactionCommand(
        **{
            **vars(command()),
            "symbol": "mu",
            "market": "US",
            "currency": "USD",
        }
    )
    result = AddTransactionUseCase(repository).execute(us)  # type: ignore[arg-type]
    assert result.symbol == "MU"
    assert result.market == "US"
    assert result.currency == "USD"


def test_market_currency_mismatch_is_rejected() -> None:
    repository = FakeTransactionRepository()
    invalid = AddTransactionCommand(
        **{**vars(command()), "market": "US", "currency": "TWD"}
    )
    with pytest.raises(ValueError, match="US transactions require USD"):
        AddTransactionUseCase(repository).execute(invalid)  # type: ignore[arg-type]


def test_duplicate_transaction_id_is_rejected_without_overwrite() -> None:
    repository = FakeTransactionRepository()
    use_case = AddTransactionUseCase(repository)  # type: ignore[arg-type]
    use_case.execute(command())
    with pytest.raises(DuplicateTransactionError, match="tx-1"):
        use_case.execute(command())
    assert len(repository.records) == 1


def test_transaction_list_filters_symbol_and_date_range() -> None:
    repository = FakeTransactionRepository()
    add = AddTransactionUseCase(repository)  # type: ignore[arg-type]
    add.execute(command("first"))
    repository.add(
        Transaction(
            id="second",
            symbol="8299",
            market=Market.TPEX,
            transaction_type=TransactionType.SELL,
            trade_date=date(2026, 7, 10),
            settlement_date=date(2026, 7, 12),
            quantity=Decimal("1"),
            price=Decimal("2"),
            currency=Currency.TWD,
        )
    )
    result = ListTransactionsUseCase(repository).execute(  # type: ignore[arg-type]
        symbol="2330", start_date=date(2026, 7, 1), end_date=date(2026, 7, 5)
    )
    assert [item.id for item in result.transactions] == ["first"]
    with pytest.raises(ValueError, match="from-date"):
        ListTransactionsUseCase(repository).execute(  # type: ignore[arg-type]
            start_date=date(2026, 7, 2), end_date=date(2026, 7, 1)
        )


def test_sqlite_transaction_add_rejects_duplicates_and_filters(
    connection: sqlite3.Connection,
) -> None:
    repository = SQLiteTransactionRepository(connection)
    use_case = AddTransactionUseCase(repository)
    use_case.execute(command("sqlite-one"))
    with pytest.raises(DuplicateTransactionError):
        use_case.execute(command("sqlite-one"))
    listed = ListTransactionsUseCase(repository).execute(
        symbol="2330", start_date=date(2026, 7, 1), end_date=date(2026, 7, 1)
    )
    assert len(listed.transactions) == 1
    assert listed.transactions[0].fees == Decimal("20.05")


def test_full_sale_atomically_closes_ledger_derived_holding(
    connection: sqlite3.Connection,
) -> None:
    transactions = SQLiteTransactionRepository(connection)
    unit = SQLiteHoldingRebuildUnitOfWork(connection)
    use_case = AddTransactionUseCase(transactions, holding_unit_of_work=unit)
    buy = AddTransactionCommand(
        transaction_id="aapl-buy",
        symbol="AAPL",
        market="US",
        transaction_type="buy",
        trade_date=date(2026, 9, 9),
        settlement_date=date(2026, 9, 11),
        quantity=Decimal("1"),
        price=Decimal("312.50"),
        fees=Decimal("0.25"),
        taxes=Decimal("0"),
        currency="USD",
        notes="fixture",
    )
    sell = AddTransactionCommand(
        **{
            **vars(buy),
            "transaction_id": "aapl-sell",
            "transaction_type": "sell",
            "trade_date": date(2026, 10, 14),
            "settlement_date": date(2026, 10, 16),
            "price": Decimal("343.50"),
            "fees": Decimal("0.27"),
        }
    )

    assert use_case.execute(buy).updated_holding_quantity == Decimal("1")
    result = use_case.execute(sell)

    holding = SQLiteHoldingRepository(connection).get_by_id(
        "holding-ledger-us-usd-aapl"
    )
    assert result.updated_holding_quantity == Decimal("0")
    assert holding is not None
    assert holding.quantity == Decimal("0")
    assert holding.average_cost == Decimal("0")
    assert [item.id for item in transactions.list_all()] == ["aapl-buy", "aapl-sell"]


def test_transaction_and_holding_update_roll_back_together(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    transactions = SQLiteTransactionRepository(connection)
    unit = SQLiteHoldingRebuildUnitOfWork(connection)
    use_case = AddTransactionUseCase(transactions, holding_unit_of_work=unit)
    failing = command("atomic-failure")
    monkeypatch.setattr(
        unit.holdings,
        "upsert",
        lambda _holding: (_ for _ in ()).throw(RuntimeError("holding write failed")),
    )

    with pytest.raises(RuntimeError, match="holding write failed"):
        use_case.execute(failing)

    assert not transactions.exists("atomic-failure")
    assert SQLiteHoldingRepository(connection).list_all() == []
