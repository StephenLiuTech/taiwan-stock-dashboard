"""Application tests for transaction-derived holding queries."""

from datetime import date
from decimal import Decimal

import pytest

from domain import Currency, Holding, Market, PriceQuote, Transaction, TransactionType
from pams.application import (
    HoldingNotFoundError,
    InvalidHoldingHistoryError,
    QueryHoldingsUseCase,
)


def transaction(
    identifier: str,
    *,
    transaction_type: TransactionType = TransactionType.BUY,
    trade_date: date = date(2026, 7, 1),
    settlement_date: date | None = None,
    quantity: str = "1",
    price: str = "80",
) -> Transaction:
    return Transaction(
        id=identifier,
        symbol="2330",
        market=Market.TWSE,
        transaction_type=transaction_type,
        trade_date=trade_date,
        settlement_date=settlement_date or trade_date,
        quantity=Decimal(quantity),
        price=Decimal(price),
        currency=Currency.TWD,
    )


class TransactionRepository:
    def __init__(self, values: list[Transaction]) -> None:
        self.values = values

    def list_all(self) -> list[Transaction]:
        return list(self.values)

    def list_filtered(
        self,
        *,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Transaction]:
        return [
            item
            for item in self.values
            if (symbol is None or item.symbol == symbol)
            and (start_date is None or item.trade_date >= start_date)
            and (end_date is None or item.trade_date <= end_date)
        ]


class HoldingRepository:
    def list_all(self) -> list[Holding]:
        return [
            Holding(
                id="holding-2330",
                symbol="2330",
                name="TSMC",
                market=Market.TWSE,
                currency=Currency.TWD,
                quantity=Decimal("0"),
                average_cost=Decimal("0"),
            )
        ]


class QuoteRepository:
    def get_latest(self, symbol: str, market: str) -> PriceQuote | None:
        assert (symbol, market) == ("2330", "TWSE")
        return PriceQuote(
            symbol="2330",
            market=Market.TWSE,
            trade_date=date(2026, 7, 22),
            close_price=Decimal("100"),
            currency=Currency.TWD,
            source="test",
        )

    def get_latest_on_or_before(
        self, symbol: str, market: str, trade_date: date
    ) -> PriceQuote | None:
        quote = self.get_latest(symbol, market)
        return quote if quote is not None and quote.trade_date <= trade_date else None


class MissingQuoteRepository:
    def get_latest(self, symbol: str, market: str) -> None:
        del symbol, market
        return None

    def get_latest_on_or_before(
        self, symbol: str, market: str, trade_date: date
    ) -> None:
        del symbol, market, trade_date
        return None


class HistoricalQuoteRepository:
    def __init__(self, values: list[PriceQuote]) -> None:
        self.values = values

    def get_latest(self, symbol: str, market: str) -> PriceQuote | None:
        matches = [
            item
            for item in self.values
            if item.symbol == symbol and item.market.value == market
        ]
        return max(matches, key=lambda item: item.trade_date, default=None)

    def get_latest_on_or_before(
        self, symbol: str, market: str, trade_date: date
    ) -> PriceQuote | None:
        matches = [
            item
            for item in self.values
            if item.symbol == symbol
            and item.market.value == market
            and item.trade_date <= trade_date
        ]
        return max(matches, key=lambda item: item.trade_date, default=None)


def quote(trade_date: date, close: str) -> PriceQuote:
    return PriceQuote(
        symbol="2330",
        market=Market.TWSE,
        trade_date=trade_date,
        close_price=Decimal(close),
        currency=Currency.TWD,
        source="test",
    )


def use_case(values: list[Transaction]) -> QueryHoldingsUseCase:
    return QueryHoldingsUseCase(
        TransactionRepository(values),  # type: ignore[arg-type]
        HoldingRepository(),  # type: ignore[arg-type]
        QuoteRepository(),  # type: ignore[arg-type]
    )


def test_query_uses_weighted_transaction_position_and_shared_valuation() -> None:
    result = use_case(
        [
            transaction("first", quantity="2", price="80"),
            transaction(
                "second",
                trade_date=date(2026, 7, 2),
                settlement_date=date(2026, 7, 6),
                quantity="1",
                price="110",
            ),
        ]
    ).execute()

    item = result.holdings[0]
    assert item.quantity == Decimal("3")
    assert item.total_cost == Decimal("270")
    assert item.average_cost == Decimal("90")
    assert item.latest_price == Decimal("100")
    assert item.market_value == Decimal("300")
    assert item.unrealized_pl == Decimal("30")
    assert item.unrealized_return == Decimal("30") / Decimal("270")


def test_sell_reduces_quantity_using_moving_average_cost() -> None:
    result = use_case(
        [
            transaction("buy", quantity="3", price="90"),
            transaction(
                "sell",
                transaction_type=TransactionType.SELL,
                trade_date=date(2026, 7, 5),
                settlement_date=date(2026, 7, 8),
                quantity="1",
                price="120",
            ),
        ]
    ).execute("2330")

    assert result.holdings[0].quantity == Decimal("2")
    assert result.holdings[0].average_cost == Decimal("90")
    assert result.holdings[0].total_cost == Decimal("180")


def test_holding_dates_use_trade_date_not_settlement_date() -> None:
    result = use_case(
        [
            transaction(
                "delayed-settlement",
                trade_date=date(2026, 7, 1),
                settlement_date=date(2026, 7, 10),
            ),
            transaction(
                "later-trade",
                trade_date=date(2026, 7, 5),
                settlement_date=date(2026, 7, 6),
            ),
        ]
    ).execute("2330")

    item = result.holdings[0]
    assert item.first_trade_date == date(2026, 7, 1)
    assert item.latest_trade_date == date(2026, 7, 5)
    assert item.transaction_count == 2


def test_empty_and_missing_holdings_are_clean_outcomes() -> None:
    assert use_case([]).execute().holdings == ()
    with pytest.raises(HoldingNotFoundError, match="No active"):
        use_case([]).execute("2330")


def test_existing_holding_without_quote_retains_cost_facts() -> None:
    query = QueryHoldingsUseCase(
        TransactionRepository([transaction("buy", quantity="2", price="80")]),  # type: ignore[arg-type]
        HoldingRepository(),  # type: ignore[arg-type]
        MissingQuoteRepository(),  # type: ignore[arg-type]
    )

    result = query.execute("2330")
    item = result.holdings[0]
    assert result.valuation_date is None
    assert item.quantity == Decimal("2")
    assert item.average_cost == Decimal("80")
    assert item.total_cost == Decimal("160")
    assert item.latest_price is None
    assert item.market_value is None
    assert item.unrealized_pl is None
    assert item.unrealized_return is None


@pytest.mark.parametrize(
    "transactions, message",
    [
        (
            [transaction("a-sell", transaction_type=TransactionType.SELL)],
            "SELL before BUY",
        ),
        (
            [
                transaction("a-buy", quantity="1"),
                transaction(
                    "b-sell", transaction_type=TransactionType.SELL, quantity="2"
                ),
            ],
            "SELL exceeds held quantity",
        ),
    ],
)
def test_invalid_sell_history_is_a_typed_holding_query_error(
    transactions: list[Transaction], message: str
) -> None:
    with pytest.raises(InvalidHoldingHistoryError, match=message):
        use_case(transactions).execute()


def test_as_of_excludes_later_transactions_and_preserves_current_query() -> None:
    query = use_case(
        [
            transaction("a-buy", trade_date=date(2026, 7, 1), quantity="2"),
            transaction("b-buy", trade_date=date(2026, 7, 10), quantity="3"),
        ]
    )
    assert query.execute().holdings[0].quantity == Decimal("5")
    historical = query.execute(as_of_date=date(2026, 7, 5))
    assert historical.as_of_date == date(2026, 7, 5)
    assert historical.holdings[0].quantity == Decimal("2")


def test_position_opened_after_as_of_is_historically_missing() -> None:
    query = use_case([transaction("buy", trade_date=date(2026, 7, 10))])
    assert query.execute(as_of_date=date(2026, 7, 5)).holdings == ()
    with pytest.raises(HoldingNotFoundError, match="No active"):
        query.execute("2330", as_of_date=date(2026, 7, 5))


def test_position_closure_is_effective_inclusively_on_trade_date() -> None:
    query = use_case(
        [
            transaction("z-buy", trade_date=date(2026, 7, 1), quantity="2"),
            transaction(
                "a-sell",
                transaction_type=TransactionType.SELL,
                trade_date=date(2026, 7, 5),
                settlement_date=date(2026, 7, 10),
                quantity="2",
            ),
        ]
    )
    assert query.execute(as_of_date=date(2026, 7, 4)).holdings[0].quantity == Decimal(
        "2"
    )
    assert query.execute(as_of_date=date(2026, 7, 5)).holdings == ()


def test_same_day_buy_before_sell_is_preserved_at_cutoff() -> None:
    query = use_case(
        [
            transaction(
                "a-sell",
                transaction_type=TransactionType.SELL,
                trade_date=date(2026, 7, 5),
                quantity="1",
            ),
            transaction("z-buy", trade_date=date(2026, 7, 5), quantity="2"),
        ]
    )
    assert query.execute(as_of_date=date(2026, 7, 5)).holdings[0].quantity == Decimal(
        "1"
    )


def test_historical_query_uses_latest_quote_not_after_cutoff() -> None:
    query = QueryHoldingsUseCase(
        TransactionRepository([transaction("buy", quantity="2")]),  # type: ignore[arg-type]
        HoldingRepository(),  # type: ignore[arg-type]
        HistoricalQuoteRepository(
            [quote(date(2026, 7, 3), "90"), quote(date(2026, 7, 10), "120")]
        ),  # type: ignore[arg-type]
    )
    result = query.execute(as_of_date=date(2026, 7, 5))
    assert result.valuation_date == date(2026, 7, 3)
    assert result.holdings[0].latest_price == Decimal("90")
    assert result.holdings[0].quote_date == date(2026, 7, 3)


def test_no_quote_before_cutoff_keeps_historical_cost_only() -> None:
    query = QueryHoldingsUseCase(
        TransactionRepository([transaction("buy", quantity="2")]),  # type: ignore[arg-type]
        HoldingRepository(),  # type: ignore[arg-type]
        HistoricalQuoteRepository([quote(date(2026, 7, 10), "120")]),  # type: ignore[arg-type]
    )
    result = query.execute(as_of_date=date(2026, 7, 5))
    item = result.holdings[0]
    assert result.valuation_date is None
    assert item.total_cost == Decimal("160")
    assert item.latest_price is None
    assert item.market_value is None
    assert item.quote_date is None


def test_as_of_excludes_later_buy_and_preserves_cross_date_oversell() -> None:
    query = use_case(
        [
            transaction(
                "a-sell",
                transaction_type=TransactionType.SELL,
                trade_date=date(2026, 7, 2),
                quantity="1",
            ),
            transaction("b-buy", trade_date=date(2026, 7, 3), quantity="2"),
        ]
    )
    with pytest.raises(InvalidHoldingHistoryError, match="SELL before BUY"):
        query.execute(as_of_date=date(2026, 7, 2))
