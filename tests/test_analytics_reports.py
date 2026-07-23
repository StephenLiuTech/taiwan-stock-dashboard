"""Analytics integration tests for daily report presentation."""

from datetime import date
from decimal import Decimal

from domain import PortfolioAnalytics, PortfolioValuation
from pams.reporting import (
    DailyReportBuilder,
    HtmlReportRenderer,
    MarkdownReportRenderer,
)


def analytics() -> PortfolioAnalytics:
    return PortfolioAnalytics(
        start_date=date(2026, 7, 21),
        end_date=date(2026, 7, 22),
        starting_value=Decimal("1000"),
        ending_value=Decimal("900"),
        absolute_profit_loss=Decimal("-100"),
        total_return=Decimal("-0.1"),
        daily_returns=(),
        peak_value=Decimal("1000"),
        trough_value=Decimal("900"),
        max_drawdown=Decimal("-0.1"),
        snapshot_count=2,
    )


def valuation() -> PortfolioValuation:
    return PortfolioValuation(
        valuation_date=date(2026, 7, 22),
        total_cost=Decimal("0"),
        total_market_value=Decimal("0"),
        total_unrealized_pl=Decimal("0"),
        total_return=Decimal("0"),
        holdings=(),
    )


def test_markdown_report_renders_application_analytics_values() -> None:
    report = DailyReportBuilder().build(valuation(), analytics())
    rendered = MarkdownReportRenderer().render(report)
    assert "## Portfolio Analytics" in rendered
    assert "Period: 2026-07-21 to 2026-07-22" in rendered
    assert "Starting Value: NT$1,000.00" in rendered
    assert "Ending Value: NT$900.00" in rendered
    assert "Profit / Loss: NT$-100.00" in rendered
    assert "Total Return: -10.00%" in rendered
    assert "Maximum Drawdown: -10.00%" in rendered


def test_html_report_renders_application_analytics_values() -> None:
    report = DailyReportBuilder().build(valuation(), analytics())
    rendered = HtmlReportRenderer().render(report)
    assert "<h2>Portfolio Analytics</h2>" in rendered
    assert "<dt>Period</dt><dd>2026-07-21 to 2026-07-22</dd>" in rendered
    assert "<dt>Total Return</dt><dd>-10.00%</dd>" in rendered
    assert "<dt>Maximum Drawdown</dt><dd>-10.00%</dd>" in rendered


def test_reports_render_controlled_analytics_unavailable_state() -> None:
    message = "Portfolio analytics could not be loaded."
    report = DailyReportBuilder().build(valuation(), analytics_unavailable=message)
    markdown = MarkdownReportRenderer().render(report)
    html = HtmlReportRenderer().render(report)
    assert message in markdown
    assert message in html
    assert "sqlite" not in markdown.lower()
    assert "traceback" not in html.lower()
