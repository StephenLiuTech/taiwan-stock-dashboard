"""Domain model validation tests."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from domain import (
    Currency,
    Dividend,
    Holding,
    Liability,
    LiabilityType,
    Market,
    PriceQuote,
    Transaction,
    TransactionType,
)


def holding(**changes: object) -> Holding:
    values: dict[str, object] = {
        "symbol": "2330",
        "name": "TSMC",
        "market": Market.TWSE,
        "currency": Currency.TWD,
        "quantity": Decimal("10"),
        "average_cost": Decimal("500.25"),
    }
    values.update(changes)
    return Holding.model_validate(values)


def test_holding_constructs_with_decimal_values() -> None:
    assert holding().average_cost == Decimal("500.25")


def test_symbol_is_normalized() -> None:
    assert holding(symbol="  abc ").symbol == "ABC"


@pytest.mark.parametrize("field", ["quantity", "average_cost"])
def test_holding_rejects_negative_values(field: str) -> None:
    with pytest.raises(ValidationError):
        holding(**{field: Decimal("-0.01")})


def test_transaction_rejects_settlement_before_trade() -> None:
    with pytest.raises(ValidationError):
        Transaction(
            symbol="2330",
            market=Market.TWSE,
            transaction_type=TransactionType.BUY,
            trade_date=date(2026, 2, 2),
            settlement_date=date(2026, 2, 1),
            quantity=Decimal("1"),
            price=Decimal("1"),
            currency=Currency.TWD,
        )


def test_transaction_rejects_negative_fees() -> None:
    with pytest.raises(ValidationError):
        Transaction(
            symbol="2330",
            market=Market.TWSE,
            transaction_type=TransactionType.BUY,
            trade_date=date(2026, 2, 1),
            settlement_date=date(2026, 2, 2),
            quantity=Decimal("1"),
            price=Decimal("1"),
            fees=Decimal("-1"),
            currency=Currency.TWD,
        )


def test_dividend_rejects_payment_before_ex_date() -> None:
    with pytest.raises(ValidationError):
        Dividend(
            symbol="2330",
            market=Market.TWSE,
            ex_dividend_date=date(2026, 3, 2),
            payment_date=date(2026, 3, 1),
            amount_per_share=Decimal("1"),
            currency=Currency.TWD,
            shares_eligible=Decimal("10"),
            gross_amount=Decimal("10"),
            net_amount=Decimal("10"),
        )


def test_dividend_rejects_inconsistent_net_amount() -> None:
    with pytest.raises(ValidationError):
        Dividend(
            symbol="2330",
            market=Market.TWSE,
            ex_dividend_date=date(2026, 3, 2),
            amount_per_share=Decimal("1"),
            currency=Currency.TWD,
            shares_eligible=Decimal("10"),
            gross_amount=Decimal("10"),
            withholding_tax=Decimal("1"),
            net_amount=Decimal("10"),
        )


def test_liability_rate_uses_decimal_fraction_convention() -> None:
    liability = Liability(
        liability_type=LiabilityType.OTHER,
        principal=Decimal("100"),
        annual_interest_rate=Decimal("0.05"),
        currency=Currency.TWD,
    )
    assert liability.annual_interest_rate == Decimal("0.05")


def test_liability_rejects_rate_above_one() -> None:
    with pytest.raises(ValidationError):
        Liability(
            liability_type=LiabilityType.OTHER,
            principal=Decimal("100"),
            annual_interest_rate=Decimal("1.01"),
            currency=Currency.TWD,
        )


def test_price_quote_requires_explicit_market_and_currency() -> None:
    with pytest.raises(ValidationError):
        PriceQuote(
            symbol="2330",
            trade_date=date(2026, 1, 1),
            close_price=Decimal("1"),
            source="manual",
        )
