"""Plain-text and email-compatible HTML daily report rendering."""

from decimal import Decimal
from html import escape
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from domain import Currency, Market
from pams.application.send_daily_report import (
    ChartSource,
    DailyEmailHistoryPoint,
    DailyEmailPosition,
    DailyEmailReport,
    InlineImage,
    RenderedEmail,
)
from pams.delivery.formatting import (
    format_compact_decimal,
    format_native_currency_amount,
    format_percentage,
    format_twd_amount,
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


def _money(value: Decimal | None, *, signed: bool = False) -> str:
    return format_twd_amount(value, signed=signed)


def _whole_money(value: Decimal | None, *, signed: bool = False) -> str:
    """Format an HTML table monetary value as a whole dollar."""
    return format_twd_amount(value, signed=signed)


def _compact_number(value: Decimal | None) -> str:
    """Format a table price with at most two decimal places."""
    return format_compact_decimal(value)


def _native_money(value: Decimal | None, currency: Currency) -> str:
    return format_native_currency_amount(value, currency)


def _percent(value: Decimal | None, *, signed: bool = False) -> str:
    return format_percentage(value, signed=signed)


def _tone(value: Decimal | None) -> str:
    return taiwan_performance_color(value)


def _history_text(history: tuple[DailyEmailHistoryPoint, ...]) -> list[str]:
    if len(history) <= 1:
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
    """Order contributors by raw Decimal daily P/L with unavailable last."""
    return sorted(
        positions,
        key=lambda item: (
            item.daily_profit_loss is None,
            (
                -item.daily_profit_loss
                if item.daily_profit_loss is not None
                else Decimal("0")
            ),
            item.market.value,
            item.symbol,
        ),
    )


def _ordered_holdings(
    positions: tuple[DailyEmailPosition, ...],
) -> list[DailyEmailPosition]:
    """Order holdings by Decimal unrealized P/L with unavailable values last."""
    return sorted(
        positions,
        key=lambda item: (
            item.unrealized_pnl is None,
            -item.unrealized_pnl if item.unrealized_pnl is not None else Decimal("0"),
            item.market.value,
            item.symbol,
        ),
    )


def _contributor_text(positions: tuple[DailyEmailPosition, ...]) -> list[str]:
    lines = ["Rank | Market | Symbol | Name | Quote Date | Today's P/L | Today's P/L %"]
    lines.extend(
        " | ".join(
            (
                str(rank),
                item.market.value,
                item.symbol,
                item.name,
                str(item.quote_date or "N/A"),
                _money(item.daily_profit_loss, signed=True),
                _percent(item.daily_profit_loss_percentage, signed=True),
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
        '<td class="pams-summary-card" style="padding:12px;border:1px solid #e5e7eb;'
        'background:#f9fafb;width:33%;vertical-align:top">'
        f'<div style="font-size:12px;color:#6b7280">{escape(label)}</div>'
        f'<div style="font-size:20px;font-weight:700;color:{color};'
        f'margin-top:4px">{escape(value)}</div></td>'
    )


def _mobile_metric_row(
    first_label: str,
    first_value: str,
    second_label: str | None = None,
    second_value: str | None = None,
    *,
    first_color: str = "#111827",
    second_color: str = "#111827",
) -> str:
    """Render one conservative two-column mobile holding metric row."""
    cells = (
        '<td style="width:50%;padding:9px;border:1px solid #e5e7eb;'
        'vertical-align:top"><span style="font-size:11px;color:#6b7280">'
        f'{escape(first_label)}</span><br><strong style="color:{first_color};'
        f'white-space:nowrap">{escape(first_value)}</strong></td>'
    )
    if second_label is None or second_value is None:
        return f'<tr>{cells}<td style="width:50%;border:1px solid #e5e7eb"></td></tr>'
    cells += (
        '<td style="width:50%;padding:9px;border:1px solid #e5e7eb;'
        'vertical-align:top"><span style="font-size:11px;color:#6b7280">'
        f'{escape(second_label)}</span><br><strong style="color:{second_color};'
        f'white-space:nowrap">{escape(second_value)}</strong></td>'
    )
    return f"<tr>{cells}</tr>"


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
        ordered_holdings = _ordered_holdings(report.positions)
        taiwan_market_value = sum(
            (
                item.market_value
                for item in report.positions
                if item.market is not Market.US and item.market_value is not None
            ),
            Decimal("0"),
        )
        us_market_value = sum(
            (
                item.market_value
                for item in report.positions
                if item.market is Market.US and item.market_value is not None
            ),
            Decimal("0"),
        )
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
            f"Taiwan Holdings: {_money(taiwan_market_value)}",
            f"US Holdings: {_money(us_market_value)}",
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
                "Market | Symbol | Name | Currency | Quantity | Average Cost | Close | "
                "Quote Date | FX | Unrealized P/L | Return % | Market Value"
            ),
        ]
        text_lines.extend(
            " | ".join(
                (
                    item.market.value,
                    item.symbol,
                    item.name,
                    item.native_currency.value,
                    f"{item.quantity:,.2f}",
                    _native_money(item.average_cost, item.native_currency),
                    _native_money(item.close_price, item.native_currency),
                    str(item.quote_date or "N/A"),
                    (
                        _compact_number(item.fx_rate)
                        if item.market is Market.US
                        else "—"
                    ),
                    _money(item.unrealized_pnl, signed=True),
                    _percent(item.unrealized_return, signed=True),
                    _money(item.market_value),
                )
            )
            for item in ordered_holdings
        )

        contributor_rows = "".join(
            "<tr>"
            + _cell(
                str(rank),
                align="right",
                width="5%",
                font_size="14px",
                padding="11px 6px",
            )
            + _cell(item.market.value, width="8%", font_size="14px")
            + _cell(item.symbol, width="9%", font_size="14px")
            + _cell(item.name, width="38%", name=True, font_size="14px")
            + _cell(str(item.quote_date or "N/A"), width="12%", font_size="14px")
            + _cell(
                _whole_money(item.daily_profit_loss, signed=True),
                color=_tone(item.daily_profit_loss),
                align="right",
                width="15%",
                font_size="14px",
                padding="11px 6px",
            )
            + _cell(
                _percent(item.daily_profit_loss_percentage, signed=True),
                color=_tone(item.daily_profit_loss),
                align="right",
                width="13%",
                font_size="14px",
                padding="11px 6px",
            )
            + "</tr>"
            for rank, item in enumerate(contributors, start=1)
        )
        mobile_contributor_rows = "".join(
            "<tr>"
            + _cell(str(rank), align="right", width="12%", padding="11px 6px")
            + _cell(item.symbol, width="24%", padding="11px 6px")
            + _cell(
                _whole_money(item.daily_profit_loss, signed=True),
                color=_tone(item.daily_profit_loss),
                align="right",
                width="34%",
                padding="11px 6px",
            )
            + _cell(
                _percent(item.daily_profit_loss_percentage, signed=True),
                color=_tone(item.daily_profit_loss),
                align="right",
                width="30%",
                padding="11px 6px",
            )
            + "</tr>"
            for rank, item in enumerate(contributors, start=1)
        )
        rows = "".join(
            "<tr>"
            + _cell(item.market.value, width="7%", padding="10px 6px")
            + _cell(item.symbol, width="7%", padding="10px 6px")
            + _cell(item.name, name=True, padding="10px 6px")
            + _cell(item.native_currency.value, width="7%", padding="10px 6px")
            + _cell(
                f"{item.quantity:,.0f}",
                align="right",
                width="9%",
                padding="10px 6px",
            )
            + _cell(
                _native_money(item.average_cost, item.native_currency),
                align="right",
                width="10%",
                padding="10px 6px",
            )
            + _cell(
                _native_money(item.close_price, item.native_currency),
                align="right",
                width="8%",
                padding="10px 6px",
            )
            + _cell(
                str(item.quote_date or "N/A"),
                align="right",
                width="9%",
                padding="10px 6px",
            )
            + _cell(
                _compact_number(item.fx_rate) if item.market is Market.US else "—",
                align="right",
                width="7%",
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
            for item in ordered_holdings
        )
        mobile_holding_cards = "".join(
            '<table role="presentation" class="pams-mobile-holding-card" '
            'style="border-collapse:collapse;width:100%;margin:0 0 14px;'
            'border:1px solid #d1d5db">'
            '<tr><td colspan="2" style="padding:10px;background:#f3f4f6;'
            'font-size:16px;line-height:1.4"><strong>'
            + escape(item.symbol)
            + "</strong> &nbsp;"
            + escape(item.name)
            + '<br><span style="font-size:12px;color:#4b5563">'
            + escape(item.market.value)
            + (
                " &middot; USD"
                + (
                    " &middot; FX " + escape(_compact_number(item.fx_rate))
                    if item.fx_rate is not None
                    else ""
                )
                if item.market is Market.US
                else ""
            )
            + "</span></td></tr>"
            + _mobile_metric_row(
                "Quantity",
                f"{item.quantity:,.0f}",
                "Close",
                _native_money(item.close_price, item.native_currency),
            )
            + _mobile_metric_row("Market Value", _whole_money(item.market_value))
            + _mobile_metric_row(
                "Unrealized P/L",
                _whole_money(item.unrealized_pnl, signed=True),
                "Return %",
                _percent(item.unrealized_return, signed=True),
                first_color=_tone(item.unrealized_pnl),
                second_color=_tone(item.unrealized_return),
            )
            + "</table>"
            for item in ordered_holdings
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
            + _summary_card("Taiwan Holdings", _money(taiwan_market_value))
            + _summary_card("US Holdings", _money(us_market_value))
            + "</tr><tr>"
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
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
.pams-mobile-only {{ display:none; mso-hide:all; }}
@media only screen and (max-width:600px) {{
  .pams-report {{ padding:10px !important; }}
  .pams-container {{ width:100% !important; max-width:100% !important; }}
  .pams-wide-tables {{ width:100% !important; max-width:100% !important; }}
  .pams-desktop-only {{ display:none !important; mso-hide:all !important; }}
  .pams-mobile-only {{ display:block !important; mso-hide:none !important; }}
  table.pams-mobile-only {{ display:table !important; width:100% !important; }}
  .pams-summary-card {{ display:block !important; width:100% !important;
    box-sizing:border-box !important; }}
  .pams-responsive-table {{ width:100% !important; table-layout:fixed !important;
    font-size:12px !important; }}
  .pams-responsive-table th, .pams-responsive-table td {{
    white-space:normal !important; overflow-wrap:anywhere !important;
    word-break:break-word !important; }}
  .pams-container h2 {{ clear:both !important; margin-top:24px !important; }}
}}
</style>
</head>
<body class="pams-report" style="margin:0;padding:16px;font-family:Arial,sans-serif;color:#111827">
<div class="pams-container" style="max-width:760px;margin:0 auto">
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
<table role="presentation" class="pams-wide-tables" width="100%" style="border-collapse:collapse;width:100%;table-layout:auto"><tr><td style="padding:0;vertical-align:top">
<h2 style="font-size:18px;margin-top:24px">Today's Contributors</h2>
<table class="pams-desktop-only" width="100%" style="border-collapse:collapse;width:100%;table-layout:auto">
<thead><tr>{headers((("Rank", "5%", "right"), ("Market", "8%", "left"), ("Symbol", "9%", "left"), ("Name", "38%", "left"), ("Quote Date", "12%", "left"), ("Today's P/L", "15%", "right"), ("Today's P/L %", "13%", "right")), font_size="14px")}</tr></thead>
<tbody>{contributor_rows}</tbody></table>
<table class="pams-mobile-only" style="border-collapse:collapse;width:100%;table-layout:fixed">
<thead><tr>{headers((("Rank", "12%", "right"), ("Symbol", "24%", "left"), ("Today's P/L", "34%", "right"), ("Today's P/L %", "30%", "right")), font_size="12px")}</tr></thead>
<tbody>{mobile_contributor_rows}</tbody></table>
<p style="font-size:12px;line-height:1.5;margin:10px 0 24px;color:{NEUTRAL_COLOR}">Ranked by Today's P/L from highest to lowest.</p>
<h2 style="font-size:18px;margin-top:24px">Holdings</h2>
<table class="pams-desktop-only" style="border-collapse:collapse;width:100%;table-layout:auto">
<thead><tr>{headers((("Market", "6%", "left"), ("Symbol", "7%", "left"), ("Name", None, "left"), ("Currency", "7%", "left"), ("Quantity", "7%", "right"), ("Average Cost", "9%", "right"), ("Close", "7%", "right"), ("Quote Date", "9%", "right"), ("FX", "6%", "right"), ("Unrealized P/L", "9%", "right"), ("Return %", "6%", "right"), ("Market Value", "9%", "right")), font_size="13px")}</tr></thead>
<tbody>{rows}</tbody></table>
<div class="pams-mobile-only" style="width:100%;margin:0 0 24px">
{mobile_holding_cards}</div>
{DailyReportSectionRenderer().html(report.sections)}
</td></tr></table>
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
