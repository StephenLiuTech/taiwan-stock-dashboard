"""Plain-text and email-compatible HTML daily report rendering."""

from decimal import Decimal
from html import escape

from pams.application.send_daily_report import DailyEmailReport, RenderedEmail


def _money(value: Decimal, *, signed: bool = False) -> str:
    prefix = "+" if signed and value > 0 else ""
    return f"{prefix}NT${value:,.2f}"


def _percent(value: Decimal | None, *, signed: bool = False) -> str:
    if value is None:
        return "N/A"
    prefix = "+" if signed and value > 0 else ""
    return f"{prefix}{value * 100:,.2f}%"


class DailyEmailReportRenderer:
    """Render persisted daily portfolio facts into multipart email content."""

    def render(self, report: DailyEmailReport) -> RenderedEmail:
        """Return deterministic plain text, HTML, and subject."""
        subject = f"PAMS Daily Portfolio Report - {report.report_date}"
        top_gainer = report.top_gainer.symbol if report.top_gainer else "N/A"
        top_loser = report.top_loser.symbol if report.top_loser else "N/A"
        text_lines = [
            "PAMS Daily Portfolio Report",
            f"Report date: {report.report_date}",
            f"Verified market source date: {report.verified_source_date or 'N/A'}",
            "",
            f"Total stock market value: {_money(report.total_market_value)}",
            f"Total investment cost: {_money(report.total_cost_basis)}",
            f"Unrealized P/L: {_money(report.total_unrealized_pnl, signed=True)}",
            f"Total return: {_percent(report.total_return, signed=True)}",
            f"Liabilities: {_money(report.total_liabilities)}",
            f"Net stock equity: {_money(report.net_asset_value)}",
            f"Liability ratio: {_percent(report.liability_ratio)}",
            f"Position count: {len(report.positions)}",
            f"Top gainer: {top_gainer}",
            f"Top loser: {top_loser}",
            "",
            "Holdings",
            (
                "Symbol | Name | Quantity | Average Cost | Close | Daily Change | "
                "Market Value | Unrealized P/L | Return | Weight"
            ),
        ]
        text_lines.extend(
            " | ".join(
                (
                    item.symbol,
                    item.name,
                    f"{item.quantity:,.2f}",
                    _money(item.average_cost),
                    _money(item.close_price),
                    _percent(item.daily_return, signed=True),
                    _money(item.market_value),
                    _money(item.unrealized_pnl, signed=True),
                    _percent(item.unrealized_return, signed=True),
                    _percent(item.portfolio_weight),
                )
            )
            for item in report.positions
        )
        rows = "".join(
            "<tr>"
            + "".join(
                f'<td style="padding:8px;border:1px solid #d1d5db">{escape(value)}</td>'
                for value in (
                    item.symbol,
                    item.name,
                    f"{item.quantity:,.2f}",
                    _money(item.average_cost),
                    _money(item.close_price),
                    _percent(item.daily_return, signed=True),
                    _money(item.market_value),
                    _money(item.unrealized_pnl, signed=True),
                    _percent(item.unrealized_return, signed=True),
                    _percent(item.portfolio_weight),
                )
            )
            + "</tr>"
            for item in report.positions
        )
        headers = "".join(
            f'<th style="padding:8px;border:1px solid #d1d5db;background:#f3f4f6">'
            f"{escape(header)}</th>"
            for header in (
                "Symbol",
                "Name",
                "Quantity",
                "Average Cost",
                "Close",
                "Daily Change",
                "Market Value",
                "Unrealized P/L",
                "Return",
                "Weight",
            )
        )
        html = f"""<!doctype html>
<html><body style="font-family:Arial,sans-serif;color:#111827">
<h1 style="color:#1f4e78">PAMS Daily Portfolio Report</h1>
<p><strong>Report date:</strong> {report.report_date}<br>
<strong>Verified market source date:</strong>
{report.verified_source_date or "N/A"}</p>
<table style="border-collapse:collapse;margin-bottom:20px">
<tr><td>Total stock market value</td><td>{_money(report.total_market_value)}</td></tr>
<tr><td>Total investment cost</td><td>{_money(report.total_cost_basis)}</td></tr>
<tr><td>Unrealized P/L</td><td>{_money(report.total_unrealized_pnl, signed=True)}</td></tr>
<tr><td>Total return</td><td>{_percent(report.total_return, signed=True)}</td></tr>
<tr><td>Liabilities</td><td>{_money(report.total_liabilities)}</td></tr>
<tr><td>Net stock equity</td><td>{_money(report.net_asset_value)}</td></tr>
<tr><td>Liability ratio</td><td>{_percent(report.liability_ratio)}</td></tr>
<tr><td>Position count</td><td>{len(report.positions)}</td></tr>
</table>
<p><strong>Top gainer:</strong> {escape(top_gainer)} &nbsp;
<strong>Top loser:</strong> {escape(top_loser)}</p>
<table style="border-collapse:collapse;width:100%"><thead><tr>{headers}</tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""
        return RenderedEmail(subject, "\n".join(text_lines) + "\n", html)
