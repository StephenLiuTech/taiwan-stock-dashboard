"""Plain-text and email-compatible HTML daily report rendering."""

from datetime import date, timedelta
from decimal import Decimal
from html import escape
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from domain import Currency, Market, StockNetEquityQuality
from pams.application.send_daily_report import (
    ChartSource,
    DailyEmailHistoryPoint,
    DailyEmailPosition,
    DailyEmailReport,
    InlineImage,
    RenderedEmail,
    StockNetEquityHistoryPoint,
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
NET_EQUITY_CHART_CONTENT_ID = "pams-stock-net-equity-trend-chart"
NET_EQUITY_CHART_FILENAME = "pams-stock-net-equity-trend.png"
ANNUAL_CHART_CONTENT_ID = "pams-realized-total-pnl-ytd-chart"
ANNUAL_CHART_FILENAME = "pams-realized-total-pnl-ytd.png"
PORTFOLIO_TREND_SERIES = (
    "Total stock market value",
    "Daily P/L",
)
CHART_FALLBACK = (
    "Only one portfolio snapshot is available; "
    "a trend chart requires at least two snapshots."
)
POSITIVE_COLOR = TAIWAN_GAIN_COLOR
NEGATIVE_COLOR = TAIWAN_LOSS_COLOR
DAILY_PNL_PROFIT_BAR = (220, 38, 38, 105)
DAILY_PNL_LOSS_BAR = (22, 163, 74, 105)
DAILY_PNL_NEUTRAL_BAR = (107, 114, 128, 105)


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
        "Date | Total stock market value | Daily P/L",
    ]
    lines.extend(
        f"{point.snapshot_date} | {_money(point.total_market_value)} | "
        f"{_money(point.daily_profit_loss, signed=True)}"
        for point in history
    )
    return lines


def _net_equity_history_text(
    history: tuple[StockNetEquityHistoryPoint, ...],
) -> list[str]:
    available = [
        point
        for point in history
        if point.net_asset_value is not None
        and point.quality_status is not StockNetEquityQuality.UNKNOWN
    ]
    if len(available) <= 1:
        return [CHART_FALLBACK]
    lines = [
        f"Requested period: {history[0].snapshot_date} to {history[-1].snapshot_date}",
        f"Earliest available: {available[0].snapshot_date}",
        "Date | Stock net equity | Quality",
    ]
    lines.extend(
        f"{point.snapshot_date} | {_money(point.net_asset_value)} | "
        f"{point.quality_status.value}"
        for point in available
        if point.quality_status is not None
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


def _daily_pnl_bar_specs(
    history: tuple[DailyEmailHistoryPoint, ...],
) -> tuple[tuple[date, Decimal, tuple[int, int, int, int]], ...]:
    """Map available signed P/L to absolute-height, sign-colored bars."""
    return tuple(
        (
            point.snapshot_date,
            abs(point.daily_profit_loss),
            (
                DAILY_PNL_PROFIT_BAR
                if point.daily_profit_loss > 0
                else (
                    DAILY_PNL_LOSS_BAR
                    if point.daily_profit_loss < 0
                    else DAILY_PNL_NEUTRAL_BAR
                )
            ),
        )
        for point in history
        if point.daily_profit_loss is not None
    )


def _chart_png(history: tuple[DailyEmailHistoryPoint, ...]) -> bytes:
    """Render portfolio value lines and persisted daily P/L bars."""
    width, height = 1200, 650
    left, top, right, bottom = 170, 92, 170, 92
    image = Image.new("RGBA", (width, height), "white")
    draw = ImageDraw.Draw(image)
    legend_font = _chart_font(18)
    axis_font = _chart_font(16)
    chart_width = width - left - right
    chart_height = height - top - bottom

    def scale(values: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
        minimum = min(values)
        maximum = max(values)
        span = maximum - minimum
        if span == 0:
            span = abs(maximum) or Decimal("1")
            minimum -= span / Decimal("2")
            maximum += span / Decimal("2")
            span = maximum - minimum
        return minimum, maximum, span

    minimum, maximum, span = scale([point.total_market_value for point in history])
    daily_bars = _daily_pnl_bar_specs(history)
    daily_values = [value for _, value, _ in daily_bars]
    daily_maximum = max([Decimal("0"), *daily_values]) or Decimal("1")
    first_date = history[0].snapshot_date
    last_date = history[-1].snapshot_date
    date_span = max((last_date - first_date).days, 1)

    def x_coordinate(point_date: date) -> int:
        return left + round((point_date - first_date).days * chart_width / date_span)

    def line_coordinates(point_date: date, value: Decimal) -> tuple[int, int]:
        x = x_coordinate(point_date)
        ratio = (value - minimum) / span
        return x, top + int(((Decimal("1") - ratio) * chart_height).to_integral_value())

    def daily_y(value: Decimal) -> int:
        ratio = abs(value) / daily_maximum
        return top + int(((Decimal("1") - ratio) * chart_height).to_integral_value())

    legend_y = 40
    draw.line((left, legend_y, left + 44, legend_y), fill="#2563eb", width=7)
    draw.text(
        (left + 58, legend_y - 11),
        PORTFOLIO_TREND_SERIES[0],
        fill="#334155",
        font=legend_font,
    )
    pnl_legend_x = left + 410
    draw.rectangle(
        (pnl_legend_x, legend_y - 9, pnl_legend_x + 13, legend_y + 9),
        fill=TAIWAN_GAIN_COLOR,
    )
    draw.rectangle(
        (pnl_legend_x + 13, legend_y - 9, pnl_legend_x + 26, legend_y + 9),
        fill=TAIWAN_LOSS_COLOR,
    )
    draw.text(
        (pnl_legend_x + 40, legend_y - 11),
        f"{PORTFOLIO_TREND_SERIES[1]} (Profit: red; Loss: green)",
        fill="#334155",
        font=legend_font,
    )
    draw.text(
        (12, top - 34),
        "Total Stock Market Value / TWD",
        fill="#64748b",
        font=axis_font,
    )
    right_axis_title = "Daily P/L Absolute Amount / TWD"
    draw.text(
        (width - 12 - draw.textlength(right_axis_title, font=axis_font), top - 34),
        right_axis_title,
        fill="#64748b",
        font=axis_font,
    )

    for step in range(5):
        ratio = Decimal(step) / Decimal("4")
        market_value = maximum - span * ratio
        y = top + round(step * chart_height / 4)
        draw.line((left, y, left + chart_width, y), fill="#eef2f7", width=2)
        draw.text(
            (12, y - 10),
            f"NT${market_value:,.0f}",
            fill="#64748b",
            font=axis_font,
        )
        daily_axis_value = daily_maximum * (Decimal("1") - ratio)
        draw.text(
            (width - right + 14, y - 10),
            f"NT${daily_axis_value:,.0f}",
            fill="#64748b",
            font=axis_font,
        )

    monday_ticks = _monday_ticks(first_date, last_date)
    for monday in monday_ticks:
        x = x_coordinate(monday)
        draw.line((x, top, x, top + chart_height), fill="#eef2f7", width=2)
        label = monday.strftime("%m-%d")
        label_width = draw.textlength(label, font=axis_font)
        draw.text(
            (x - label_width / 2, top + chart_height + 24),
            label,
            fill="#64748b",
            font=axis_font,
        )

    x_positions = [x_coordinate(point.snapshot_date) for point in history]
    gaps = [
        later - earlier
        for earlier, later in zip(x_positions, x_positions[1:], strict=False)
        if later > earlier
    ]
    bar_half_width = max(4, min(18, min(gaps) // 3 if gaps else 12))
    zero_y = top + chart_height
    bar_layer = Image.new("RGBA", image.size, (255, 255, 255, 0))
    bar_draw = ImageDraw.Draw(bar_layer)
    for point_date, absolute_value, color in daily_bars:
        x = x_coordinate(point_date)
        value_y = daily_y(absolute_value)
        bar_draw.rectangle(
            (
                x - bar_half_width,
                min(value_y, zero_y),
                x + bar_half_width,
                max(value_y, zero_y),
            ),
            fill=color,
        )
    image = Image.alpha_composite(image, bar_layer)
    draw = ImageDraw.Draw(image)
    draw.line((left, zero_y, left + chart_width, zero_y), fill="#94a3b8", width=3)

    market_points = [
        line_coordinates(point.snapshot_date, point.total_market_value)
        for point in history
    ]
    draw.line(market_points, fill="#2563eb", width=5, joint="curve")
    for x, y in market_points:
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill="#2563eb")

    output = BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def _net_equity_chart_png(history: tuple[StockNetEquityHistoryPoint, ...]) -> bytes:
    """Render available persisted Stock Net Equity points on the shared timeline."""
    width, height = 1200, 650
    left, top, right, bottom = 170, 92, 48, 92
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    legend_font = _chart_font(18)
    axis_font = _chart_font(16)
    chart_width = width - left - right
    chart_height = height - top - bottom
    available = [
        point
        for point in history
        if point.net_asset_value is not None
        and point.quality_status is not StockNetEquityQuality.UNKNOWN
    ]
    values = [point.net_asset_value for point in available]
    minimum = min(values)
    maximum = max(values)
    span = maximum - minimum
    if span == 0:
        padding = abs(maximum) or Decimal("1")
        minimum -= padding / Decimal("2")
        maximum += padding / Decimal("2")
        span = maximum - minimum
    first_date = history[0].snapshot_date
    last_date = history[-1].snapshot_date
    date_span = max((last_date - first_date).days, 1)

    def x_coordinate(point_date: date) -> int:
        return left + round((point_date - first_date).days * chart_width / date_span)

    def coordinates(point: StockNetEquityHistoryPoint) -> tuple[int, int]:
        assert point.net_asset_value is not None
        ratio = (point.net_asset_value - minimum) / span
        return (
            x_coordinate(point.snapshot_date),
            top + int(((Decimal("1") - ratio) * chart_height).to_integral_value()),
        )

    qualities = {
        point.quality_status for point in available if point.quality_status is not None
    }
    has_estimated = StockNetEquityQuality.ESTIMATED_LIABILITY in qualities
    legend_y = 40
    draw.line((left, legend_y, left + 44, legend_y), fill="#2563eb", width=7)
    draw.text(
        (left + 58, legend_y - 11),
        "Verified" if has_estimated else "Stock Net Equity",
        fill="#334155",
        font=legend_font,
    )
    if has_estimated:
        estimated_x = left + 190
        _draw_dashed_line(
            draw, (estimated_x, legend_y), (estimated_x + 44, legend_y), "#f59e0b", 5
        )
        draw.text(
            (estimated_x + 58, legend_y - 11),
            "Estimated Liability",
            fill="#334155",
            font=legend_font,
        )
    draw.text((12, top - 34), "Stock Net Equity / TWD", fill="#64748b", font=axis_font)
    for step in range(5):
        ratio = Decimal(step) / Decimal("4")
        value = maximum - span * ratio
        y = top + round(step * chart_height / 4)
        draw.line((left, y, left + chart_width, y), fill="#eef2f7", width=2)
        draw.text((12, y - 10), f"NT${value:,.0f}", fill="#64748b", font=axis_font)
    for tick_date, label in _long_range_ticks(first_date, last_date):
        x = x_coordinate(tick_date)
        draw.line((x, top, x, top + chart_height), fill="#eef2f7", width=2)
        label_width = draw.textlength(label, font=axis_font)
        draw.text(
            (x - label_width / 2, top + chart_height + 24),
            label,
            fill="#64748b",
            font=axis_font,
        )
    segments = _net_equity_chart_segments(history)
    for quality, segment in segments:
        points = [coordinates(point) for point in segment]
        color = "#2563eb" if quality == "VERIFIED" else "#f59e0b"
        if len(points) > 1:
            if quality == "VERIFIED":
                draw.line(points, fill=color, width=5, joint="curve")
            else:
                for start, end in zip(points, points[1:], strict=False):
                    _draw_dashed_line(draw, start, end, color, 5)
        for x, y in points:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=color)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _net_equity_chart_segments(
    history: tuple[StockNetEquityHistoryPoint, ...],
) -> tuple[tuple[str, tuple[StockNetEquityHistoryPoint, ...]], ...]:
    """Split lines at real data gaps while skipping confirmed market closures."""
    segments: list[tuple[str, tuple[StockNetEquityHistoryPoint, ...]]] = []
    current_segment: list[StockNetEquityHistoryPoint] = []
    current_quality: str | None = None
    for point in history:
        if point.net_asset_value is None and point.is_expected_market_closure:
            continue
        quality = (
            point.quality_status.value if point.quality_status is not None else None
        )
        if point.net_asset_value is None or quality in (None, "UNKNOWN"):
            if current_segment:
                assert current_quality is not None
                segments.append((current_quality, tuple(current_segment)))
                current_segment = []
                current_quality = None
            continue
        if current_quality is not None and quality != current_quality:
            segments.append((current_quality, tuple(current_segment)))
            current_segment = []
        current_quality = quality
        current_segment.append(point)
    if current_segment:
        assert current_quality is not None
        segments.append((current_quality, tuple(current_segment)))
    return tuple(segments)


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    color: str,
    width: int,
) -> None:
    """Draw a conservative email-chart dashed segment."""
    x1, y1 = start
    x2, y2 = end
    distance = max(abs(x2 - x1), abs(y2 - y1), 1)
    for offset in range(0, distance, 16):
        finish = min(offset + 9, distance)
        draw.line(
            (
                x1 + (x2 - x1) * offset / distance,
                y1 + (y2 - y1) * offset / distance,
                x1 + (x2 - x1) * finish / distance,
                y1 + (y2 - y1) * finish / distance,
            ),
            fill=color,
            width=width,
        )


def _long_range_ticks(start: date, end: date) -> tuple[tuple[date, str], ...]:
    """Return readable monthly, quarterly, or annual ticks for a long timeline."""
    days = (end - start).days
    if days > 730:
        ticks = [(start, start.strftime("%Y-%m-%d"))]
        current = date(start.year + 1, 1, 1)
        while current <= end:
            ticks.append((current, current.strftime("%Y")))
            current = date(current.year + 1, 1, 1)
        return tuple(ticks)
    if days <= 120:
        month_step = 1
        label_format = "%m-%d"
    else:
        month_step = 3
        label_format = "%Y-%m"
    current = date(start.year, start.month, 1)
    if current < start:
        month = current.month - 1 + month_step
        current = date(current.year + month // 12, month % 12 + 1, 1)
    ticks = []
    while current <= end:
        ticks.append((current, current.strftime(label_format)))
        month = current.month - 1 + month_step
        current = date(current.year + month // 12, month % 12 + 1, 1)
    return tuple(ticks)


def _monday_ticks(start: date, end: date) -> tuple[date, ...]:
    """Return every Monday in an inclusive chart date range."""
    first_monday = start + timedelta(days=(7 - start.weekday()) % 7)
    ticks = []
    current = first_monday
    while current <= end:
        ticks.append(current)
        current += timedelta(days=7)
    return tuple(ticks)


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


def _annual_bar_chart(
    title: str, rows: tuple[tuple[str, Decimal], ...], *, chart_class: str
) -> str:
    """Render one conservative email-safe horizontal bar chart."""
    maximum = max((abs(value) for _, value in rows), default=Decimal("0"))
    rendered_rows = []
    for label, value in rows:
        width = (
            int((abs(value) * Decimal("100") / maximum).to_integral_value())
            if maximum != 0
            else 0
        )
        color = _tone(value)
        bar = (
            '<table role="presentation" width="100%" style="width:100%;'
            'border-collapse:collapse"><tr>'
            f'<td width="{width}%" bgcolor="{color}" '
            f'style="height:14px;background:{color};font-size:1px;line-height:14px">&nbsp;</td>'
            f'<td width="{100 - width}%" style="height:14px;font-size:1px;line-height:14px">&nbsp;</td>'
            "</tr></table>"
        )
        rendered_rows.append(
            "<tr>"
            f'<td style="padding:7px 8px 7px 0;width:24%;white-space:nowrap">{escape(label)}</td>'
            f'<td style="padding:7px 10px;width:54%">{bar}</td>'
            f'<td style="padding:7px 0 7px 8px;width:22%;text-align:right;'
            f'white-space:nowrap;color:{color};font-weight:600">'
            f"{escape(_money(value, signed=True))}</td></tr>"
        )
    return (
        f'<h3 style="font-size:16px;margin:20px 0 8px">{escape(title)}</h3>'
        f'<table class="{chart_class}" role="img" aria-label="{escape(title, quote=True)}" '
        'width="100%" style="width:100%;border-collapse:collapse;table-layout:fixed">'
        + "".join(rendered_rows)
        + "</table>"
    )


def _annual_total_pnl_chart_png(report: DailyEmailReport) -> bytes:
    """Render one realized-total YTD line from reproduced accounting facts."""
    performance = report.annual_performance
    if performance is None:  # pragma: no cover - guarded by caller
        return b""
    history = performance.history or (performance.realized,)
    width, height = 1000, 420
    left, top, right, bottom = 135, 58, 42, 82
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _chart_font(25, bold=True)
    label_font = _chart_font(15)
    draw.text((left, 18), "Realized Total P/L YTD", fill="#0f172a", font=title_font)
    values = [item.realized_total_pnl_ytd for item in history]
    minimum = min(values)
    maximum = max(values)
    if minimum == maximum:
        padding = abs(maximum) / Decimal("10") or Decimal("1")
        minimum -= padding
        maximum += padding
    span = maximum - minimum
    chart_width = width - left - right
    chart_height = height - top - bottom
    first_date = history[0].snapshot_date
    last_date = history[-1].snapshot_date
    date_span = (last_date - first_date).days

    def coordinates(index: int, value: Decimal) -> tuple[int, int]:
        x = (
            left + chart_width // 2
            if date_span == 0
            else left
            + int(
                (
                    Decimal((history[index].snapshot_date - first_date).days)
                    * chart_width
                    / Decimal(date_span)
                ).to_integral_value()
            )
        )
        ratio = (value - minimum) / span
        return x, top + int(((Decimal("1") - ratio) * chart_height).to_integral_value())

    for step in range(5):
        ratio = Decimal(step) / Decimal("4")
        value = maximum - span * ratio
        y = top + round(step * chart_height / 4)
        draw.line((left, y, left + chart_width, y), fill="#e5e7eb", width=1)
        draw.text((8, y - 9), f"NT${value:,.0f}", fill="#64748b", font=label_font)
    points = [
        coordinates(index, item.realized_total_pnl_ytd)
        for index, item in enumerate(history)
    ]
    if len(points) > 1:
        draw.line(points, fill="#2563eb", width=5, joint="curve")
    label_indices = (
        {0}
        if len(points) == 1
        else {
            round(step * (len(points) - 1) / min(len(points) - 1, 7))
            for step in range(min(len(points) - 1, 7) + 1)
        }
    )
    for index, (x, y) in enumerate(points):
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill="#2563eb")
        if index not in label_indices:
            continue
        date_label = history[index].snapshot_date.strftime("%m-%d")
        label_width = draw.textlength(date_label, font=label_font)
        draw.text(
            (x - label_width / 2, top + chart_height + 24),
            date_label,
            fill="#64748b",
            font=label_font,
        )
    latest_label = _money(history[-1].realized_total_pnl_ytd, signed=True)
    latest_x, latest_y = points[-1]
    latest_width = draw.textlength(latest_label, font=label_font)
    draw.text(
        (max(left, latest_x - latest_width - 10), max(top, latest_y - 28)),
        latest_label,
        fill="#2563eb",
        font=label_font,
    )
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _annual_total_pnl_chart(
    report: DailyEmailReport, chart_source: ChartSource | None
) -> tuple[str, InlineImage | None]:
    """Return the line chart HTML and optional CID image."""
    performance = report.annual_performance
    if performance is None:
        return "", None
    source = (
        ChartSource(uri=chart_source.annual_uri)
        if chart_source is not None and chart_source.annual_uri is not None
        else ChartSource(
            uri=f"cid:{ANNUAL_CHART_CONTENT_ID}",
            attachment=InlineImage(
                ANNUAL_CHART_CONTENT_ID,
                ANNUAL_CHART_FILENAME,
                "image/png",
                _annual_total_pnl_chart_png(report),
            ),
        )
    )
    title = f"{performance.snapshot.year} 年度總損益走勢（YTD）"
    html = (
        f'<h3 style="font-size:16px;margin:20px 0 8px">{escape(title)}</h3>'
        '<div style="font-size:11px;color:#4b5563;margin-bottom:8px">'
        "Realized Total P/L YTD</div>"
        f'<img class="pams-realized-total-pnl-ytd-chart" src="{escape(source.uri, quote=True)}" '
        f'alt="{escape(title, quote=True)}" data-chart-type="line" '
        'data-series="Realized Total P/L YTD" '
        f'data-latest-label="{escape(_money(performance.realized.realized_total_pnl_ytd, signed=True), quote=True)}" '
        'width="760" style="display:block;width:100%;max-width:760px;'
        'height:auto;border:0">'
    )
    return html, source.attachment


def _annual_performance_text(report: DailyEmailReport) -> str:
    performance = report.annual_performance
    if performance is None:
        return "\n".join(
            (
                "年度投資績效（YTD）",
                f"Warning: {report.annual_warning or 'Annual P/L data is unavailable.'}",
            )
        )
    snapshot = performance.snapshot
    realized = performance.realized
    lines = [
        f"{snapshot.year} 年度投資績效（YTD）",
        f"Realized Trading P/L YTD: {_money(realized.realized_trading_pnl_ytd, signed=True)}",
        f"Dividend Income YTD: {_money(realized.dividend_income_ytd, signed=True)}",
        f"Margin Financing Interest YTD: {_money(-realized.margin_financing_interest_ytd, signed=True)}",
        f"Stock Pledge Interest YTD: {_money(-realized.stock_pledge_interest_ytd, signed=True)}",
        f"Buy Brokerage Fees YTD: {_money(-realized.buy_brokerage_fees_ytd, signed=True)}",
        f"Realized Total P/L YTD: {_money(realized.realized_total_pnl_ytd, signed=True)}",
        "",
        f"{snapshot.year} 年度總損益走勢（YTD）",
        "Accounting Date | Realized Total P/L YTD",
        *(
            f"{item.snapshot_date} | {_money(item.realized_total_pnl_ytd, signed=True)}"
            for item in performance.history or (realized,)
        ),
        "",
        "各股票 YTD 已實現損益",
    ]
    lines.extend(
        f"{item.market} | {item.symbol} | {_money(item.realized_pnl, signed=True)}"
        for item in performance.realized_by_symbol
    )
    if not performance.realized_by_symbol:
        lines.append("No realized sales in the current calendar year.")
    return "\n".join(lines)


def _annual_performance_html(
    report: DailyEmailReport, annual_chart_html: str = ""
) -> str:
    performance = report.annual_performance
    if performance is None:
        return (
            '<div class="pams-annual-performance" style="margin-top:28px">'
            '<h2 style="font-size:18px;margin:0 0 10px">年度投資績效（YTD）</h2>'
            f'<p style="color:{NEUTRAL_COLOR};margin:0">'
            f'{escape(report.annual_warning or "Annual P/L data is unavailable.")}</p></div>'
        )
    snapshot = performance.snapshot
    realized = performance.realized
    symbols = tuple(
        (f"{item.market} {item.symbol}", item.realized_pnl)
        for item in performance.realized_by_symbol
    )
    symbol_chart = (
        _annual_bar_chart(
            "各股票 YTD 已實現損益",
            symbols,
            chart_class="pams-realized-symbol-chart",
        )
        if symbols
        else (
            '<h3 style="font-size:16px;margin:20px 0 8px">'
            "各股票 YTD 已實現損益</h3>"
            f'<p style="color:{NEUTRAL_COLOR}">No realized sales in the current calendar year.</p>'
        )
    )
    metrics = (
        ("Realized Trading P/L YTD", realized.realized_trading_pnl_ytd, None),
        (
            "Dividend Income YTD",
            realized.dividend_income_ytd,
            "#2563eb",
        ),
        (
            "Margin Financing Interest YTD",
            -realized.margin_financing_interest_ytd,
            None,
        ),
        (
            "Stock Pledge Interest YTD",
            -realized.stock_pledge_interest_ytd,
            None,
        ),
        (
            "Buy Brokerage Fees YTD",
            -realized.buy_brokerage_fees_ytd,
            None,
        ),
        (
            "Realized Total P/L YTD",
            realized.realized_total_pnl_ytd,
            None,
        ),
    )
    metric_rows = "".join(
        '<tr><td style="padding:7px 8px;border-bottom:1px solid #e5e7eb">'
        f'{escape(label)}</td><td style="padding:7px 8px;border-bottom:1px solid #e5e7eb;'
        f"text-align:right;white-space:nowrap;color:{color or _tone(display_value)};"
        f'font-weight:600">{escape(_money(display_value, signed=True))}</td></tr>'
        for label, display_value, color in metrics
    )
    return (
        '<div class="pams-annual-performance" style="margin-top:28px">'
        f'<h2 style="font-size:18px;margin:0 0 8px">{snapshot.year} 年度投資績效（YTD）</h2>'
        '<table class="pams-annual-metrics" width="100%" style="width:100%;'
        f'border-collapse:collapse">{metric_rows}</table>'
        + annual_chart_html
        + symbol_chart
        + "</div>"
    )


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
                'alt="30-day total stock market value and daily profit or loss chart" '
                'data-chart-type="mixed-line-bar" data-internal-title="none" '
                'data-point-markers="visible" '
                'data-primary-axis="Total stock market value" '
                'data-secondary-axis="Daily P/L absolute amount" '
                'data-secondary-axis-range="nonnegative" '
                'data-daily-pnl-baseline="zero" data-daily-pnl-height="absolute" '
                'data-daily-pnl-colors="profit:red,loss:green,zero:neutral" '
                'data-layer-order="daily-pnl-bars,portfolio-lines" '
                'data-series-count="2" data-horizontal-gridlines="visible" '
                'data-vertical-gridlines="monday" '
                f'data-observation-count="{len(report.history)}" '
                f'data-daily-pnl-values="{escape(",".join("null" if item.daily_profit_loss is None else str(item.daily_profit_loss) for item in report.history), quote=True)}" '
                f'data-x-axis-ticks="{escape(",".join(item.strftime("%m-%d") for item in _monday_ticks(report.history[0].snapshot_date, report.history[-1].snapshot_date)), quote=True)}" '
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
        available_net_equity = tuple(
            item
            for item in report.net_equity_history
            if item.net_asset_value is not None
            and item.quality_status is not StockNetEquityQuality.UNKNOWN
        )
        if len(available_net_equity) > 1:
            net_equity_qualities = {
                item.quality_status
                for item in available_net_equity
                if item.quality_status is not None
            }
            has_estimated_net_equity = (
                StockNetEquityQuality.ESTIMATED_LIABILITY in net_equity_qualities
            )
            net_equity_series = (
                "Verified,Estimated Liability"
                if has_estimated_net_equity
                else "Stock Net Equity"
            )
            net_equity_source = (
                ChartSource(uri=chart_source.net_equity_uri)
                if chart_source is not None and chart_source.net_equity_uri is not None
                else ChartSource(
                    uri=f"cid:{NET_EQUITY_CHART_CONTENT_ID}",
                    attachment=InlineImage(
                        NET_EQUITY_CHART_CONTENT_ID,
                        NET_EQUITY_CHART_FILENAME,
                        "image/png",
                        _net_equity_chart_png(report.net_equity_history),
                    ),
                )
            )
            net_equity_chart_html = (
                f'<img src="{escape(net_equity_source.uri, quote=True)}" '
                'alt="Stock Net Equity Trend" data-chart-type="line" '
                f'data-series="{net_equity_series}" '
                f'data-series-count="{2 if has_estimated_net_equity else 1}" '
                'data-quality-styles="VERIFIED:blue-solid,ESTIMATED_LIABILITY:orange-dashed,UNKNOWN:gap" '
                'data-primary-axis="Stock Net Equity" '
                'data-y-axis="Stock Net Equity / TWD" '
                'data-vertical-gridlines="adaptive" '
                f'data-observation-count="{len(available_net_equity)}" '
                f'data-history-start="{report.net_equity_history[0].snapshot_date}" '
                f'data-history-end="{report.net_equity_history[-1].snapshot_date}" '
                f'data-missing-count="{len(report.net_equity_history) - len(available_net_equity)}" '
                f'data-expected-closure-count="{sum(item.is_expected_market_closure for item in report.net_equity_history)}" '
                f'data-x-axis-ticks="{escape(",".join(label for _, label in _long_range_ticks(report.net_equity_history[0].snapshot_date, report.net_equity_history[-1].snapshot_date)), quote=True)}" '
                'width="760" style="display:block;width:100%;max-width:760px;'
                'height:auto;border:0">'
            )
            if net_equity_source.attachment is not None:
                inline_images += (net_equity_source.attachment,)
        else:
            net_equity_chart_html = (
                f'<p style="color:{NEUTRAL_COLOR}">{escape(CHART_FALLBACK)}</p>'
            )
        annual_chart_html, annual_chart_image = _annual_total_pnl_chart(
            report, chart_source
        )
        if annual_chart_image is not None:
            inline_images += (annual_chart_image,)
        summary_cards = (
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
            + '<td class="pams-summary-empty" style="width:33.333%"></td>'
            + "</tr><tr>"
            + _summary_card(
                "Total stock market value", _money(report.total_market_value)
            )
            + _summary_card(
                "Total unrealized P/L",
                _money(report.total_unrealized_pnl, signed=True),
                color=_tone(report.total_unrealized_pnl),
            )
            + _summary_card(
                "Total return",
                _percent(report.total_return, signed=True),
                color=_tone(report.total_return),
            )
            + "</tr><tr>"
            + _summary_card("Net stock equity", _money(report.net_asset_value))
            + _summary_card("Liabilities", _money(report.total_liabilities))
            + _summary_card("Liability ratio", _percent(report.liability_ratio))
            + "</tr>"
        )
        html = f"""<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
@media only screen and (max-width:600px) {{
  .pams-report {{ padding:10px !important; }}
  .pams-container {{ width:100% !important; max-width:100% !important; }}
  .pams-wide-tables {{ width:760px !important; min-width:760px !important;
    max-width:none !important; }}
  .pams-portfolio-summary {{ width:100% !important; table-layout:fixed !important; }}
  .pams-summary-card {{ width:33.333% !important; padding:8px 5px !important; }}
  .pams-summary-card div:first-child {{ font-size:10px !important; line-height:1.25 !important; }}
  .pams-summary-card div:last-child {{ font-size:15px !important; line-height:1.25 !important; }}
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
<table role="presentation" class="pams-portfolio-summary" width="100%" style="border-collapse:collapse;width:100%;table-layout:fixed;margin-bottom:24px">
{summary_cards}</table>
<h2 style="font-size:18px">Portfolio Trend</h2>
{chart_html}
<table role="presentation" class="pams-wide-tables" width="100%" style="border-collapse:collapse;width:100%;table-layout:auto"><tr><td style="padding:0;vertical-align:top">
<h2 style="font-size:18px;margin-top:24px">Today's Contributors</h2>
<table class="pams-canonical-report-table" width="100%" style="border-collapse:collapse;width:100%;table-layout:auto">
<thead><tr>{headers((("Rank", "5%", "right"), ("Market", "8%", "left"), ("Symbol", "9%", "left"), ("Name", "38%", "left"), ("Quote Date", "12%", "left"), ("Today's P/L", "15%", "right"), ("Today's P/L %", "13%", "right")), font_size="14px")}</tr></thead>
<tbody>{contributor_rows}</tbody></table>
<p style="font-size:12px;line-height:1.5;margin:10px 0 24px;color:{NEUTRAL_COLOR}">Ranked by Today's P/L from highest to lowest.</p>
<h2 style="font-size:18px;margin-top:24px">Holdings</h2>
<table class="pams-canonical-report-table" style="border-collapse:collapse;width:100%;table-layout:auto">
<thead><tr>{headers((("Market", "6%", "left"), ("Symbol", "7%", "left"), ("Name", None, "left"), ("Currency", "7%", "left"), ("Quantity", "7%", "right"), ("Average Cost", "9%", "right"), ("Close", "7%", "right"), ("Quote Date", "9%", "right"), ("FX", "6%", "right"), ("Unrealized P/L", "9%", "right"), ("Return %", "6%", "right"), ("Market Value", "9%", "right")), font_size="13px")}</tr></thead>
<tbody>{rows}</tbody></table>
{DailyReportSectionRenderer().html(report.sections)}
{_annual_performance_html(report, annual_chart_html)}
<h2 style="font-size:18px;margin-top:24px">Stock Net Equity Trend</h2>
{net_equity_chart_html}
</td></tr></table>
</div></body></html>"""
        section_text = DailyReportSectionRenderer().text(report.sections)
        annual_text = _annual_performance_text(report)
        return RenderedEmail(
            subject,
            "\n".join(text_lines)
            + ("\n\n" + section_text if section_text else "")
            + "\n\n"
            + annual_text
            + "\n\nStock Net Equity Trend\n"
            + "\n".join(_net_equity_history_text(report.net_equity_history))
            + "\n",
            html,
            inline_images,
        )
