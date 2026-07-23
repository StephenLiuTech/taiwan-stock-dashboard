"""Daily report builder tests."""

from datetime import date
from decimal import Decimal

from domain import HoldingValuation, Market, PortfolioValuation
from pams.reporting import DailyReportBuilder


def position(symbol: str, market_value: str, unrealized: str) -> HoldingValuation:
    return HoldingValuation(
        symbol=symbol,
        market=Market.TWSE,
        quantity=Decimal("10"),
        average_cost=Decimal("80"),
        last_price=Decimal("100"),
        cost_basis=Decimal("800"),
        market_value=Decimal(market_value),
        unrealized_pl=Decimal(unrealized),
        unrealized_return=Decimal(unrealized) / Decimal("800"),
        portfolio_weight=Decimal("0.5"),
    )


def valuation(
    holdings: tuple[HoldingValuation, ...] = (),
) -> PortfolioValuation:
    return PortfolioValuation(
        valuation_date=date(2026, 7, 22) if holdings else None,
        total_cost=Decimal("0"),
        total_market_value=Decimal("0"),
        total_unrealized_pl=Decimal("0"),
        total_return=Decimal("0"),
        holdings=holdings,
    )


def test_empty_portfolio_builds_empty_report() -> None:
    report = DailyReportBuilder().build(valuation())
    assert report.report_date is None
    assert report.largest_positions == ()
    assert report.top_gainers == ()
    assert report.top_losers == ()
    assert report.portfolio == ()


def test_one_holding_is_present_in_every_report_section() -> None:
    holding = position("2330", "1000", "200")
    report = DailyReportBuilder().build(valuation((holding,)))
    assert report.largest_positions == (holding,)
    assert report.top_gainers == (holding,)
    assert report.top_losers == (holding,)
    assert report.portfolio == (holding,)


def test_multiple_holdings_are_ranked_deterministically() -> None:
    holdings = (
        position("B", "100", "-20"),
        position("A", "300", "20"),
        position("C", "200", "0"),
    )
    builder = DailyReportBuilder()
    first = builder.build(valuation(holdings))
    second = builder.build(valuation(tuple(reversed(holdings))))
    assert [item.symbol for item in first.largest_positions] == ["A", "C", "B"]
    assert [item.symbol for item in first.top_gainers] == ["A", "C", "B"]
    assert [item.symbol for item in first.top_losers] == ["B", "C", "A"]
    assert first == second
