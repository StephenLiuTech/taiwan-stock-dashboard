"""Annual P/L foundation and ledger-derived realized-sale tests."""

from datetime import date
from decimal import Decimal

import pytest

from domain import (
    Currency,
    DividendEvent,
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


def test_realized_pnl_by_symbol_uses_ledger_values_and_descending_order() -> None:
    transactions = [
        tx("2330-buy", TransactionType.BUY, date(2026, 1, 1), "10", "100"),
        tx("2330-sell", TransactionType.SELL, date(2026, 2, 1), "5", "150"),
        tx("8299-buy", TransactionType.BUY, date(2026, 1, 1), "10", "100").model_copy(
            update={"symbol": "8299", "market": Market.TPEX}
        ),
        tx("8299-sell", TransactionType.SELL, date(2026, 2, 1), "5", "80").model_copy(
            update={"symbol": "8299", "market": Market.TPEX}
        ),
    ]

    result = AnnualPnlEngine().realized_pnl_by_symbol(
        date(2026, 8, 30), transactions, {}
    )

    assert [(item.symbol, item.realized_pnl) for item in result] == [
        ("2330", Decimal("250")),
        ("8299", Decimal("-100")),
    ]


def test_realized_total_excludes_unrealized_and_splits_audited_expenses() -> None:
    transactions = [
        tx("buy", TransactionType.BUY, date(2026, 1, 1), "10", "100", fees="5"),
        tx(
            "sell",
            TransactionType.SELL,
            date(2026, 2, 1),
            "5",
            "120",
            fees="2",
            taxes="3",
        ),
    ]
    dividends = [
        DividendEvent(
            source_event_id="dividend",
            symbol="2330",
            market=Market.TWSE,
            name="TSMC",
            dividend_year=2026,
            ex_dividend_date=date(2026, 1, 2),
            payment_date=date(2026, 3, 1),
            cash_dividend_per_share=Decimal("2"),
            source="official",
        )
    ]
    costs = [
        InvestmentCostEvent(
            id="financing-interest:liability-margin-financing:2026-03-01",
            event_date=date(2026, 3, 1),
            cost_type=InvestmentCostType.FINANCING,
            amount=Decimal("7"),
            currency=Currency.TWD,
        ),
        InvestmentCostEvent(
            id="financing-interest:liability-stock-pledge:2026-03-01",
            event_date=date(2026, 3, 1),
            cost_type=InvestmentCostType.FINANCING,
            amount=Decimal("11"),
            currency=Currency.TWD,
        ),
    ]

    result = AnnualPnlEngine().realized_performance(
        date(2026, 3, 2),
        date(2026, 3, 2),
        transactions,
        dividends,
        costs,
        {},
    )

    assert result.realized_trading_pnl_ytd == Decimal("95")
    assert result.dividend_income_ytd == Decimal("20")
    assert result.margin_financing_interest_ytd == Decimal("7")
    assert result.stock_pledge_interest_ytd == Decimal("11")
    assert result.buy_brokerage_fees_ytd == Decimal("5")
    assert result.realized_total_pnl_ytd == Decimal("92")


def test_0050_payment_dates_produce_approved_2026_realized_total() -> None:
    transactions = [
        Transaction(
            id="0050-opening",
            symbol="0050",
            market=Market.TWSE,
            transaction_type=TransactionType.BUY,
            trade_date=date(2026, 1, 1),
            settlement_date=date(2026, 1, 1),
            quantity=Decimal("2150"),
            price=Decimal("1"),
            currency=Currency.TWD,
        ),
        Transaction(
            id="0050-additions",
            symbol="0050",
            market=Market.TWSE,
            transaction_type=TransactionType.BUY,
            trade_date=date(2026, 7, 1),
            settlement_date=date(2026, 7, 1),
            quantity=Decimal("3650"),
            price=Decimal("1"),
            currency=Currency.TWD,
        ),
        tx("realized-buy", TransactionType.BUY, date(2026, 1, 1), "1", "0"),
        tx(
            "realized-sell",
            TransactionType.SELL,
            date(2026, 8, 1),
            "1",
            "345393.4500000000000000000013",
        ),
        tx(
            "fee-only-buy",
            TransactionType.BUY,
            date(2026, 8, 2),
            "1",
            "1",
            fees="5839.154900",
        ),
    ]
    dividends = [
        DividendEvent(
            source_event_id="existing-recognized-dividends",
            symbol="2330",
            market=Market.TWSE,
            name="TSMC",
            dividend_year=2026,
            ex_dividend_date=date(2026, 1, 2),
            payment_date=date(2026, 1, 31),
            cash_dividend_per_share=Decimal("118044.64737350"),
            source="existing recognized official events fixture",
        ),
        DividendEvent(
            source_event_id="0050-january",
            symbol="0050",
            market=Market.TWSE,
            name="Yuanta Taiwan 50",
            dividend_year=2026,
            ex_dividend_date=date(2026, 1, 22),
            record_date=date(2026, 1, 28),
            payment_date=date(2026, 2, 11),
            cash_dividend_per_share=Decimal("1.00"),
            source="TWSE ETF",
        ),
        DividendEvent(
            source_event_id="0050-july",
            symbol="0050",
            market=Market.TWSE,
            name="Yuanta Taiwan 50",
            dividend_year=2026,
            ex_dividend_date=date(2026, 7, 21),
            record_date=date(2026, 7, 27),
            payment_date=date(2026, 8, 10),
            cash_dividend_per_share=Decimal("0.60"),
            source="TWSE ETF",
        ),
    ]
    costs = [
        InvestmentCostEvent(
            id="financing-catchup-margin-through-2026-08-29",
            event_date=date(2026, 8, 29),
            cost_type=InvestmentCostType.FINANCING,
            amount=Decimal("8149.1194520547945205479452055"),
            currency=Currency.TWD,
        ),
        InvestmentCostEvent(
            id="financing-catchup-stock-pledge-through-2026-08-29",
            event_date=date(2026, 8, 29),
            cost_type=InvestmentCostType.FINANCING,
            amount=Decimal("30837.6027397260273972602739797"),
            currency=Currency.TWD,
        ),
    ]

    result = AnnualPnlEngine().realized_performance(
        date(2026, 8, 29),
        date(2026, 8, 28),
        transactions,
        dividends,
        costs,
        {},
    )

    assert result.dividend_income_ytd == Decimal("123674.64737350")
    assert result.realized_total_pnl_ytd == Decimal("424242.2202817191780821917821")
