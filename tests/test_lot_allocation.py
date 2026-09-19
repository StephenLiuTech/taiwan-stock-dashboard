"""Explicit broker lot matching leaves acquisition fees outside trade basis."""

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from database.schema import initialize_schema
from database.sqlite import initialize_database
from domain import Currency, LotAllocation, Market, Transaction, TransactionType
from repositories.sqlite import (
    SQLiteLotAllocationRepository,
    SQLiteTransactionRepository,
)
from services import InvalidLotAllocationError, TransactionEngine


def trade(
    identifier: str,
    side: TransactionType,
    quantity: str,
    price: str,
    fees: str = "0",
    taxes: str = "0",
    day: int = 1,
) -> Transaction:
    return Transaction(
        id=identifier,
        symbol="3293",
        market=Market.TPEX,
        transaction_type=side,
        trade_date=date(2026, 9, day),
        settlement_date=date(2026, 9, day),
        quantity=Decimal(quantity),
        price=Decimal(price),
        fees=Decimal(fees),
        taxes=Decimal(taxes),
        currency=Currency.TWD,
    )


def allocation(buy: Transaction, fee: str) -> LotAllocation:
    return LotAllocation(
        sell_transaction_id="sell",
        buy_transaction_id=buy.id,
        matched_quantity=buy.quantity,
        matched_trade_cost=buy.quantity * buy.price,
        allocated_buy_fee=Decimal(fee),
        source="broker",
        source_reference="approved-3293-20260918",
    )


def case() -> tuple[list[Transaction], list[LotAllocation]]:
    opening = trade("opening", TransactionType.BUY, "2000", "838.559")
    buys = [
        trade("buy-707", TransactionType.BUY, "80", "707", "22", day=2),
        trade("buy-711-80", TransactionType.BUY, "80", "711", "22", day=3),
        trade("buy-706", TransactionType.BUY, "80", "706", "22", day=4),
        trade("buy-711-60", TransactionType.BUY, "60", "711", "17", day=4),
    ]
    sale = trade("sell", TransactionType.SELL, "300", "718", "85", "646", day=18)
    return [opening, *buys, sale], [allocation(buy, str(buy.fees)) for buy in buys]


def test_3293_explicit_lots_preserve_remaining_basis_and_separate_buy_fees() -> None:
    transactions, allocations = case()
    ledger = TransactionEngine(lot_allocations=allocations).build_ledger(transactions)

    sale = ledger.realized_sales[0]
    assert sale.total_cost_basis == Decimal("212580")
    assert sale.net_proceeds == Decimal("214669")
    assert sale.realized_pnl == Decimal("2089")
    assert ledger.total_buy_fees == Decimal("83")
    assert sale.realized_pnl - ledger.total_buy_fees == Decimal("2006")
    assert ledger.positions[0].quantity == Decimal("2000")
    assert ledger.positions[0].cost_basis == Decimal("1677118")
    assert ledger.positions[0].average_cost == Decimal("838.559")
    assert (
        TransactionEngine(lot_allocations=list(reversed(allocations))).build_ledger(
            list(reversed(transactions))
        )
        == ledger
    )


def test_incomplete_lot_match_stops_ledger_replay() -> None:
    transactions, allocations = case()
    with pytest.raises(InvalidLotAllocationError, match="Incomplete"):
        TransactionEngine(lot_allocations=allocations[:-1]).build_ledger(transactions)


def test_incorrect_matched_cost_or_buy_fee_stops_replay() -> None:
    transactions, allocations = case()
    wrong_cost = allocations[0].model_copy(
        update={"matched_trade_cost": Decimal("56561")}
    )
    with pytest.raises(InvalidLotAllocationError, match="trade cost"):
        TransactionEngine(lot_allocations=[wrong_cost, *allocations[1:]]).build_ledger(
            transactions
        )
    wrong_fee = allocations[0].model_copy(update={"allocated_buy_fee": Decimal("23")})
    with pytest.raises(InvalidLotAllocationError, match="BUY fee"):
        TransactionEngine(lot_allocations=[wrong_fee, *allocations[1:]]).build_ledger(
            transactions
        )


def test_legacy_sale_without_allocations_keeps_moving_average() -> None:
    transactions, _ = case()
    ledger = TransactionEngine().build_ledger(transactions)
    assert ledger.realized_sales[0].realized_pnl == (
        Decimal("214669") - Decimal("1889698") / Decimal("2300") * Decimal("300")
    )


def test_sqlite_lot_allocation_constraints_and_round_trip() -> None:
    connection = initialize_database("sqlite:///:memory:")
    initialize_schema(connection)
    transactions, allocations = case()
    transaction_repo = SQLiteTransactionRepository(connection)
    for item in transactions:
        transaction_repo.add(item)
    repository = SQLiteLotAllocationRepository(connection)
    repository.add_many(allocations)
    assert repository.list_by_sell("sell") == sorted(
        allocations, key=lambda item: item.buy_transaction_id
    )
    with pytest.raises(sqlite3.IntegrityError):
        repository.add_many(allocations[:1])
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO lot_allocations VALUES
            ('sell', 'opening', '-1', '1', '0', 'broker', 'invalid', '2026-09-18')"""
        )
    connection.close()
