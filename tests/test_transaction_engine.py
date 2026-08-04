"""Exact Decimal tests for the ordered transaction ledger engine."""

from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from decimal import Decimal

import pytest

from domain import (
    Currency,
    Holding,
    HoldingType,
    Market,
    Transaction,
    TransactionType,
)
from pams.application import RebuildHoldingsUseCase
from services import (
    HoldingProjectionMetadata,
    InvalidTransactionHistoryError,
    OversellError,
    TransactionEngine,
    UnsupportedTransactionTypeError,
)


def transaction(
    identifier: str,
    direction: TransactionType,
    quantity: str,
    price: str,
    *,
    symbol: str = "2330",
    market: Market = Market.TWSE,
    currency: Currency = Currency.TWD,
    fees: str = "0",
    taxes: str = "0",
    trade_date: date = date(2026, 1, 2),
    settlement_date: date | None = None,
) -> Transaction:
    return Transaction(
        id=identifier,
        symbol=symbol,
        market=market,
        transaction_type=direction,
        trade_date=trade_date,
        settlement_date=settlement_date or trade_date + timedelta(days=2),
        quantity=Decimal(quantity),
        price=Decimal(price),
        fees=Decimal(fees),
        taxes=Decimal(taxes),
        currency=currency,
    )


def test_one_buy_tracks_fee_without_increasing_broker_cost() -> None:
    ledger = TransactionEngine().build_ledger(
        [transaction("buy-1", TransactionType.BUY, "100", "50", fees="10")]
    )
    position = ledger.positions[0]
    assert position.quantity == Decimal("100")
    assert position.cost_basis == Decimal("5000")
    assert position.average_cost == Decimal("50")
    assert position.realized_pnl == Decimal("0")
    assert ledger.total_buy_fees == Decimal("10")
    assert ledger.total_trading_expenses == Decimal("10")


def test_multiple_buys_use_exact_weighted_average() -> None:
    ledger = TransactionEngine().build_ledger(
        [
            transaction("buy-1", TransactionType.BUY, "100", "10"),
            transaction("buy-2", TransactionType.BUY, "50", "20"),
        ]
    )
    position = ledger.positions[0]
    assert position.quantity == Decimal("150")
    assert position.cost_basis == Decimal("2000")
    assert position.average_cost == Decimal("2000") / Decimal("150")


def test_current_holdings_aggregate_same_day_buys_by_instrument() -> None:
    existing = [
        Holding(
            id="holding-0050",
            symbol="0050",
            name="ETF",
            market=Market.TWSE,
            currency=Currency.TWD,
            quantity=Decimal("5800"),
            average_cost=Decimal("83.95"),
            holding_type=HoldingType.ETF,
        )
    ]
    same_day = date(2026, 7, 24)
    projected = TransactionEngine().project_current_holdings(
        [
            transaction(
                "buy-5800",
                TransactionType.BUY,
                "5800",
                "83.95",
                symbol="0050",
                trade_date=same_day,
                settlement_date=same_day,
            ),
            transaction(
                "buy-100",
                TransactionType.BUY,
                "100",
                "101.70",
                symbol="0050",
                trade_date=same_day,
                settlement_date=same_day,
            ),
        ],
        existing,
    )

    assert len(projected) == 1
    assert projected[0].quantity == Decimal("5900")
    assert projected[0].quantity * projected[0].average_cost == Decimal("497080")
    assert projected[0].average_cost == Decimal("497080") / Decimal("5900")


def test_current_holdings_do_not_merge_same_symbol_across_markets() -> None:
    projected = TransactionEngine().project_current_holdings(
        [
            transaction(
                "twse-buy",
                TransactionType.BUY,
                "10",
                "20",
                symbol="0050",
                market=Market.TWSE,
            ),
            transaction(
                "tpex-buy",
                TransactionType.BUY,
                "5",
                "30",
                symbol="0050",
                market=Market.TPEX,
            ),
        ],
        [],
    )

    assert [(item.market, item.quantity, item.average_cost) for item in projected] == [
        (Market.TPEX, Decimal("5"), Decimal("30")),
        (Market.TWSE, Decimal("10"), Decimal("20")),
    ]


def test_partial_sell_keeps_average_cost_and_deducts_fees_and_tax() -> None:
    ledger = TransactionEngine().build_ledger(
        [
            transaction("buy", TransactionType.BUY, "100", "50", fees="10"),
            transaction(
                "sell",
                TransactionType.SELL,
                "40",
                "60",
                fees="5",
                taxes="15",
                trade_date=date(2026, 1, 3),
            ),
        ]
    )
    position = ledger.positions[0]
    assert position.quantity == Decimal("60")
    assert position.average_cost == Decimal("50")
    assert position.cost_basis == Decimal("3000")
    assert position.realized_pnl == Decimal("380")
    assert ledger.total_realized_pnl == Decimal("380")
    assert ledger.total_buy_fees == Decimal("10")
    assert ledger.total_sell_fees == Decimal("5")
    assert ledger.total_taxes == Decimal("15")
    assert ledger.total_trading_expenses == Decimal("30")


def test_full_liquidation_is_excluded_but_realized_pnl_is_retained() -> None:
    ledger = TransactionEngine().build_ledger(
        [
            transaction("buy", TransactionType.BUY, "10", "10"),
            transaction(
                "sell",
                TransactionType.SELL,
                "10",
                "12",
                fees="1",
                taxes="2",
                trade_date=date(2026, 1, 3),
            ),
        ]
    )
    assert ledger.positions == ()
    assert ledger.total_realized_pnl == Decimal("17")


def test_rebuy_after_full_liquidation_starts_new_cost_basis() -> None:
    ledger = TransactionEngine().build_ledger(
        [
            transaction("a-buy", TransactionType.BUY, "10", "10"),
            transaction(
                "b-sell",
                TransactionType.SELL,
                "10",
                "12",
                trade_date=date(2026, 1, 3),
            ),
            transaction(
                "c-rebuy",
                TransactionType.BUY,
                "5",
                "8",
                trade_date=date(2026, 1, 4),
            ),
        ]
    )
    position = ledger.positions[0]
    assert position.quantity == Decimal("5")
    assert position.cost_basis == Decimal("40")
    assert position.average_cost == Decimal("8")
    assert position.realized_pnl == Decimal("20")


def test_multiple_symbols_and_group_dimensions_remain_independent() -> None:
    ledger = TransactionEngine().build_ledger(
        [
            transaction("twse-twd", TransactionType.BUY, "1", "10"),
            transaction(
                "tpex-twd",
                TransactionType.BUY,
                "2",
                "20",
                market=Market.TPEX,
            ),
            transaction(
                "twse-usd",
                TransactionType.BUY,
                "3",
                "30",
                currency=Currency.USD,
            ),
            transaction(
                "other-symbol",
                TransactionType.BUY,
                "4",
                "40",
                symbol="8299",
                market=Market.TPEX,
            ),
        ]
    )
    assert len(ledger.positions) == 4
    assert {(item.symbol, item.market, item.currency) for item in ledger.positions} == {
        ("2330", Market.TWSE, Currency.TWD),
        ("2330", Market.TPEX, Currency.TWD),
        ("2330", Market.TWSE, Currency.USD),
        ("8299", Market.TPEX, Currency.TWD),
    }


def test_same_day_buy_precedes_lexically_earlier_sell() -> None:
    buy = transaction("z-buy", TransactionType.BUY, "10", "10")
    sell = transaction("a-sell", TransactionType.SELL, "5", "12")
    supplied = [sell, buy]
    original_ids = [item.id for item in supplied]
    ledger = TransactionEngine().build_ledger(supplied)
    assert ledger.positions[0].quantity == Decimal("5")
    assert [item.id for item in supplied] == original_ids


def test_same_day_order_ignores_settlement_date_and_uses_id() -> None:
    later_settlement_buy = transaction(
        "a-buy",
        TransactionType.BUY,
        "5",
        "10",
        settlement_date=date(2026, 1, 5),
    )
    earlier_settlement_sell = transaction(
        "z-sell",
        TransactionType.SELL,
        "1",
        "10",
        settlement_date=date(2026, 1, 3),
    )
    ledger = TransactionEngine().build_ledger(
        [earlier_settlement_sell, later_settlement_buy]
    )
    assert ledger.positions[0].quantity == Decimal("4")


def test_same_day_sell_input_then_buy_still_applies_buy_first() -> None:
    transactions = [
        transaction("a-sell", TransactionType.SELL, "1", "11"),
        transaction("b-buy", TransactionType.BUY, "5", "10"),
    ]
    ledger = TransactionEngine().build_ledger(transactions)
    assert ledger.positions[0].quantity == Decimal("4")


def test_arbitrary_repository_order_reconstructs_identically() -> None:
    transactions = [
        transaction("a-buy", TransactionType.BUY, "5", "10"),
        transaction("b-buy", TransactionType.BUY, "3", "20"),
        transaction("c-sell", TransactionType.SELL, "2", "30"),
        transaction("d-sell", TransactionType.SELL, "1", "25"),
    ]
    engine = TransactionEngine()
    expected = engine.build_ledger(transactions)
    assert engine.build_ledger(list(reversed(transactions))) == expected
    assert (
        engine.build_ledger(
            [transactions[1], transactions[3], transactions[2], transactions[0]]
        )
        == expected
    )


def test_same_day_oversell_uses_total_available_buys() -> None:
    transactions = [
        transaction("z-buy", TransactionType.BUY, "1", "10"),
        transaction("a-sell", TransactionType.SELL, "4", "12"),
        transaction("a-buy", TransactionType.BUY, "2", "11"),
    ]
    with pytest.raises(OversellError, match="a-sell"):
        TransactionEngine().build_ledger(transactions)


def test_cross_date_sell_before_later_buy_remains_invalid() -> None:
    transactions = [
        transaction(
            "z-sell",
            TransactionType.SELL,
            "1",
            "12",
            trade_date=date(2026, 1, 2),
        ),
        transaction(
            "a-buy",
            TransactionType.BUY,
            "1",
            "10",
            trade_date=date(2026, 1, 3),
        ),
    ]
    with pytest.raises(InvalidTransactionHistoryError, match="z-sell"):
        TransactionEngine().build_ledger(transactions)


def test_oversell_and_sell_before_buy_have_symbol_and_id() -> None:
    with pytest.raises(InvalidTransactionHistoryError, match=r"2330.*sell-first"):
        TransactionEngine().build_ledger(
            [transaction("sell-first", TransactionType.SELL, "1", "10")]
        )
    with pytest.raises(OversellError, match=r"2330.*oversell"):
        TransactionEngine().build_ledger(
            [
                transaction("buy", TransactionType.BUY, "1", "10"),
                transaction("oversell", TransactionType.SELL, "2", "10"),
            ]
        )


def test_selling_exactly_available_quantity_closes_position() -> None:
    ledger = TransactionEngine().build_ledger(
        [
            transaction("a-buy", TransactionType.BUY, "2", "10"),
            transaction("b-sell", TransactionType.SELL, "2", "12"),
        ]
    )
    assert ledger.positions == ()


def test_zero_quantity_and_unsupported_type_are_rejected() -> None:
    zero_values = transaction("zero", TransactionType.BUY, "1", "10").model_dump()
    zero_values["quantity"] = Decimal("0")
    zero = Transaction.model_construct(**zero_values)
    with pytest.raises(InvalidTransactionHistoryError, match=r"2330.*zero"):
        TransactionEngine().build_ledger([zero])
    unsupported_values = transaction(
        "unsupported", TransactionType.BUY, "1", "10"
    ).model_dump()
    unsupported_values["transaction_type"] = "split"
    unsupported = Transaction.model_construct(**unsupported_values)
    with pytest.raises(UnsupportedTransactionTypeError, match=r"2330.*unsupported"):
        TransactionEngine().build_ledger([unsupported])


def test_invalid_market_or_currency_is_rejected() -> None:
    invalid_values = transaction("invalid", TransactionType.BUY, "1", "10").model_dump()
    invalid_values["market"] = "OTHER"
    invalid = Transaction.model_construct(**invalid_values)
    with pytest.raises(InvalidTransactionHistoryError, match=r"2330.*invalid"):
        TransactionEngine().build_ledger([invalid])


def test_decimal_precision_is_not_quantized_or_converted_to_float() -> None:
    ledger = TransactionEngine().build_ledger(
        [transaction("precise", TransactionType.BUY, "3", "0.123456789", fees="0.01")]
    )
    position = ledger.positions[0]
    assert position.cost_basis == Decimal("0.370370367")
    assert position.average_cost == Decimal("0.123456789")
    assert isinstance(position.average_cost, Decimal)


def test_ledger_models_are_immutable() -> None:
    ledger = TransactionEngine().build_ledger(
        [transaction("buy", TransactionType.BUY, "1", "10")]
    )
    with pytest.raises(FrozenInstanceError):
        ledger.total_realized_pnl = Decimal("1")  # type: ignore[misc]


def test_holding_projection_uses_metadata_and_stable_ids() -> None:
    engine = TransactionEngine()
    ledger = engine.build_ledger([transaction("buy", TransactionType.BUY, "2", "10")])
    key = ("2330", Market.TWSE, Currency.TWD)
    metadata = {key: HoldingProjectionMetadata("TSMC", HoldingType.STOCK)}
    generated = engine.project_holdings(ledger, metadata)
    assert generated[0].id == "holding-ledger-twse-twd-2330"
    assert generated[0].name == "TSMC"
    existing = Holding(
        id="stable-existing-id",
        symbol="2330",
        name="Old name",
        market=Market.TWSE,
        currency=Currency.TWD,
        quantity=Decimal("99"),
        average_cost=Decimal("99"),
    )
    projected = engine.project_holdings(ledger, metadata, [existing])
    assert projected[0].id == "stable-existing-id"
    assert projected[0].quantity == Decimal("2")
    assert projected[0].average_cost == Decimal("10")


def test_projection_requires_metadata() -> None:
    ledger = TransactionEngine().build_ledger(
        [transaction("buy", TransactionType.BUY, "1", "10")]
    )
    with pytest.raises(InvalidTransactionHistoryError, match="2330"):
        TransactionEngine().project_holdings(ledger, {})


class FakeTransactionRepository:
    def __init__(self, transactions: list[Transaction]) -> None:
        self.transactions = transactions
        self.write_calls = 0

    def list_all(self) -> list[Transaction]:
        return list(self.transactions)


class FakeHoldingRepository:
    def __init__(self, holdings: list[Holding]) -> None:
        self.holdings = holdings
        self.write_calls = 0

    def list_all(self) -> list[Holding]:
        return list(self.holdings)


def test_application_use_case_returns_immutable_dto_without_writes() -> None:
    transactions = FakeTransactionRepository(
        [transaction("buy", TransactionType.BUY, "5", "20", fees="1")]
    )
    holdings = FakeHoldingRepository([])
    key = ("2330", Market.TWSE, Currency.TWD)
    result = RebuildHoldingsUseCase(
        transactions,  # type: ignore[arg-type]
        holdings,  # type: ignore[arg-type]
        {key: HoldingProjectionMetadata("TSMC", HoldingType.STOCK)},
    ).execute()
    assert result.transaction_count == 1
    assert result.persisted is False
    assert result.positions[0].cost_basis == Decimal("100")
    assert result.projected_holdings[0].average_cost == Decimal("20")
    assert result.total_buy_fees == Decimal("1")
    assert result.total_sell_fees == Decimal("0")
    assert result.total_taxes == Decimal("0")
    assert result.total_trading_expenses == Decimal("1")
    assert transactions.write_calls == 0
    assert holdings.write_calls == 0
