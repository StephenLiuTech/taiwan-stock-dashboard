"""Plain-text and email-compatible HTML daily report rendering."""

from decimal import Decimal
from html import escape
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from pams.application.send_daily_report import (
    ChartSource,
    DailyEmailHistoryPoint,
    DailyEmailPosition,
    DailyEmailReport,
    InlineImage,
    RenderedEmail,
)
from pams.delivery.html_styles import (
    NEUTRAL_COLOR,
    TAIWAN_GAIN_COLOR,
    TAIWAN_LOSS_COLOR,
    taiwan_performance_color,
)
from pams.delivery.sections import DailyReportSectionRenderer

CHART_CONTENT_ID = "pams-asset-change-chart"
CHART_FILENAME = "pams-30-day-asset-change.png"
CHART_FALLBACK = (
    "Only one portfolio snapshot is available; "
    "a trend chart requires at least two snapshots."
)
POSITIVE_COLOR = TAIWAN_GAIN_COLOR
NEGATIVE_COLOR = TAIWAN_LOSS_COLOR


def _money(value: Decimal, *, signed: bool = False) -> str:
    if signed and value > 0:
        return f"+NT${value:,.2f}"
    if signed and value < 0:
        return f"-NT${abs(value):,.2f}"
    return f"NT${value:,.2f}"


def _whole_money(value: Decimal, *, signed: bool = False) -> str:
    """Format an HTML table monetary value as a whole dollar."""
    if signed and value > 0:
        return f"+NT${value:,.0f}"
    if signed and value < 0:
        return f"-NT${abs(value):,.0f}"
    return f"NT${value:,.0f}"


def _compact_number(value: Decimal) -> str:
    """Format a table price with at most two decimal places."""
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _percent(value: Decimal | None, *, signed: bool = False) -> str:
    if value is None:
        return "N/A"
    prefix = "+" if signed and value > 0 else ""
    return f"{prefix}{value * 100:,.2f}%"


def _tone(value: Decimal) -> str:
    return taiwan_performance_color(value)


def _history_text(history: tuple[DailyEmailHistoryPoint, ...]) -> list[str]:
    if len(history) == 1:
        return [CHART_FALLBACK]
    lines = [
        (
            f"Period: {history[0].snapshot_date} to {history[-1].snapshot_date} "
            f"({len(history)} available snapshots)"
        ),
        "Date | Total stock market value | Net stock equity",
    ]
    lines.extend(
        f"{point.snapshot_date} | {_money(point.total_market_value)} | "
        f"{_money(point.net_asset_value)}"
        for point in history
    )
    return lines


def _ranked_contributors(
    positions: tuple[DailyEmailPosition, ...],
) -> list[DailyEmailPosition]:
    return sorted(
        positions,
        key=lambda item: (-abs(item.daily_profit_loss), item.symbol),
    )


def _contributor_text(positions: tuple[DailyEmailPosition, ...]) -> list[str]:
    lines = [
        "Rank | Symbol | Name | Today's P/L | Today's P/L % | Share of net daily P/L"
    ]
    lines.extend(
        " | ".join(
            (
                str(rank),
                item.symbol,
                item.name,
                _money(item.daily_profit_loss, signed=True),
                _percent(item.daily_profit_loss_percentage, signed=True),
                _percent(item.daily_profit_loss_share, signed=True),
            )
        )
        for rank, item in enumerate(_ranked_contributors(positions), start=1)
    )
    return lines


def _chart_png(history: tuple[DailyEmailHistoryPoint, ...]) -> bytes:
    """Render a high-resolution, email-compatible financial dashboard PNG."""
    width, height = 1200, 650
    left, top, right, bottom = 160, 130, 48, 92
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _chart_font(32, bold=True)
    legend_font = _chart_font(18)
    axis_font = _chart_font(16)
    chart_width = width - left - right
    chart_height = height - top - bottom
    values = [
        value
        for point in history
        for value in (point.total_market_value, point.net_asset_value)
    ]
    minimum = min(values)
    maximum = max(values)
    span = maximum - minimum
    if span == 0:
        span = abs(maximum) or Decimal("1")
        minimum -= span / Decimal("2")
        maximum += span / Decimal("2")
        span = maximum - minimum

    def coordinates(index: int, value: Decimal) -> tuple[int, int]:
        x = left + round(index * chart_width / (len(history) - 1))
        ratio = float((value - minimum) / span)
        return x, top + round((1 - ratio) * chart_height)

    draw.text(
        (left, 30),
        "Portfolio Trend",
        fill="#0f172a",
        font=title_font,
    )
    legend_y = 96
    draw.line((left, legend_y, left + 44, legend_y), fill="#2563eb", width=7)
    draw.text(
        (left + 58, legend_y - 11),
        "Total stock market value",
        fill="#334155",
        font=legend_font,
    )
    second_legend_x = left + 410
    draw.line(
        (second_legend_x, legend_y, second_legend_x + 44, legend_y),
        fill="#16a34a",
        width=7,
    )
    draw.text(
        (second_legend_x + 58, legend_y - 11),
        "Net stock equity",
        fill="#334155",
        font=legend_font,
    )

    for step in range(5):
        ratio = Decimal(step) / Decimal("4")
        value = maximum - span * ratio
        y = top + round(step * chart_height / 4)
        draw.line((left, y, left + chart_width, y), fill="#eef2f7", width=2)
        draw.text(
            (12, y - 10),
            f"NT${value:,.0f}",
            fill="#64748b",
            font=axis_font,
        )

    market_points = [
        coordinates(index, point.total_market_value)
        for index, point in enumerate(history)
    ]
    equity_points = [
        coordinates(index, point.net_asset_value) for index, point in enumerate(history)
    ]
    draw.line(market_points, fill="#2563eb", width=7, joint="curve")
    draw.line(equity_points, fill="#16a34a", width=7, joint="curve")

    label_count = min(5, len(history))
    label_indexes = sorted(
        {
            round(index * (len(history) - 1) / (label_count - 1))
            for index in range(label_count)
        }
    )
    for index in label_indexes:
        label = history[index].snapshot_date.strftime("%m-%d")
        x, _ = coordinates(index, minimum)
        label_width = draw.textlength(label, font=axis_font)
        draw.text(
            (x - label_width / 2, top + chart_height + 24),
            label,
            fill="#64748b",
            font=axis_font,
        )

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _chart_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    """Load a portable TrueType font with a safe Pillow fallback."""
    candidates = (
        ("arialbd.ttf", "DejaVuSans-Bold.ttf")
        if bold
        else ("arial.ttf", "DejaVuSans.ttf")
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cell(
    value: str,
    *,
    color: str | None = None,
    align: str = "left",
    width: str | None = None,
    name: bool = False,
    font_size: str = "13px",
    padding: str = "9px 6px",
) -> str:
    style = (
        f"padding:{padding};border:1px solid #d1d5db;"
        f"font-size:{font_size};line-height:1.4;vertical-align:middle;"
        f"text-align:{align};white-space:nowrap"
    )
    if name:
        style += ";word-break:keep-all"
    if width:
        style += f";width:{width}"
    if color:
        style += f";color:{color};font-weight:600"
    return f'<td style="{style}">{escape(value)}</td>'


def _summary_card(label: str, value: str, *, color: str = "#111827") -> str:
    return (
        '<td style="padding:12px;border:1px solid #e5e7eb;'
        'background:#f9fafb;width:33%;vertical-align:top">'
        f'<div style="font-size:12px;color:#6b7280">{escape(label)}</div>'
        f'<div style="font-size:20px;font-weight:700;color:{color};'
        f'margin-top:4px">{escape(value)}</div></td>'
    )


def _expected_dividend_card(estimated: Decimal, received: Decimal) -> str:
    remaining = estimated - received
    return (
        '<td colspan="3" style="padding:12px;border:1px solid #e5e7eb;'
        'background:#f9fafb;vertical-align:top">'
        '<div style="font-size:12px;color:#6b7280">Expected Annual Dividend</div>'
        '<table role="presentation" style="width:100%;border-collapse:collapse;'
        'margin-top:6px"><tr>'
        f'<td style="padding:2px 8px 2px 0;color:#111827">Estimated<br>'
        f"<strong>{escape(_whole_money(estimated))}</strong></td>"
        f'<td style="padding:2px 8px;color:#111827">Already Received<br>'
        f"<strong>{escape(_whole_money(received))}</strong></td>"
        f'<td style="padding:2px 0 2px 8px;color:#111827">Remaining<br>'
        f"<strong>{escape(_whole_money(remaining))}</strong></td>"
        "</tr></table></td>"
    )


class DailyEmailReportRenderer:
    """Render persisted daily portfolio facts into multipart email content."""

    def render(
        self, report: DailyEmailReport, chart_source: ChartSource | None = None
    ) -> RenderedEmail:
        """Return deterministic plain text, HTML, subject, and inline chart."""
        subject = f"PAMS Daily Portfolio Report - {report.report_date}"
        contributors = _ranked_contributors(report.positions)
        dividend_summary = report.sections.dividend_calendar
        expected_dividend_text = (
            [
                "Expected Annual Dividend",
                f"Estimated: {_whole_money(dividend_summary.estimated_annual_dividend)}",
                f"Already Received: {_whole_money(dividend_summary.already_received)}",
                "Remaining: "
                f"{_whole_money(dividend_summary.estimated_annual_dividend - dividend_summary.already_received)}",
            ]
            if dividend_summary is not None and dividend_summary.items
            else []
        )
        text_lines = [
            "PAMS Daily Portfolio Report",
            f"Report date: {report.report_date}",
            f"Verified market source date: {report.verified_source_date or 'N/A'}",
            "",
            "Portfolio Summary",
            "Today's P/L",
            f"Amount: {_money(report.daily_profit_loss, signed=True)}",
            (
                "Percentage: "
                f"{_percent(report.daily_profit_loss_percentage, signed=True)}"
            ),
            f"Net stock equity: {_money(report.net_asset_value)}",
            f"Total stock market value: {_money(report.total_market_value)}",
            f"Total investment cost: {_money(report.total_cost_basis)}",
            f"Unrealized P/L: {_money(report.total_unrealized_pnl, signed=True)}",
            f"Total return: {_percent(report.total_return, signed=True)}",
            f"Liabilities: {_money(report.total_liabilities)}",
            f"Liability ratio: {_percent(report.liability_ratio)}",
            f"Position count: {len(report.positions)}",
            *expected_dividend_text,
            "",
            "Portfolio Trend",
            *_history_text(report.history),
            "",
            "Today's Contributors",
            *_contributor_text(report.positions),
            "",
            "Holdings",
            (
                "Symbol | Name | Quantity | Average Cost | Close | Daily Change | "
                "Today's P/L | Today's P/L % | Market Value | Unrealized P/L | "
                "Return | Weight"
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
                    _money(item.daily_profit_loss, signed=True),
                    _percent(item.daily_profit_loss_percentage, signed=True),
                    _money(item.market_value),
                    _money(item.unrealized_pnl, signed=True),
                    _percent(item.unrealized_return, signed=True),
                    _percent(item.portfolio_weight),
                )
            )
            for item in report.positions
        )

        contributor_rows = "".join(
            "<tr>"
            + _cell(
                str(rank),
                align="right",
                width="7%",
                font_size="14px",
                padding="11px 6px",
            )
            + _cell(item.symbol, width="13%", font_size="14px")
            + _cell(item.name, name=True, font_size="14px")
            + _cell(
                _whole_money(item.daily_profit_loss, signed=True),
                color=_tone(item.daily_profit_loss),
                align="right",
                width="19%",
                font_size="14px",
                padding="11px 6px",
            )
            + _cell(
                _percent(item.daily_profit_loss_percentage, signed=True),
                color=_tone(item.daily_profit_loss),
                align="right",
                width="17%",
                font_size="14px",
                padding="11px 6px",
            )
            + _cell(
                _percent(item.daily_profit_loss_share, signed=True),
                color=_tone(item.daily_profit_loss),
                align="right",
                width="20%",
                font_size="14px",
                padding="11px 6px",
            )
            + "</tr>"
            for rank, item in enumerate(contributors, start=1)
        )
        rows = "".join(
            "<tr>"
            + _cell(item.symbol, width="8%", padding="10px 6px")
            + _cell(item.name, name=True, padding="10px 6px")
            + _cell(
                f"{item.quantity:,.0f}",
                align="right",
                width="9%",
                padding="10px 6px",
            )
            + _cell(
                _compact_number(item.average_cost),
                align="right",
                width="10%",
                padding="10px 6px",
            )
            + _cell(
                _compact_number(item.close_price),
                align="right",
                width="8%",
                padding="10px 6px",
            )
            + _cell(
                _whole_money(item.daily_profit_loss, signed=True),
                color=_tone(item.daily_profit_loss),
                align="right",
                width="11%",
                padding="10px 6px",
            )
            + _cell(
                _percent(item.daily_profit_loss_percentage, signed=True),
                color=_tone(item.daily_profit_loss),
                align="right",
                width="9%",
                padding="10px 6px",
            )
            + _cell(
                _whole_money(item.unrealized_pnl, signed=True),
                color=_tone(item.unrealized_pnl),
                align="right",
                width="11%",
                padding="10px 6px",
            )
            + _cell(
                _percent(item.unrealized_return, signed=True),
                color=_tone(item.unrealized_return),
                align="right",
                width="8%",
                padding="10px 6px",
            )
            + _cell(
                _whole_money(item.market_value),
                align="right",
                width="10%",
                padding="10px 6px",
            )
            + "</tr>"
            for item in report.positions
        )

        def headers(
            values: tuple[tuple[str, str | None, str], ...], *, font_size: str
        ) -> str:
            cells = []
            for label, width, align in values:
                style = (
                    "padding:10px 6px;border:1px solid #d1d5db;"
                    f"background:#f3f4f6;text-align:{align};"
                    f"font-size:{font_size};line-height:1.3;vertical-align:middle;"
                    "white-space:nowrap;word-break:normal"
                )
                if width:
                    style += f";width:{width}"
                cells.append(f'<th style="{style}">{escape(label)}</th>')
            return "".join(cells)

        if len(report.history) > 1:
            source = chart_source or ChartSource(
                uri=f"cid:{CHART_CONTENT_ID}",
                attachment=InlineImage(
                    CHART_CONTENT_ID,
                    CHART_FILENAME,
                    "image/png",
                    _chart_png(report.history),
                ),
            )
            chart_html = (
                f'<img src="{escape(source.uri, quote=True)}" '
                'alt="30-day stock market value and net stock equity chart" '
                'width="760" style="display:block;width:100%;max-width:760px;'
                'height:auto;border:0">'
            )
            inline_images = (
                (source.attachment,) if source.attachment is not None else ()
            )
        else:
            chart_html = (
                f'<p style="color:{NEUTRAL_COLOR}">{escape(CHART_FALLBACK)}</p>'
            )
            inline_images = ()

        daily_cards = (
            "<tr>"
            + _summary_card(
                "Today's P/L",
                _money(report.daily_profit_loss, signed=True),
                color=_tone(report.daily_profit_loss),
            )
            + _summary_card(
                "Today's P/L %",
                _percent(report.daily_profit_loss_percentage, signed=True),
                color=_tone(report.daily_profit_loss),
            )
            + '<td style="width:33%"></td>'
            + "</tr>"
        )
        summary_cards = (
            "<tr>"
            + _summary_card("Net stock equity", _money(report.net_asset_value))
            + _summary_card(
                "Total stock market value", _money(report.total_market_value)
            )
            + _summary_card(
                "Total unrealized P/L",
                _money(report.total_unrealized_pnl, signed=True),
                color=_tone(report.total_unrealized_pnl),
            )
            + "</tr><tr>"
            + _summary_card(
                "Total return",
                _percent(report.total_return, signed=True),
                color=_tone(report.total_return),
            )
            + _summary_card("Liability ratio", _percent(report.liability_ratio))
            + _summary_card("Liabilities", _money(report.total_liabilities))
            + "</tr><tr>"
            + _summary_card("Position count", str(len(report.positions)))
            + '<td style="width:33%"></td><td style="width:33%"></td>'
            + "</tr>"
            + (
                "<tr>"
                + _expected_dividend_card(
                    dividend_summary.estimated_annual_dividend,
                    dividend_summary.already_received,
                )
                + "</tr>"
                if dividend_summary is not None and dividend_summary.items
                else ""
            )
        )
        html = f"""<!doctype html>
<html><body style="margin:0;padding:16px;font-family:Arial,sans-serif;color:#111827">
<div style="max-width:760px;margin:0 auto">
<h1 style="color:#1f4e78;margin-bottom:8px">PAMS Daily Portfolio Report</h1>
<p style="margin-top:0;color:#4b5563"><strong>Report date:</strong>
{report.report_date}<br><strong>Verified market source date:</strong>
{report.verified_source_date or "N/A"}</p>
<h2 style="font-size:18px">Portfolio Summary</h2>
<table role="presentation" style="border-collapse:collapse;width:100%;margin-bottom:24px">
{daily_cards}</table>
<table role="presentation" style="border-collapse:collapse;width:100%;margin-bottom:24px">
{summary_cards}</table>
<h2 style="font-size:18px">Portfolio Trend</h2>
{chart_html}
<h2 style="font-size:18px;margin-top:24px">Today's Contributors</h2>
<table style="border-collapse:collapse;width:100%;table-layout:auto">
<thead><tr>{headers((("Rank", "7%", "right"), ("Symbol", "13%", "left"), ("Name", None, "left"), ("Today's P/L", "19%", "right"), ("Today's P/L %", "17%", "right"), ("Share of Net P/L", "20%", "right")), font_size="14px")}</tr></thead>
<tbody>{contributor_rows}</tbody></table>
<p style="font-size:12px;color:{NEUTRAL_COLOR}">Ranked by absolute daily P/L
impact. Share is unavailable when total portfolio daily P/L is zero.</p>
<h2 style="font-size:18px;margin-top:24px">Holdings</h2>
<table style="border-collapse:collapse;width:100%;table-layout:auto">
<thead><tr>{headers((("Symbol", "8%", "left"), ("Name", None, "left"), ("Quantity", "9%", "right"), ("Average Cost", "10%", "right"), ("Close", "8%", "right"), ("Today's P/L", "11%", "right"), ("Today's P/L %", "9%", "right"), ("Unrealized P/L", "11%", "right"), ("Return %", "8%", "right"), ("Market Value", "10%", "right")), font_size="13px")}</tr></thead>
<tbody>{rows}</tbody></table>
{DailyReportSectionRenderer().html(report.sections)}
</div></body></html>"""
        section_text = DailyReportSectionRenderer().text(report.sections)
        return RenderedEmail(
            subject,
            "\n".join(text_lines)
            + ("\n\n" + section_text if section_text else "")
            + "\n",
            html,
            inline_images,
        )
