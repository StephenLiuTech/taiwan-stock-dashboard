"""Semantic HTML rendering for structured daily reports."""

from decimal import Decimal
from html import escape

from pams.reporting.builder import DailyReport


def _money(value: Decimal, *, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}NT${value:,.0f}"


def _number(value: Decimal) -> str:
    return f"{value:,.2f}"


def _percent(value: Decimal, *, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:,.2f}%"


def _table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    heading = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return (
        "<table><thead><tr>"
        + heading
        + "</tr></thead><tbody>"
        + body
        + "</tbody></table>"
    )


class HtmlReportRenderer:
    """Render a DailyReport as standalone semantic HTML."""

    def render(self, report: DailyReport) -> str:
        """Return printable HTML with no scripts or external dependencies."""
        summary = report.portfolio_summary
        largest = _table(
            ("Symbol", "Weight"),
            [
                (item.symbol, _percent(item.portfolio_weight))
                for item in report.largest_positions
            ],
        )
        winners = _table(
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
        losers = _table(
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
        portfolio = _table(
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
        return "\n".join(
            [
                "<!doctype html>",
                '<html lang="en">',
                "<head>",
                '<meta charset="utf-8">',
                "<title>Portfolio Report</title>",
                "</head>",
                "<body>",
                "<main>",
                "<h1>Portfolio Report</h1>",
                f"<p><strong>Date:</strong> {report.report_date or 'N/A'}</p>",
                "<section>",
                "<h2>Summary</h2>",
                "<dl>",
                f"<dt>Market Value</dt><dd>{_money(summary.market_value)}</dd>",
                f"<dt>Cost</dt><dd>{_money(summary.cost)}</dd>",
                f"<dt>Unrealized</dt><dd>{_money(summary.unrealized_pl, signed=True)}</dd>",
                f"<dt>Return</dt><dd>{_percent(summary.total_return, signed=True)}</dd>",
                "</dl>",
                "</section>",
                f"<section><h2>Largest Positions</h2>{largest}</section>",
                f"<section><h2>Top Winners</h2>{winners}</section>",
                f"<section><h2>Top Losers</h2>{losers}</section>",
                f"<section><h2>Portfolio</h2>{portfolio}</section>",
                "</main>",
                "</body>",
                "</html>",
                "",
            ]
        )
