"""Markdown rendering for structured daily reports."""

from decimal import Decimal

from pams.analytics_reporting import analytics_view_model
from pams.reporting.builder import DailyReport


def _money(value: Decimal, *, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}NT${value:,.0f}"


def _number(value: Decimal) -> str:
    return f"{value:,.2f}"


def _percent(value: Decimal, *, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:,.2f}%"


def _table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    lines = [
        "|" + "|".join(headers) + "|",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend("|" + "|".join(row) + "|" for row in rows)
    return lines


class MarkdownReportRenderer:
    """Render a DailyReport as deterministic printable Markdown."""

    def render(self, report: DailyReport) -> str:
        """Return Markdown without reading data or invoking other renderers."""
        summary = report.portfolio_summary
        lines = [
            "# Portfolio Report",
            "",
            "Date",
            "",
            str(report.report_date or "N/A"),
            "",
            "---",
            "",
            "## Summary",
            "",
            f"Market Value: {_money(summary.market_value)}",
            "",
            f"Cost: {_money(summary.cost)}",
            "",
            f"Unrealized: {_money(summary.unrealized_pl, signed=True)}",
            "",
            f"Return: {_percent(summary.total_return, signed=True)}",
            "",
            "---",
            "",
            "## Largest Positions",
            "",
        ]
        lines.extend(
            _table(
                ("Symbol", "Weight"),
                [
                    (item.symbol, _percent(item.portfolio_weight))
                    for item in report.largest_positions
                ],
            )
        )
        lines.extend(["", "---", "", "## Top Winners", ""])
        lines.extend(
            _table(
                ("Symbol", "Unrealized", "Return"),
                [
                    (
                        item.symbol,
                        _money(item.unrealized_pl, signed=True),
                        _percent(item.unrealized_return, signed=True),
                    )
                    for item in report.top_gainers
                ],
            )
        )
        lines.extend(["", "---", "", "## Top Losers", ""])
        lines.extend(
            _table(
                ("Symbol", "Unrealized", "Return"),
                [
                    (
                        item.symbol,
                        _money(item.unrealized_pl, signed=True),
                        _percent(item.unrealized_return, signed=True),
                    )
                    for item in report.top_losers
                ],
            )
        )
        lines.extend(["", "---", "", "## Portfolio", ""])
        lines.extend(
            _table(
                (
                    "Symbol",
                    "Quantity",
                    "Average Cost",
                    "Last Price",
                    "Cost Basis",
                    "Market Value",
                    "Unrealized",
                    "Return",
                ),
                [
                    (
                        item.symbol,
                        _number(item.quantity),
                        _money(item.average_cost),
                        _money(item.last_price),
                        _money(item.cost_basis),
                        _money(item.market_value),
                        _money(item.unrealized_pl, signed=True),
                        _percent(item.unrealized_return, signed=True),
                    )
                    for item in report.portfolio
                ],
            )
        )
        lines.extend(["", "---", "", "## Portfolio Analytics", ""])
        if report.analytics is not None:
            analytics = analytics_view_model(report.analytics)
            lines.extend(
                [
                    f"Period: {analytics.period}",
                    "",
                    f"Starting Value: {analytics.starting_value}",
                    "",
                    f"Ending Value: {analytics.ending_value}",
                    "",
                    f"Profit / Loss: {analytics.absolute_profit_loss}",
                    "",
                    f"Total Return: {analytics.total_return}",
                    "",
                    f"Maximum Drawdown: {analytics.max_drawdown}",
                ]
            )
        else:
            lines.append(report.analytics_unavailable or "Analytics unavailable.")
        return "\n".join(lines) + "\n"
