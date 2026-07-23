"""Pure and application-level portfolio valuation tests."""

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from database import initialize_database, initialize_schema
from domain import Currency, Holding, Market, PortfolioValuation, PriceQuote
from pams.application import (
    MissingQuoteError,
    ValuatePortfolioUseCase,
    ValuationDataUnavailableError,
    ValuationRepositoryError,
)
from pams.cli import main
from repositories import SQLiteHoldingRepository, SQLitePriceQuoteRepository
from services import ValuationEngine


def holding(
    symbol: str = "2330", quantity: str = "10", average_cost: str = "80"
) -> Holding:
    return Holding(
        id=f"holding-{symbol}",
        symbol=symbol,
        name=symbol,
        market=Market.TWSE,
        currency=Currency.TWD,
        quantity=Decimal(quantity),
        average_cost=Decimal(average_cost),
    )


def quote(symbol: str = "2330", price: str = "100") -> PriceQuote:
    return PriceQuote(
        symbol=symbol,
        market=Market.TWSE,
        trade_date=date(2026, 7, 22),
        close_price=Decimal(price),
        currency=Currency.TWD,
        source="test",
    )


def test_single_holding_valuation() -> None:
    result = ValuationEngine().valuate([holding()], [quote()])
    item = result.holdings[0]
    assert item.cost_basis == Decimal("800")
    assert item.market_value == Decimal("1000")
    assert item.unrealized_pl == Decimal("200")
    assert item.unrealized_return == Decimal("0.25")
    assert result.total_return == Decimal("0.25")


def test_multiple_holdings_are_aggregated() -> None:
    result = ValuationEngine().valuate(
        [holding("2330"), holding("2317", "2", "50")],
        [quote("2330"), quote("2317", "75")],
    )
    assert result.total_cost == Decimal("900")
    assert result.total_market_value == Decimal("1150")
    assert result.total_unrealized_pl == Decimal("250")
    assert result.holdings[0].portfolio_weight == Decimal("1000") / Decimal("1150")
    assert result.holdings[1].portfolio_weight == Decimal("150") / Decimal("1150")
    assert sum(
        (item.portfolio_weight for item in result.holdings), Decimal("0")
    ) == Decimal("1")


@pytest.mark.parametrize(
    ("quantity", "cost", "price", "expected_return"),
    [("0", "80", "100", "0"), ("10", "0", "100", "0")],
)
def test_zero_quantity_or_cost_returns_zero(
    quantity: str, cost: str, price: str, expected_return: str
) -> None:
    result = ValuationEngine().valuate(
        [holding(quantity=quantity, average_cost=cost)], [quote(price=price)]
    )
    assert result.holdings[0].unrealized_return == Decimal(expected_return)
    assert result.total_return == Decimal(expected_return)
    if quantity == "0":
        assert result.holdings[0].portfolio_weight == Decimal("0")


def test_decimal_precision_is_not_converted_to_float() -> None:
    result = ValuationEngine().valuate(
        [holding(quantity="0.3", average_cost="0.1")], [quote(price="0.2")]
    )
    assert result.total_cost == Decimal("0.03")
    assert result.total_market_value == Decimal("0.06")
    assert result.total_return == Decimal("1")


def test_empty_portfolio_has_zero_totals_and_no_date() -> None:
    result = ValuationEngine().valuate([], [])
    assert result.valuation_date is None
    assert result.holdings == ()
    assert result.total_cost == result.total_market_value == Decimal("0")
    assert result.total_unrealized_pl == result.total_return == Decimal("0")


def test_valuation_dtos_are_immutable() -> None:
    result = ValuationEngine().valuate([holding()], [quote()])
    with pytest.raises(FrozenInstanceError):
        result.total_cost = Decimal("1")  # type: ignore[misc]


class HoldingRepo:
    def __init__(self, values: list[Holding]) -> None:
        self.values = values

    def list_all(self) -> list[Holding]:
        return self.values


class QuoteRepo:
    def __init__(self, values: list[PriceQuote]) -> None:
        self.values = {(item.symbol, item.market.value): item for item in values}

    def get_latest(self, symbol: str, market: str) -> PriceQuote | None:
        return self.values.get((symbol, market))


class FailingHoldingRepo:
    def list_all(self) -> list[Holding]:
        raise RuntimeError("sqlite holding detail")


class FailingQuoteRepo:
    def get_latest(self, symbol: str, market: str) -> PriceQuote | None:
        raise RuntimeError("sqlite quote detail")


def test_use_case_loads_inputs_and_returns_valuation() -> None:
    result = ValuatePortfolioUseCase(
        HoldingRepo([holding()]), QuoteRepo([quote()])
    ).execute()
    assert result.total_market_value == Decimal("1000")
    assert result.holdings[0].portfolio_weight == Decimal("1")


def test_use_case_returns_engine_result_without_recalculating() -> None:
    expected = PortfolioValuation(
        valuation_date=date(2026, 7, 22),
        total_cost=Decimal("1"),
        total_market_value=Decimal("2"),
        total_unrealized_pl=Decimal("1"),
        total_return=Decimal("1"),
        holdings=(),
    )

    class EngineStub:
        def valuate(
            self, holdings: list[Holding], quotes: list[PriceQuote]
        ) -> PortfolioValuation:
            return expected

    result = ValuatePortfolioUseCase(
        HoldingRepo([holding()]),
        QuoteRepo([quote()]),
        EngineStub(),  # type: ignore[arg-type]
    ).execute()
    assert result is expected


def test_use_case_raises_typed_error_for_missing_quote() -> None:
    with pytest.raises(MissingQuoteError, match="2330"):
        ValuatePortfolioUseCase(HoldingRepo([holding()]), QuoteRepo([])).execute()


def test_use_case_distinguishes_empty_portfolio_from_zero_valuation() -> None:
    with pytest.raises(ValuationDataUnavailableError, match="No portfolio holdings"):
        ValuatePortfolioUseCase(HoldingRepo([]), QuoteRepo([])).execute()


def test_holding_repository_failure_is_translated_with_original_cause() -> None:
    with pytest.raises(
        ValuationRepositoryError, match="load portfolio holdings"
    ) as exc:
        ValuatePortfolioUseCase(  # type: ignore[arg-type]
            FailingHoldingRepo(), QuoteRepo([])
        ).execute()
    assert isinstance(exc.value.__cause__, RuntimeError)
    assert "sqlite holding detail" not in str(exc.value)


def test_quote_repository_failure_is_translated_with_original_cause() -> None:
    with pytest.raises(ValuationRepositoryError, match="price quotes") as exc:
        ValuatePortfolioUseCase(  # type: ignore[arg-type]
            HoldingRepo([holding()]), FailingQuoteRepo()
        ).execute()
    assert isinstance(exc.value.__cause__, RuntimeError)
    assert "sqlite quote detail" not in str(exc.value)


@pytest.mark.parametrize("json_output", [False, True])
def test_portfolio_valuate_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], json_output: bool
) -> None:
    database_path = tmp_path / "valuation.db"
    connection = initialize_database(f"sqlite:///{database_path.as_posix()}")
    initialize_schema(connection)
    SQLiteHoldingRepository(connection).upsert(holding())
    SQLitePriceQuoteRepository(connection).upsert_many([quote()])
    connection.commit()
    connection.close()

    arguments = ["portfolio", "valuate", "--database", str(database_path)]
    if json_output:
        arguments.append("--json")
    assert main(arguments) == 0
    output = capsys.readouterr().out
    assert (
        ('"total_market_value": "1000"' in output)
        if json_output
        else ("Market Value: 1,000.00" in output)
    )
