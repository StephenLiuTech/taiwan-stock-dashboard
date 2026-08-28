"""Annual P/L foundation and ledger-derived realized-sale tests."""

from datetime import date
from decimal import Decimal

import pytest

from domain import (
    Currency,
    InvestmentCostEvent,
    InvestmentCostType,
    Market,
    Transaction,
    TransactionType,
)
from services import AnnualPnlEngine, AnnualPnlFxUnavailableError, TransactionEngine


def tx(
    identifier: str,
    side: TransactionType,
    day: date,
    quantity: str,
    price: str,
    *,
    fees: str = "0",
    taxes: str = "0",
    currency: Currency = Currency.TWD,
) -> Transaction:
    return Transaction(
        id=identifier,
        symbol="2330" if currency is Currency.TWD else "MU",
        market=Market.TWSE if currency is Currency.TWD else Market.US,
        transaction_type=side,
        trade_date=day,
        settlement_date=day,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fees=Decimal(fees),
        taxes=Decimal(taxes),
        currency=currency,
    )


def test_realized_sale_uses_moving_average_and_sell_expenses() -> None:
    transactions = [
        tx("bootstrap", TransactionType.BUY, date(2026, 1, 1), "110", "100"),
        tx("may-1", TransactionType.BUY, date(2026, 5, 1), "20", "130"),
        tx("may-2", TransactionType.BUY, date(2026, 5, 2), "20", "160"),
        tx(
            "sale",
            TransactionType.SELL,
            date(2026, 8, 20),
            "50",
            "200",
            fees="10",
            taxes="30",
        ),
    ]

    ledger = TransactionEngine().build_ledger(transactions)

    sale = ledger.realized_sales[0]
    expected_average = Decimal("16800") / Decimal("150")
    assert sale.quantity_sold == Decimal("50")
    assert sale.average_cost_basis == expected_average
    assert sale.total_cost_basis == Decimal("5600")
    assert sale.gross_proceeds == Decimal("10000")
    assert sale.net_proceeds == Decimal("9960")
    assert sale.realized_pnl == Decimal("4360")
    assert sale.realized_return == Decimal("4360") / Decimal("5600")


def test_annual_pnl_counts_buy_expense_once_and_explicit_cost_events() -> None:
    transactions = [
        tx(
            "buy",
            TransactionType.BUY,
            date(2026, 1, 2),
            "10",
            "100",
            fees="3",
            taxes="1",
        ),
        tx(
            "sell",
            TransactionType.SELL,
            date(2026, 2, 2),
            "5",
            "120",
            fees="2",
            taxes="3",
        ),
    ]
    costs = [
        InvestmentCostEvent(
            id="interest",
            event_date=date(2026, 3, 1),
            cost_type=InvestmentCostType.FINANCING,
            amount=Decimal("20"),
            currency=Currency.TWD,
        ),
        InvestmentCostEvent(
            id="custody",
            event_date=date(2026, 3, 1),
            cost_type=InvestmentCostType.OTHER,
            amount=Decimal("6"),
            currency=Currency.TWD,
        ),
    ]

    result = AnnualPnlEngine().calculate(
        date(2026, 3, 2),
        transactions,
        Decimal("50"),
        [],
        costs,
        {},
    )

    assert result.realized_pnl_ytd == Decimal("95")
    assert result.other_cost_ytd == Decimal("10")  # BUY 4 + explicit 6
    assert result.financing_cost_ytd == Decimal("20")
    assert result.total_pnl_ytd == Decimal("115")


def test_historical_fx_is_required_and_uses_effective_transaction_date() -> None:
    transaction = tx(
        "usd-buy",
        TransactionType.BUY,
        date(2026, 6, 1),
        "2",
        "10",
        fees="1",
        currency=Currency.USD,
    )
    engine = AnnualPnlEngine()

    with pytest.raises(AnnualPnlFxUnavailableError):
        engine.calculate(date(2026, 8, 1), [transaction], Decimal("0"), [], [], {})

    result = engine.calculate(
        date(2026, 8, 1),
        [transaction],
        Decimal("0"),
        [],
        [],
        {(Currency.USD, date(2026, 6, 1)): Decimal("32")},
    )
    assert result.other_cost_ytd == Decimal("32")
    assert result.total_pnl_ytd == Decimal("-32")


def test_new_calendar_year_resets_flow_metrics() -> None:
    prior_sale = [
        tx("buy", TransactionType.BUY, date(2026, 1, 1), "1", "10"),
        tx("sell", TransactionType.SELL, date(2026, 2, 1), "1", "20"),
    ]
    result = AnnualPnlEngine().calculate(
        date(2027, 1, 2), prior_sale, Decimal("7"), [], [], {}
    )
    assert result.realized_pnl_ytd == 0
    assert result.other_cost_ytd == 0
    assert result.total_pnl_ytd == Decimal("7")
