"""First-class margin-financed transaction workflow tests."""

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from database.schema import initialize_schema
from domain import (
    Currency,
    FinancingType,
    Holding,
    HoldingType,
    Liability,
    LiabilityType,
    Market,
    Transaction,
    TransactionType,
)
from pams.application import AddTransactionCommand, AddTransactionUseCase
from pams.application.exceptions import DuplicateTransactionError
from repositories.holding_rebuild_uow import SQLiteMarginTransactionUnitOfWork
from repositories.sqlite import (
    SQLiteHoldingRepository,
    SQLiteLiabilityRepository,
    SQLiteTransactionRepository,
)
from services import MarginFinancingError, MarginFinancingService


def command(**updates: object) -> AddTransactionCommand:
    values = {
        "transaction_id": "margin-1",
        "symbol": "2027",
        "market": "TWSE",
        "transaction_type": "buy",
        "trade_date": date(2026, 8, 21),
        "settlement_date": None,
        "quantity": Decimal("2000"),
        "price": Decimal("50"),
        "fees": Decimal("25"),
        "taxes": Decimal("0"),
        "currency": "TWD",
        "notes": "approved margin purchase",
        "financing_type": "margin",
    }
    values.update(updates)
    return AddTransactionCommand(**values)  # type: ignore[arg-type]


@pytest.fixture
def margin_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    transactions = SQLiteTransactionRepository(connection)
    transactions.add(
        Transaction(
            id="opening-2027",
            symbol="2027",
            market=Market.TWSE,
            transaction_type=TransactionType.BUY,
            trade_date=date(2026, 1, 1),
            settlement_date=date(2026, 1, 1),
            quantity=Decimal("36000"),
            price=Decimal("42"),
            currency=Currency.TWD,
        )
    )
    SQLiteHoldingRepository(connection).upsert(
        Holding(
            id="holding-2027",
            symbol="2027",
            name="Ta Chen",
            market=Market.TWSE,
            currency=Currency.TWD,
            quantity=Decimal("36000"),
            average_cost=Decimal("42"),
            holding_type=HoldingType.STOCK,
        )
    )
    liabilities = SQLiteLiabilityRepository(connection)
    liabilities.upsert(
        Liability(
            id="liability-margin-financing",
            liability_type=LiabilityType.MARGIN_FINANCING,
            principal=Decimal("707000"),
            currency=Currency.TWD,
            collateral_description="2027 — 28,000 shares",
            notes="Accrued interest reference: NT$4,583",
        )
    )
    liabilities.upsert(
        Liability(
            id="liability-stock-pledge",
            liability_type=LiabilityType.STOCK_PLEDGE,
            principal=Decimal("998000"),
            currency=Currency.TWD,
            collateral_description="0050: 4,000 shares",
            notes="pledge metadata",
        )
    )
    yield connection
    connection.close()


def use_case(
    connection: sqlite3.Connection, ratio: Decimal = Decimal("0.40")
) -> AddTransactionUseCase:
    return AddTransactionUseCase(
        SQLiteTransactionRepository(connection),
        SQLiteMarginTransactionUnitOfWork(connection),
        ratio,
    )


def test_normal_buy_remains_transaction_only() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    repository = SQLiteTransactionRepository(connection)
    result = AddTransactionUseCase(repository).execute(
        command(financing_type=None, transaction_id="cash-buy")
    )
    assert result.financing_type is None
    assert repository.get_by_id("cash-buy") is not None
    assert SQLiteHoldingRepository(connection).list_all() == []
    connection.close()


def test_margin_buy_atomically_updates_transaction_holding_and_liability(
    margin_database: sqlite3.Connection,
) -> None:
    pledge_before = SQLiteLiabilityRepository(margin_database).get_by_id(
        "liability-stock-pledge"
    )
    result = use_case(margin_database).execute(command())
    assert result.gross_purchase_value == Decimal("100000")
    assert result.self_funded_amount == Decimal("40000.00")
    assert result.financed_principal == Decimal("60000.00")
    assert result.updated_holding_quantity == Decimal("38000")
    assert result.updated_margin_quantity == Decimal("30000")
    assert result.updated_margin_principal == Decimal("767000.00")
    stored = SQLiteTransactionRepository(margin_database).get_by_id("margin-1")
    assert stored is not None and stored.financing_type is FinancingType.MARGIN
    holding = SQLiteHoldingRepository(margin_database).list_all()[0]
    assert holding.quantity == Decimal("38000")
    margin = SQLiteLiabilityRepository(margin_database).get_by_id(
        "liability-margin-financing"
    )
    assert margin is not None
    assert margin.principal == Decimal("767000.00")
    assert margin.financed_symbol == "2027"
    assert margin.financed_quantity == Decimal("30000")
    assert margin.notes == "Accrued interest reference: NT$4,583"
    assert (
        SQLiteLiabilityRepository(margin_database).get_by_id("liability-stock-pledge")
        == pledge_before
    )


def test_margin_decimal_precision_is_exact(margin_database: sqlite3.Connection) -> None:
    result = use_case(
        margin_database, Decimal("0.3333333333333333333333333333")
    ).execute(command(quantity=Decimal("3"), price=Decimal("0.1")))
    assert result.gross_purchase_value == Decimal("0.3")
    assert result.self_funded_amount == Decimal("0.09999999999999999999999999999")
    assert result.financed_principal == Decimal("0.2000000000000000000000000000")


def test_duplicate_margin_purchase_does_not_apply_twice(
    margin_database: sqlite3.Connection,
) -> None:
    case = use_case(margin_database)
    case.execute(command())
    with pytest.raises(DuplicateTransactionError):
        case.execute(command(transaction_id="different-id"))
    assert len(SQLiteTransactionRepository(margin_database).list_all()) == 2
    margin = SQLiteLiabilityRepository(margin_database).get_by_id(
        "liability-margin-financing"
    )
    assert margin is not None and margin.principal == Decimal("767000.00")
    assert SQLiteHoldingRepository(margin_database).list_all()[0].quantity == Decimal(
        "38000"
    )


def test_failure_rolls_back_all_margin_writes(
    margin_database: sqlite3.Connection,
) -> None:
    unit = SQLiteMarginTransactionUnitOfWork(margin_database)
    original = unit.liabilities

    class FailingLiabilities:
        def list_all(self) -> list[Liability]:
            return original.list_all()

        def upsert(self, liability: Liability) -> None:
            del liability
            raise RuntimeError("simulated liability write failure")

    unit.liabilities = FailingLiabilities()  # type: ignore[assignment]
    case = AddTransactionUseCase(
        SQLiteTransactionRepository(margin_database), unit, Decimal("0.40")
    )
    with pytest.raises(RuntimeError, match="simulated"):
        case.execute(command())
    assert SQLiteTransactionRepository(margin_database).get_by_id("margin-1") is None
    assert SQLiteHoldingRepository(margin_database).list_all()[0].quantity == Decimal(
        "36000"
    )
    margin = SQLiteLiabilityRepository(margin_database).get_by_id(
        "liability-margin-financing"
    )
    assert margin is not None and margin.principal == Decimal("707000")


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"market": "US", "currency": "USD"}, "Taiwan markets"),
        ({"transaction_type": "sell"}, "BUY transactions"),
    ],
)
def test_invalid_margin_combinations_are_rejected(
    margin_database: sqlite3.Connection,
    updates: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        use_case(margin_database).execute(command(**updates))
    assert SQLiteTransactionRepository(margin_database).get_by_id("margin-1") is None


@pytest.mark.parametrize("ratio", [Decimal("0"), Decimal("1"), Decimal("1.1")])
def test_invalid_margin_ratio_is_rejected(ratio: Decimal) -> None:
    with pytest.raises(MarginFinancingError, match="ratio"):
        MarginFinancingService(ratio)


def test_structured_financing_classification_survives_reload(
    margin_database: sqlite3.Connection,
) -> None:
    use_case(margin_database).execute(command())
    reloaded = SQLiteTransactionRepository(margin_database).get_by_id("margin-1")
    assert reloaded is not None
    assert reloaded.financing_type is FinancingType.MARGIN
    liability = SQLiteLiabilityRepository(margin_database).get_by_id(
        "liability-margin-financing"
    )
    assert liability is not None
    assert (liability.financed_symbol, liability.financed_quantity) == (
        "2027",
        Decimal("30000"),
    )
