import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from database.schema import initialize_schema
from domain import CorporateAction, Currency, Market, Transaction, TransactionType
from pams.application import AddCorporateActionCommand, AddCorporateActionUseCase
from pams.cli import build_parser
from repositories.provider import create_repositories
from services import InvalidTransactionHistoryError, TransactionEngine


def transaction(
    identifier: str,
    side: TransactionType,
    quantity: str,
    price: str,
    trade_date: date,
    *,
    fees: str = "0",
    taxes: str = "0",
) -> Transaction:
    return Transaction(
        id=identifier,
        symbol="00631L",
        market=Market.TWSE,
        transaction_type=side,
        trade_date=trade_date,
        settlement_date=trade_date,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fees=Decimal(fees),
        taxes=Decimal(taxes),
        currency=Currency.TWD,
    )


def action(multiplier: str = "22") -> CorporateAction:
    return CorporateAction(
        id="split-00631l-20260331",
        symbol="00631L",
        market=Market.TWSE,
        effective_date=date(2026, 3, 31),
        quantity_multiplier=Decimal(multiplier),
        source="brokerage",
        reference="statement-20260828",
        notes="quantity conversion",
    )


def test_00631l_split_replays_between_buy_and_sell() -> None:
    ledger = TransactionEngine().build_ledger(
        [
            transaction("buy", TransactionType.BUY, "10", "491.50", date(2026, 3, 5)),
            transaction(
                "sell",
                TransactionType.SELL,
                "220",
                "23.61",
                date(2026, 4, 13),
                fees="2",
                taxes="5",
            ),
        ],
        [action()],
    )
    assert ledger.positions == ()
    assert ledger.total_realized_pnl == Decimal("272.20")
    sale = ledger.realized_sales[0]
    assert sale.total_cost_basis == Decimal("4915.00")
    assert sale.average_cost_basis == Decimal("4915") / Decimal("220")
    assert ledger.total_buy_fees == 0
    assert ledger.total_sell_fees == 2
    assert ledger.total_taxes == 5


@pytest.mark.parametrize(
    ("multiplier", "expected_quantity", "expected_average"),
    (("2", "20", "50"), ("0.5", "5.0", "200")),
)
def test_split_and_reverse_split_preserve_basis(
    multiplier: str, expected_quantity: str, expected_average: str
) -> None:
    split = action(multiplier).model_copy(update={"effective_date": date(2026, 3, 6)})
    ledger = TransactionEngine().build_ledger(
        [transaction("buy", TransactionType.BUY, "10", "100", date(2026, 3, 5))],
        [split],
    )
    position = ledger.positions[0]
    assert position.quantity == Decimal(expected_quantity)
    assert position.cost_basis == Decimal("1000")
    assert position.average_cost == Decimal(expected_average)
    assert ledger.total_realized_pnl == 0
    assert ledger.total_trading_expenses == 0


def test_action_requires_an_active_holding() -> None:
    with pytest.raises(InvalidTransactionHistoryError, match="active holding"):
        TransactionEngine().build_ledger([], [action()])


def test_replay_is_deterministic_and_buy_precedes_same_day_action() -> None:
    buy = transaction("buy", TransactionType.BUY, "10", "100", date(2026, 3, 31))
    expected = TransactionEngine().build_ledger([buy], [action("2")])
    assert TransactionEngine().build_ledger([buy], [action("2")]) == expected
    assert expected.positions[0].quantity == 20


def test_invalid_ratio_is_rejected() -> None:
    with pytest.raises(ValueError):
        action("0")


def test_cli_parses_explicit_corporate_action() -> None:
    arguments = build_parser().parse_args(
        [
            "corporate-action",
            "add",
            "--symbol",
            "00631L",
            "--market",
            "TWSE",
            "--effective-date",
            "2026-03-31",
            "--ratio",
            "22",
            "--source",
            "brokerage",
        ]
    )
    assert arguments.ratio == Decimal("22")
    assert arguments.effective_date == date(2026, 3, 31)


def test_sqlite_repository_and_application_round_trip(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "corporate-actions.db")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    repositories = create_repositories("sqlite", connection)
    repositories.transactions.add(
        transaction("buy", TransactionType.BUY, "10", "100", date(2026, 3, 5))
    )
    use_case = AddCorporateActionUseCase(
        repositories.corporate_actions, repositories.transactions
    )
    created = use_case.execute(
        AddCorporateActionCommand(
            "00631L",
            Market.TWSE,
            date(2026, 3, 31),
            Decimal("22"),
            "brokerage",
            "statement",
        )
    )
    persisted = repositories.corporate_actions.get_by_id(created.id)
    assert persisted == created
    assert (
        use_case.execute(
            AddCorporateActionCommand(
                "00631L",
                Market.TWSE,
                date(2026, 3, 31),
                Decimal("22"),
                "brokerage",
                "statement",
            )
        )
        == created
    )
