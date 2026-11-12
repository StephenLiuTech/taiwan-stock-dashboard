"""First-class margin-financed transaction workflow tests."""

import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import pytest

from database.schema import initialize_schema
from domain import (
    Currency,
    FinancingType,
    Holding,
    HoldingType,
    Liability,
    LiabilityPrincipalEvent,
    LiabilityPrincipalEventType,
    LiabilityType,
    MarginFinancingPosition,
    Market,
    Transaction,
    TransactionType,
    margin_position_id,
)
from pams.application import AddTransactionCommand, AddTransactionUseCase
from pams.application.exceptions import DuplicateTransactionError
from repositories.holding_rebuild_uow import SQLiteMarginTransactionUnitOfWork
from repositories.sqlite import (
    SQLiteHoldingRepository,
    SQLiteLiabilityPrincipalEventRepository,
    SQLiteLiabilityRepository,
    SQLiteMarginFinancingPositionRepository,
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
            financed_symbol="2027",
            financed_quantity=Decimal("28000"),
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


def seed_legacy_margin_position(connection: sqlite3.Connection) -> None:
    SQLiteMarginFinancingPositionRepository(connection).upsert(
        MarginFinancingPosition(
            id=margin_position_id("liability-margin-financing", Market.TWSE, "2027"),
            liability_id="liability-margin-financing",
            market=Market.TWSE,
            symbol="2027",
            principal=Decimal("707000"),
            financed_quantity=Decimal("28000"),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )


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
    event = SQLiteLiabilityPrincipalEventRepository(margin_database).list_by_liability(
        "liability-margin-financing"
    )
    assert len(event) == 1
    assert event[0].principal_delta == Decimal("60000")
    assert event[0].resulting_principal == Decimal("767000")
    assert event[0].reference == "margin-1"
    assert (
        SQLiteLiabilityRepository(margin_database).get_by_id("liability-stock-pledge")
        == pledge_before
    )


@pytest.mark.parametrize(
    ("price", "expected_principal", "expected_cash"),
    [
        (Decimal("48.10"), Decimal("57000"), Decimal("39200")),
        (Decimal("49.30"), Decimal("59000"), Decimal("39600")),
        (Decimal("71.25"), Decimal("85000"), Decimal("57500")),
    ],
)
def test_margin_principal_uses_broker_thousand_rounding(
    margin_database: sqlite3.Connection,
    price: Decimal,
    expected_principal: Decimal,
    expected_cash: Decimal,
) -> None:
    result = use_case(margin_database).execute(command(price=price))
    assert result.gross_purchase_value == price * Decimal("2000")
    assert result.financed_principal == expected_principal
    assert result.self_funded_amount == expected_cash


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
    assert len(SQLiteLiabilityPrincipalEventRepository(margin_database).list_all()) == 1


def test_backdated_margin_buy_restates_later_principal_checkpoints(
    margin_database: sqlite3.Connection,
) -> None:
    events = SQLiteLiabilityPrincipalEventRepository(margin_database)
    events.insert_many_if_absent(
        [
            LiabilityPrincipalEvent(
                id="opening-margin",
                liability_id="liability-margin-financing",
                effective_date=date(2026, 8, 14),
                sequence=0,
                event_type=LiabilityPrincipalEventType.OPENING,
                principal_delta=Decimal("707000"),
                resulting_principal=Decimal("707000"),
                source="test opening",
                created_at=datetime(2026, 8, 14, tzinfo=UTC),
            )
        ]
    )
    case = use_case(margin_database)
    case.execute(
        command(
            transaction_id="later-margin-buy",
            trade_date=date(2026, 8, 23),
        )
    )

    result = case.execute(
        command(
            transaction_id="backdated-margin-buy",
            trade_date=date(2026, 8, 22),
            price=Decimal("48.10"),
        )
    )

    assert result.updated_margin_principal == Decimal("824000")
    checkpoints = {
        item.id: item.resulting_principal
        for item in events.list_by_liability("liability-margin-financing")
    }
    assert checkpoints == {
        "opening-margin": Decimal("707000"),
        str(
            uuid5(
                NAMESPACE_URL,
                "margin-principal|liability-margin-financing|backdated-margin-buy",
            )
        ): Decimal("764000"),
        str(
            uuid5(
                NAMESPACE_URL,
                "margin-principal|liability-margin-financing|later-margin-buy",
            )
        ): Decimal("824000"),
    }


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


def test_principal_event_failure_rolls_back_transaction_holding_and_master(
    margin_database: sqlite3.Connection,
) -> None:
    unit = SQLiteMarginTransactionUnitOfWork(margin_database)

    class FailingEvents:
        def list_by_liability(self, liability_id: str) -> list[object]:
            del liability_id
            return []

        def insert_many_if_absent(self, events: list[object]) -> int:
            del events
            raise RuntimeError("simulated principal-event write failure")

    unit.liability_principal_events = FailingEvents()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="principal-event"):
        AddTransactionUseCase(
            SQLiteTransactionRepository(margin_database), unit, Decimal("0.40")
        ).execute(command())
    assert SQLiteTransactionRepository(margin_database).get_by_id("margin-1") is None
    assert SQLiteHoldingRepository(margin_database).list_all()[0].quantity == Decimal(
        "36000"
    )
    margin = SQLiteLiabilityRepository(margin_database).get_by_id(
        "liability-margin-financing"
    )
    assert margin is not None and margin.principal == Decimal("707000")


def test_margin_position_failure_rolls_back_every_write(
    margin_database: sqlite3.Connection,
) -> None:
    seed_legacy_margin_position(margin_database)
    unit = SQLiteMarginTransactionUnitOfWork(margin_database)

    class FailingPositions:
        def get(
            self, liability_id: str, market: str, symbol: str
        ) -> MarginFinancingPosition | None:
            return SQLiteMarginFinancingPositionRepository(margin_database).get(
                liability_id, market, symbol
            )

        def upsert(self, position: MarginFinancingPosition) -> None:
            del position
            raise RuntimeError("simulated margin-position write failure")

    unit.margin_positions = FailingPositions()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="margin-position"):
        AddTransactionUseCase(
            SQLiteTransactionRepository(margin_database), unit, Decimal("0.40")
        ).execute(
            command(
                transaction_id=None,
                symbol="00406A",
                trade_date=date(2026, 11, 11),
                quantity=Decimal("15000"),
                price=Decimal("7.89"),
                fees=Decimal("47"),
                broker_order="AW854",
                margin_principal=Decimal("47000"),
            )
        )
    assert not any(
        item.symbol == "00406A"
        for item in SQLiteTransactionRepository(margin_database).list_all()
    )
    liability = SQLiteLiabilityRepository(margin_database).get_by_id(
        "liability-margin-financing"
    )
    assert liability is not None and liability.principal == Decimal("707000")
    assert (
        SQLiteMarginFinancingPositionRepository(margin_database).get(
            "liability-margin-financing", "TWSE", "00406A"
        )
        is None
    )


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


def test_second_margin_symbol_preserves_existing_legacy_position(
    margin_database: sqlite3.Connection,
) -> None:
    seed_legacy_margin_position(margin_database)
    result = use_case(margin_database).execute(
        command(
            transaction_id=None,
            symbol="00406A",
            trade_date=date(2026, 11, 11),
            quantity=Decimal("15000"),
            price=Decimal("7.89"),
            fees=Decimal("47"),
            notes="Broker order AW854",
            broker_order="AW854",
            margin_principal=Decimal("47000"),
        )
    )

    assert result.applied is True
    assert result.financed_principal == Decimal("47000")
    assert result.self_funded_amount == Decimal("71350")
    assert result.updated_holding_quantity == Decimal("15000")
    assert result.updated_margin_quantity == Decimal("15000")
    assert result.updated_margin_principal == Decimal("754000")
    positions = SQLiteMarginFinancingPositionRepository(
        margin_database
    ).list_by_liability("liability-margin-financing")
    assert [
        (item.symbol, item.principal, item.financed_quantity) for item in positions
    ] == [
        ("00406A", Decimal("47000"), Decimal("15000")),
        ("2027", Decimal("707000"), Decimal("28000")),
    ]
    liability = SQLiteLiabilityRepository(margin_database).get_by_id(
        "liability-margin-financing"
    )
    assert liability is not None
    assert liability.principal == Decimal("754000")
    assert liability.financed_symbol == "2027"
    assert liability.financed_quantity == Decimal("28000")
    event = SQLiteLiabilityPrincipalEventRepository(margin_database).list_all()[0]
    assert event.margin_position_id == result.margin_position_id
    assert event.financed_quantity_delta == Decimal("15000")


def test_second_margin_symbol_dry_run_has_no_writes(
    margin_database: sqlite3.Connection,
) -> None:
    seed_legacy_margin_position(margin_database)
    before_transactions = SQLiteTransactionRepository(margin_database).list_all()
    before_holdings = SQLiteHoldingRepository(margin_database).list_all()
    before_liabilities = SQLiteLiabilityRepository(margin_database).list_all()
    before_positions = SQLiteMarginFinancingPositionRepository(
        margin_database
    ).list_all()

    result = use_case(margin_database).execute(
        command(
            transaction_id=None,
            symbol="00406A",
            trade_date=date(2026, 11, 11),
            quantity=Decimal("15000"),
            price=Decimal("7.89"),
            fees=Decimal("47"),
            broker_order="AW854",
            margin_principal=Decimal("47000"),
        ),
        apply=False,
    )

    assert result.applied is False
    assert result.updated_holding_quantity == Decimal("15000")
    assert result.financed_principal == Decimal("47000")
    assert (
        SQLiteTransactionRepository(margin_database).list_all() == before_transactions
    )
    assert SQLiteHoldingRepository(margin_database).list_all() == before_holdings
    assert SQLiteLiabilityRepository(margin_database).list_all() == before_liabilities
    assert (
        SQLiteMarginFinancingPositionRepository(margin_database).list_all()
        == before_positions
    )


def test_duplicate_broker_order_does_not_apply_second_margin_write(
    margin_database: sqlite3.Connection,
) -> None:
    seed_legacy_margin_position(margin_database)
    case = use_case(margin_database)
    first = command(
        transaction_id=None,
        symbol="00406A",
        trade_date=date(2026, 11, 11),
        quantity=Decimal("15000"),
        price=Decimal("7.89"),
        fees=Decimal("47"),
        broker_order="AW854",
        margin_principal=Decimal("47000"),
    )
    case.execute(first)
    with pytest.raises(DuplicateTransactionError):
        case.execute(first)
    position = SQLiteMarginFinancingPositionRepository(margin_database).get(
        "liability-margin-financing", "TWSE", "00406A"
    )
    assert position is not None
    assert position.principal == Decimal("47000")
    assert position.financed_quantity == Decimal("15000")
