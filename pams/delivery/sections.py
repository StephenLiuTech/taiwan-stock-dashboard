"""Email-safe HTML and plain-text rendering for modular report sections."""

# ruff: noqa: ANN001, ANN201, ANN205

from decimal import Decimal
from html import escape

from domain import DailyReportSections, NewsSection
from pams.delivery.html_styles import (
    NEUTRAL_COLOR,
    taiwan_performance_color,
    taiwan_performance_color_from_text,
)

MUTED = NEUTRAL_COLOR

Cell = str | tuple[str, str]


def _money(value: Decimal, *, signed: bool = False) -> str:
    prefix = "+" if signed and value > 0 else ""
    sign = "-" if value < 0 else prefix
    return f"{sign}NT${abs(value):,.0f}"


def _price(value: Decimal | None) -> str:
    return "N/A" if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


def _percent(value: Decimal) -> str:
    return f"{value * 100:,.2f}%"


def _dividend_per_share(value: Decimal | None) -> str:
    return "N/A" if value is None else f"{value:,.8f}".rstrip("0").rstrip(".")


def _table(headers: tuple[tuple[str, bool], ...], rows: list[tuple[Cell, ...]]) -> str:
    header = "".join(
        '<th style="padding:9px 6px;border:1px solid #d1d5db;'
        f'background:#f3f4f6;text-align:{"right" if numeric else "left"};'
        'vertical-align:middle;white-space:nowrap">'
        f"{escape(label)}</th>"
        for label, numeric in headers
    )
    body = "".join(
        "<tr>"
        + "".join(
            '<td style="padding:9px 6px;border:1px solid #d1d5db;'
            f'text-align:{"right" if headers[index][1] else "left"};'
            "vertical-align:middle;white-space:nowrap"
            + (f";color:{value[1]};font-weight:600" if isinstance(value, tuple) else "")
            + '">'
            + escape(value[0] if isinstance(value, tuple) else value)
            + "</td>"
            for index, value in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return (
        '<table style="border-collapse:collapse;width:100%;table-layout:auto">'
        f"<thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"
    )


def _empty(message: str) -> str:
    return f'<p style="color:{MUTED};margin:6px 0 16px">{escape(message)}</p>'


class DailyReportSectionRenderer:
    """Render each optional section independently in a fixed report order."""

    def html(self, sections: DailyReportSections) -> str:
        renderers = (
            self.allocation_html(sections),
            self.market_snapshot_html(sections),
            self.upcoming_events_html(sections),
            self.dividend_calendar_html(sections),
            self.news_html("AI News", sections.ai_news),
            self.news_html("Semiconductor News", sections.semiconductor_news),
            self.insights_html(sections),
            self.risk_html(sections),
            self.watchlist_html(sections),
            self.transactions_html(sections),
        )
        return "".join(renderers)

    def text(self, sections: DailyReportSections) -> str:
        blocks = (
            self.allocation_text(sections),
            self.market_snapshot_text(sections),
            self.upcoming_events_text(sections),
            self.dividend_calendar_text(sections),
            self.news_text("AI News", sections.ai_news),
            self.news_text("Semiconductor News", sections.semiconductor_news),
            self.insights_text(sections),
            self.risk_text(sections),
            self.watchlist_text(sections),
            self.transactions_text(sections),
        )
        return "\n\n".join(block for block in blocks if block)

    @staticmethod
    def allocation_html(sections: DailyReportSections) -> str:
        value = sections.allocation
        if value is None:
            return ""
        holding_rows = [
            (
                item.label,
                item.name or "N/A",
                _money(item.market_value),
                _percent(item.weight),
            )
            for item in value.by_holding
        ]
        html = '<h2 style="font-size:18px;margin-top:24px">Portfolio Allocation</h2>'
        html += "<h3>By Holding</h3>" + (
            _table(
                (
                    ("Symbol", False),
                    ("Name", False),
                    ("Market Value", True),
                    ("Weight", True),
                ),
                holding_rows,
            )
            if holding_rows
            else _empty("No quoted holdings available")
        )
        for title, items in (
            ("By Market", value.by_market),
            ("By Instrument Type", value.by_instrument),
        ):
            html += f"<h3>{title}</h3>" + _table(
                (("Classification", False), ("Market Value", True), ("Weight", True)),
                [
                    (item.label, _money(item.market_value), _percent(item.weight))
                    for item in items
                ],
            )
        html += _empty(value.classification_status)
        if value.unquoted_holdings:
            html += _empty(
                "Without eligible quote: " + ", ".join(value.unquoted_holdings)
            )
        return html

    @staticmethod
    def market_snapshot_html(sections: DailyReportSections) -> str:
        value = sections.market_snapshot
        if value is None:
            return ""
        title = '<h2 style="font-size:18px;margin-top:24px">Market Snapshot</h2>'
        if not value.items:
            return title + _empty(value.status)
        return title + _table(
            (
                ("Instrument", False),
                ("Value", True),
                ("Change", True),
                ("Change %", True),
                ("Quoted At", False),
                ("Status", False),
            ),
            [
                (
                    item.display_name,
                    _price(item.value),
                    (
                        _price(item.daily_change),
                        taiwan_performance_color(item.daily_change),
                    ),
                    (
                        (
                            "N/A"
                            if item.daily_change_percentage is None
                            else f"{item.daily_change_percentage * 100:.2f}%"
                        ),
                        taiwan_performance_color(item.daily_change_percentage),
                    ),
                    item.quoted_at.isoformat() if item.quoted_at else "N/A",
                    item.source_status,
                )
                for item in value.items
            ],
        )

    @staticmethod
    def upcoming_events_html(sections: DailyReportSections) -> str:
        value = sections.upcoming_events
        if value is None:
            return ""
        title = '<h2 style="font-size:18px;margin-top:24px">Upcoming Events</h2>'
        if not value.items:
            return title + _empty(value.status)
        return title + _table(
            (
                ("Date", False),
                ("Type", False),
                ("Symbol / Scope", False),
                ("Title", False),
                ("Holding", False),
                ("Source / Status", False),
            ),
            [
                (
                    str(item.event_date),
                    item.event_type,
                    item.symbol_or_scope,
                    item.title,
                    "Yes" if item.relevant_to_holding else "No",
                    item.source_status,
                )
                for item in value.items
            ],
        )

    @staticmethod
    def dividend_calendar_html(sections: DailyReportSections) -> str:
        value = sections.dividend_calendar
        if value is None:
            return ""
        title = '<h2 style="font-size:18px;margin-top:24px">Dividend Calendar</h2>'
        if not value.items:
            return "" if value.hide_when_empty else title + _empty(value.status)
        table = _table(
            (
                ("Ex-Date", False),
                ("Payment Date", False),
                ("Symbol", False),
                ("Name", False),
                ("Cash / Share", True),
                ("Eligible Quantity", True),
                ("Estimated Cash Dividend", True),
                ("Actual Cash Received", True),
                ("Status", False),
                ("Source", False),
            ),
            [
                (
                    str(item.ex_dividend_date),
                    str(item.payment_date or "N/A"),
                    item.symbol,
                    item.name,
                    _dividend_per_share(item.dividend_per_share),
                    (
                        f"{item.eligible_quantity:,.0f}"
                        if item.eligible_quantity
                        == item.eligible_quantity.to_integral()
                        else f"{item.eligible_quantity:,}"
                    ),
                    (
                        _money(item.estimated_cash_dividend)
                        if item.estimated_cash_dividend is not None
                        else "N/A"
                    ),
                    (
                        _money(item.actual_cash_received)
                        if item.actual_cash_received is not None
                        else "N/A"
                    ),
                    item.status,
                    item.source_status,
                )
                for item in value.items
            ],
        )
        summary = _table(
            (("Estimate", False), ("Amount", True)),
            [
                (
                    "Estimated Annual Dividend",
                    _money(value.estimated_annual_dividend),
                ),
                ("Already Received", _money(value.already_received)),
                ("Waiting for Payment", _money(value.waiting_for_payment)),
                ("Upcoming Ex-Date", _money(value.upcoming_ex_date)),
                ("Unknown Payment Date", _money(value.unknown_payment_date)),
            ],
        )
        note = (
            '<p style="color:#6b7280;font-size:12px;margin:8px 0">'
            "Estimated amounts use transaction-reconstructed eligible quantities; "
            "they are not confirmed broker settlements. Actual Cash Received means "
            "the dividend is expected to have been paid according to the official "
            "payment date. It does not verify actual broker settlement.</p>"
        )
        return (
            title + table + '<div style="margin-top:12px">' + summary + note + "</div>"
        )

    @staticmethod
    def news_html(title: str, value: NewsSection | None) -> str:
        if value is None:
            return ""
        heading = f'<h2 style="font-size:18px;margin-top:24px">{title}</h2>'
        if not value.items:
            return heading + _empty(value.status)
        rows = "".join(
            '<div style="margin:0 0 14px"><a href="'
            + escape(item.url, quote=True)
            + '"><strong>'
            + escape(item.headline)
            + "</strong></a><br>"
            + escape(f"{item.publisher} · {item.published_at.isoformat()}")
            + "<br>"
            + escape(item.summary)
            + "<br><em>"
            + escape(item.relevance_reason)
            + "</em></div>"
            for item in value.items
        )
        return heading + rows

    @staticmethod
    def insights_html(sections: DailyReportSections) -> str:
        value = sections.insights
        if value is None:
            return ""
        title = '<h2 style="font-size:18px;margin-top:24px">Portfolio Insights</h2>'
        return title + (
            "<ul>"
            + "".join(
                (
                    '<li style="color:'
                    + taiwan_performance_color_from_text(item)
                    + '">'
                    + escape(item)
                    + "</li>"
                    if "market value changed" in item.lower()
                    else f"<li>{escape(item)}</li>"
                )
                for item in value.insights
            )
            + "</ul>"
            if value.insights
            else _empty(value.status)
        )

    @staticmethod
    def risk_html(sections: DailyReportSections) -> str:
        value = sections.risk
        if value is None:
            return ""
        return '<h2 style="font-size:18px;margin-top:24px">Risk Monitor</h2>' + _table(
            (("Fact", False), ("Value", True), ("Status", False)),
            [
                (
                    item.label,
                    (
                        (
                            item.value,
                            taiwan_performance_color_from_text(item.value),
                        )
                        if item.label
                        in {
                            "Largest unrealized loss",
                            "Largest daily loss contributor",
                        }
                        else item.value
                    ),
                    "WARNING" if item.warning else "Fact",
                )
                for item in value.items
            ],
        )

    @staticmethod
    def watchlist_html(sections: DailyReportSections) -> str:
        value = sections.watchlist
        if value is None or not value.items:
            return ""
        return '<h2 style="font-size:18px;margin-top:24px">Watchlist</h2>' + _table(
            (
                ("Symbol", False),
                ("Market", False),
                ("Name", False),
                ("Latest", True),
                ("Quote Date", False),
                ("Target", True),
                ("Buy Below", True),
                ("Notes", False),
            ),
            [
                (
                    item.symbol,
                    item.market,
                    item.display_name or "N/A",
                    _price(item.latest_price),
                    str(item.quote_date or "N/A"),
                    _price(item.target_price),
                    _price(item.buy_below_price),
                    item.notes or "N/A",
                )
                for item in value.items
            ],
        )

    @staticmethod
    def transactions_html(sections: DailyReportSections) -> str:
        value = sections.transactions
        if value is None or not value.items:
            return ""
        table = (
            '<h2 style="font-size:18px;margin-top:24px">Transaction Summary</h2>'
            + _table(
                (
                    ("Type", False),
                    ("Symbol", False),
                    ("Name", False),
                    ("Market", False),
                    ("Quantity", True),
                    ("Price", True),
                    ("Gross", True),
                    ("Fees", True),
                    ("Taxes", True),
                    ("Net Cash Impact", True),
                ),
                [
                    (
                        item.transaction_type.upper(),
                        item.symbol,
                        item.name,
                        item.market,
                        (
                            f"{item.quantity:,.0f}"
                            if item.quantity == item.quantity.to_integral()
                            else str(item.quantity)
                        ),
                        _price(item.price),
                        _money(item.gross_amount),
                        _money(item.fees),
                        _money(item.taxes),
                        _money(item.net_cash_impact, signed=True),
                    )
                    for item in value.items
                ],
            )
        )
        return table + (
            '<p style="font-size:13px;color:#475569">'
            f"Buy fees: {_money(value.total_buy_fees)} &nbsp; "
            f"Sell fees: {_money(value.total_sell_fees)} &nbsp; "
            f"Taxes: {_money(value.total_taxes)} &nbsp; "
            f"Total trading expenses: {_money(value.total_trading_expenses)}"
            "</p>"
        )

    def allocation_text(self, sections):
        return self._simple_allocation(sections)

    def market_snapshot_text(self, sections):
        value = sections.market_snapshot
        if value is None:
            return ""
        if not value.items:
            return "Market Snapshot\n" + value.status
        return self._lines(
            "Market Snapshot",
            (
                f"{item.display_name} | {_price(item.value)} | "
                f"{_price(item.daily_change)} | "
                f"{_percent(item.daily_change_percentage) if item.daily_change_percentage is not None else 'N/A'} | "
                f"{item.quoted_at.isoformat() if item.quoted_at else 'N/A'} | {item.source_status}"
                for item in value.items
            ),
        )

    def upcoming_events_text(self, sections):
        return self._event_text(sections)

    def dividend_calendar_text(self, sections):
        return self._dividend_text(sections)

    def insights_text(self, sections):
        return (
            self._lines("Portfolio Insights", sections.insights.insights)
            if sections.insights
            else ""
        )

    def risk_text(self, sections):
        return (
            self._lines(
                "Risk Monitor",
                (
                    f"{i.label}: {i.value} ({'WARNING' if i.warning else 'Fact'})"
                    for i in sections.risk.items
                ),
            )
            if sections.risk
            else ""
        )

    def watchlist_text(self, sections):
        return (
            self._lines(
                "Watchlist",
                (
                    f"{i.symbol} | {i.market} | {_price(i.latest_price)} | {i.quote_date or 'N/A'}"
                    for i in sections.watchlist.items
                ),
            )
            if sections.watchlist and sections.watchlist.items
            else ""
        )

    def transactions_text(self, sections):
        if not sections.transactions or not sections.transactions.items:
            return ""
        value = sections.transactions
        return self._lines(
            "Transaction Summary",
            [
                f"{i.transaction_type.upper()} | {i.symbol} | {i.quantity} | {_price(i.price)} | {_money(i.net_cash_impact, signed=True)}"
                for i in value.items
            ]
            + [
                f"Total Buy Fees: {_money(value.total_buy_fees)}",
                f"Total Sell Fees: {_money(value.total_sell_fees)}",
                f"Total Taxes: {_money(value.total_taxes)}",
                "Total Trading Expenses: " f"{_money(value.total_trading_expenses)}",
            ],
        )

    def news_text(self, title, value):
        return (
            self._lines(
                title,
                (
                    f"{i.headline} | {i.publisher} | {i.url} | {i.relevance_reason}"
                    for i in value.items
                ),
            )
            if value and value.items
            else (f"{title}\n{value.status}" if value else "")
        )

    @staticmethod
    def _lines(title, values):
        return title + "\n" + "\n".join(values)

    @staticmethod
    def _simple_allocation(sections):
        value = sections.allocation
        return (
            ""
            if value is None
            else "Portfolio Allocation\n"
            + "\n".join(
                f"{i.label} | {_money(i.market_value)} | {_percent(i.weight)}"
                for i in value.by_holding
            )
        )

    @staticmethod
    def _event_text(sections):
        value = sections.upcoming_events
        return (
            ""
            if value is None
            else "Upcoming Events\n"
            + (
                "\n".join(
                    f"{i.event_date} | {i.event_type} | {i.symbol_or_scope} | {i.title}"
                    for i in value.items
                )
                or value.status
            )
        )

    @staticmethod
    def _dividend_text(sections):
        value = sections.dividend_calendar
        return (
            ""
            if value is None or (not value.items and value.hide_when_empty)
            else "Dividend Calendar\n"
            + (
                "\n".join(
                    f"{i.ex_dividend_date} | {i.payment_date or 'N/A'} | "
                    f"{i.symbol} | {i.name} | "
                    f"{_dividend_per_share(i.dividend_per_share)} | {i.eligible_quantity} | "
                    f"{_money(i.estimated_cash_dividend) if i.estimated_cash_dividend is not None else 'N/A'} | "
                    f"{_money(i.actual_cash_received) if i.actual_cash_received is not None else 'N/A'} | "
                    f"{i.status} | {i.source_status}"
                    for i in value.items
                )
                or value.status
            )
            + (
                "\nDividend Estimate Summary"
                f"\nEstimated Annual Dividend | {_money(value.estimated_annual_dividend)}"
                f"\nAlready Received | {_money(value.already_received)}"
                f"\nWaiting for Payment | {_money(value.waiting_for_payment)}"
                f"\nUpcoming Ex-Date | {_money(value.upcoming_ex_date)}"
                f"\nUnknown Payment Date | {_money(value.unknown_payment_date)}"
                "\nActual Cash Received means the dividend is expected to have been paid according to the official payment date. It does not verify actual broker settlement."
                if value.items
                else ""
            )
        )
