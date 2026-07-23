"""Markdown daily report rendering tests."""

from datetime import date
from decimal import Decimal

from domain import HoldingValuation, Market, PortfolioValuation
from pams.reporting import DailyReport, DailyReportBuilder, MarkdownReportRenderer


def report_with_one_holding() -> DailyReport:
    holding = HoldingValuation(
        symbol="2330",
        market=Market.TWSE,
        quantity=Decimal("10"),
        average_cost=Decimal("80"),
        last_price=Decimal("100"),
        cost_basis=Decimal("800"),
        market_value=Decimal("1000"),
        unrealized_pl=Decimal("200"),
        unrealized_return=Decimal("0.25"),
        portfolio_weight=Decimal("1"),
    )
    return DailyReportBuilder().build(
        PortfolioValuation(
            date(2026, 7, 22),
            Decimal("800"),
            Decimal("1000"),
            Decimal("200"),
            Decimal("0.25"),
            (holding,),
        )
    )


def test_markdown_generation_contains_all_sections() -> None:
    rendered = MarkdownReportRenderer().render(report_with_one_holding())
    assert rendered.startswith("# Portfolio Report\n")
    assert "2026-07-22" in rendered
    assert "Market Value: NT$1,000" in rendered
    assert "|Symbol|Weight|" in rendered
    assert "## Top Winners" in rendered
    assert "## Top Losers" in rendered
    assert "## Portfolio" in rendered
    assert "|2330|10.00|NT$80|NT$100|NT$800|NT$1,000|+NT$200|+25.00%|" in rendered


def test_empty_markdown_is_valid_and_deterministic() -> None:
    report = DailyReportBuilder().build(
        PortfolioValuation(
            None,
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            (),
        )
    )
    renderer = MarkdownReportRenderer()
    assert renderer.render(report) == renderer.render(report)
    assert "Date\n\nN/A" in renderer.render(report)
    assert "|Symbol|Weight|\n|---|---|" in renderer.render(report)
