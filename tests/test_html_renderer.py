"""HTML daily report rendering tests."""

from datetime import date
from decimal import Decimal

from domain import HoldingValuation, Market, PortfolioValuation
from pams.reporting import DailyReport, DailyReportBuilder, HtmlReportRenderer


def build_report(symbol: str = "2330") -> DailyReport:
    holding = HoldingValuation(
        symbol=symbol,
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


def test_html_generation_is_semantic_and_printable() -> None:
    rendered = HtmlReportRenderer().render(build_report())
    assert rendered.startswith("<!doctype html>")
    assert "<main>" in rendered
    assert "<section><h2>Largest Positions</h2><table>" in rendered
    assert "<th>Symbol</th>" in rendered
    assert "<td>2330</td>" in rendered
    assert "<script" not in rendered
    assert "bootstrap" not in rendered.lower()
    assert "http://" not in rendered
    assert "https://" not in rendered


def test_html_escapes_values_and_is_deterministic() -> None:
    report = build_report("<2330>")
    renderer = HtmlReportRenderer()
    assert renderer.render(report) == renderer.render(report)
    assert "&lt;2330&gt;" in renderer.render(report)
    assert "<td><2330></td>" not in renderer.render(report)


def test_empty_html_contains_empty_table_bodies() -> None:
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
    rendered = HtmlReportRenderer().render(report)
    assert "<strong>Date:</strong> N/A" in rendered
    assert "<tbody></tbody>" in rendered
