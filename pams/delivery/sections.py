"""Email-safe HTML and plain-text rendering for modular report sections."""

# ruff: noqa: ANN001, ANN201, ANN205

from decimal import Decimal
from html import escape

from domain import DailyReportSections, NewsSection
from pams.delivery.formatting import format_percentage, format_twd_amount
from pams.delivery.html_styles import (
    NEUTRAL_COLOR,
    taiwan_performance_color,
    taiwan_performance_color_from_text,
)

MUTED = NEUTRAL_COLOR

Cell = str | tuple[str, str]


def _money(value: Decimal, *, signed: bool = False) -> str:
    return format_twd_amount(value, signed=signed)


def _price(value: Decimal | None) -> str:
    return "N/A" if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


def _percent(value: Decimal) -> str:
    return format_percentage(value)


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
        '<table class="pams-responsive-table" style="border-collapse:collapse;'
        'width:100%;table-layout:auto">'
        f"<thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"
    )


def _dividend_calendar_table(
    headers: tuple[tuple[str, bool], ...], rows: list[tuple[Cell, ...]]
) -> str:
    """Render the wide dividend table within email and print boundaries."""
    column_widths = ("10%", "11%", "7%", "10%", "10%", "11%", "14%", "14%", "13%")
    columns = "".join(f'<col style="width:{width}">' for width in column_widths)
    wrapping_style = (
        "vertical-align:middle;white-space:normal;overflow-wrap:anywhere;"
        "word-break:break-word"
    )
    header = "".join(
        '<th style="padding:6px 4px;border:1px solid #d1d5db;'
        f'background:#f3f4f6;text-align:{"right" if numeric else "left"};'
        f'{wrapping_style}">{escape(label)}</th>'
        for label, numeric in headers
    )
    body = "".join(
        "<tr>"
        + "".join(
            '<td style="padding:6px 4px;border:1px solid #d1d5db;'
            f'text-align:{"right" if headers[index][1] else "left"};'
            f"{wrapping_style}"
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
        '<table class="pams-responsive-table" style="border-collapse:collapse;'
        "width:100%;max-width:100%;"
        'table-layout:fixed;font-size:11px">'
        f"<colgroup>{columns}</colgroup>"
        f"<thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"
    )


def _empty(message: str) -> str:
    return f'<p style="color:{MUTED};margin:6px 0 16px">{escape(message)}</p>'


class DailyReportSectionRenderer:
    """Render each optional section independently in a fixed report order."""

    def html(self, sections: DailyReportSections) -> str:
        renderers = (
            self.allocation_html(sections),
            self.upcoming_events_html(sections),
            self.dividend_calendar_html(sections),
            self.financing_html(sections),
            self.currency_exposure_html(sections),
            self.market_snapshot_html(sections),
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
            self.upcoming_events_text(sections),
            self.dividend_calendar_text(sections),
            self.financing_text(sections),
            self.currency_exposure_text(sections),
            self.market_snapshot_text(sections),
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
            ("By Currency", value.by_currency),
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
        table = _dividend_calendar_table(
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
    def financing_html(sections: DailyReportSections) -> str:
        value = sections.financing_leverage
        if value is None:
            return ""

        def subsection(title: str, rows: tuple[tuple[str, str], ...]) -> str:
            content = "".join(
                '<tr><td style="width:55%;padding:7px 9px;border-top:1px solid '
                '#e5e7eb;color:#4b5563;vertical-align:top">'
                f"{escape(label)}</td>"
                '<td style="width:45%;padding:7px 9px;border-top:1px solid #e5e7eb;'
                "text-align:right;vertical-align:top;font-weight:600;white-space:normal;"
                'overflow-wrap:anywhere">'
                f"{escape(display)}</td></tr>"
                for label, display in rows
            )
            return (
                '<table role="presentation" class="pams-responsive-table" '
                'style="border-collapse:collapse;width:100%;'
                "max-width:100%;table-layout:fixed;border:1px solid #d1d5db;"
                'background:#ffffff;margin:0 0 12px;font-size:12px">'
                '<tr><td colspan="2" style="padding:9px;background:#f3f4f6;'
                f'font-weight:700;font-size:14px">{escape(title)}</td></tr>'
                f"{content}</table>"
            )

        cards: list[tuple[str, tuple[tuple[str, str], ...]]] = [
            (
                "Overall Financing",
                (
                    ("Total Principal Debt", _money(value.total_principal_debt)),
                    (
                        "Total Accrued Interest",
                        (
                            _money(value.total_accrued_interest)
                            if value.total_accrued_interest is not None
                            else "N/A"
                        ),
                    ),
                    (
                        "Total Debt (Principal + Interest)",
                        (
                            _money(value.total_debt)
                            if value.total_debt is not None
                            else "N/A"
                        ),
                    ),
                    (
                        "Total Stock Market Value",
                        _money(value.total_stock_market_value),
                    ),
                    ("Net Stock Equity", _money(value.net_stock_equity)),
                    ("Liability Ratio", _percent(value.liability_ratio)),
                ),
            )
        ]
        if value.margin_financing is not None:
            margin = value.margin_financing
            cards.append(
                (
                    "Margin Financing",
                    (
                        ("Principal", _money(margin.principal)),
                        (
                            "Accrued Interest",
                            (
                                _money(margin.accrued_interest)
                                if margin.accrued_interest is not None
                                else "N/A"
                            ),
                        ),
                        ("Margin Position", margin.position_symbol or "N/A"),
                        (
                            "Margin Quantity",
                            (
                                f"{margin.position_quantity:,.0f} shares"
                                if margin.position_quantity is not None
                                else "N/A"
                            ),
                        ),
                        ("Updated", str(margin.updated or "N/A")),
                    ),
                )
            )
        if value.stock_pledge is not None:
            pledge = value.stock_pledge
            collateral = (
                "; ".join(
                    f"{item.symbol} — {item.quantity:,.0f} shares"
                    for item in pledge.collateral_holdings
                )
                or "N/A"
            )
            cards.append(
                (
                    "Stock Pledge",
                    (
                        ("Principal", _money(pledge.principal)),
                        (
                            "Accrued Interest",
                            (
                                _money(pledge.accrued_interest)
                                if pledge.accrued_interest is not None
                                else "N/A"
                            ),
                        ),
                        (
                            "Repayment Total",
                            (
                                _money(pledge.repayment_total)
                                if pledge.repayment_total is not None
                                else "N/A"
                            ),
                        ),
                        (
                            "Collateral Market Value",
                            (
                                _money(pledge.collateral_market_value)
                                if pledge.collateral_market_value is not None
                                else "N/A"
                            ),
                        ),
                        (
                            "Maintenance Ratio",
                            (
                                _percent(pledge.maintenance_ratio)
                                if pledge.maintenance_ratio is not None
                                else "N/A"
                            ),
                        ),
                        ("Collateral Holdings", collateral),
                        ("Updated", str(pledge.updated or "N/A")),
                    ),
                )
            )
        rendered_sections = "".join(subsection(title, rows) for title, rows in cards)
        return (
            '<h2 style="font-size:18px;margin-top:24px">Financing &amp; Leverage</h2>'
            + rendered_sections
        )

    @staticmethod
    def currency_exposure_html(sections: DailyReportSections) -> str:
        value = sections.currency_exposure
        if value is None:
            return ""

        def display_money(amount: Decimal | None) -> str:
            return _money(amount) if amount is not None else "N/A"

        rows = (
            ("TWD Exposure", display_money(value.twd_exposure)),
            ("USD Exposure in TWD", display_money(value.usd_exposure_twd)),
            (
                "Total Quoted Market Value",
                display_money(value.total_quoted_market_value),
            ),
            (
                "USD Portfolio Weight",
                (
                    _percent(value.usd_portfolio_weight)
                    if value.usd_portfolio_weight is not None
                    else "N/A"
                ),
            ),
            (
                "USD/TWD Rate",
                (
                    f"{value.usd_twd_rate:,.4f}".rstrip("0").rstrip(".")
                    if value.usd_twd_rate is not None
                    else "N/A"
                ),
            ),
            ("FX Rate Date", str(value.fx_rate_date or "N/A")),
            (
                "Estimated impact of a 1% USD/TWD move",
                display_money(value.estimated_one_percent_usd_move),
            ),
        )
        body = "".join(
            '<tr><td style="padding:8px 10px;border-top:1px solid #e5e7eb;'
            'color:#4b5563">'
            f"{escape(label)}</td>"
            '<td style="padding:8px 10px;border-top:1px solid #e5e7eb;'
            'text-align:right;white-space:nowrap;color:#111827">'
            f"{escape(display)}</td></tr>"
            for label, display in rows
        )
        return (
            '<h2 style="font-size:18px;margin-top:24px">Currency Exposure</h2>'
            '<table role="presentation" class="pams-responsive-table" '
            'style="border-collapse:collapse;width:100%;'
            "max-width:100%;table-layout:fixed;border:1px solid #d1d5db;"
            'background:#ffffff;font-size:12px">'
            '<tr><td colspan="2" style="padding:9px;background:#f3f4f6;'
            'font-weight:700;font-size:14px">Overall Currency Exposure</td></tr>'
            f"{body}</table>"
            '<p style="color:#6b7280;font-size:12px;margin:8px 0 16px">'
            "The 1% USD/TWD move impact is a sensitivity estimate, not a forecast."
            "</p>"
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

    def financing_text(self, sections):
        value = sections.financing_leverage
        if value is None:
            return ""
        lines = [
            "Financing & Leverage",
            "Overall Financing",
            f"Total Principal Debt: {_money(value.total_principal_debt)}",
            "Total Accrued Interest: "
            + (
                _money(value.total_accrued_interest)
                if value.total_accrued_interest is not None
                else "N/A"
            ),
            "Total Debt (Principal + Interest): "
            + (_money(value.total_debt) if value.total_debt is not None else "N/A"),
            f"Total Stock Market Value: {_money(value.total_stock_market_value)}",
            f"Net Stock Equity: {_money(value.net_stock_equity)}",
            f"Liability Ratio: {_percent(value.liability_ratio)}",
        ]
        if value.margin_financing is not None:
            margin = value.margin_financing
            lines.extend(
                (
                    "",
                    "Margin Financing",
                    f"Principal: {_money(margin.principal)}",
                    "Accrued Interest: "
                    + (
                        _money(margin.accrued_interest)
                        if margin.accrued_interest is not None
                        else "N/A"
                    ),
                    f"Margin Position: {margin.position_symbol or 'N/A'}",
                    "Margin Quantity: "
                    + (
                        f"{margin.position_quantity:,.0f} shares"
                        if margin.position_quantity is not None
                        else "N/A"
                    ),
                    f"Updated: {margin.updated or 'N/A'}",
                )
            )
        if value.stock_pledge is not None:
            pledge = value.stock_pledge
            lines.extend(
                (
                    "",
                    "Stock Pledge",
                    f"Principal: {_money(pledge.principal)}",
                    "Accrued Interest: "
                    + (
                        _money(pledge.accrued_interest)
                        if pledge.accrued_interest is not None
                        else "N/A"
                    ),
                    "Repayment Total: "
                    + (
                        _money(pledge.repayment_total)
                        if pledge.repayment_total is not None
                        else "N/A"
                    ),
                    "Collateral Market Value: "
                    + (
                        _money(pledge.collateral_market_value)
                        if pledge.collateral_market_value is not None
                        else "N/A"
                    ),
                    "Maintenance Ratio: "
                    + (
                        _percent(pledge.maintenance_ratio)
                        if pledge.maintenance_ratio is not None
                        else "N/A"
                    ),
                    "Collateral Holdings:",
                    *(
                        f"{item.symbol} — {item.quantity:,.0f} shares"
                        for item in pledge.collateral_holdings
                    ),
                    f"Updated: {pledge.updated or 'N/A'}",
                )
            )
        return "\n".join(lines)

    def currency_exposure_text(self, sections):
        value = sections.currency_exposure
        if value is None:
            return ""

        def money(amount: Decimal | None) -> str:
            return _money(amount) if amount is not None else "N/A"

        return "\n".join(
            (
                "Currency Exposure",
                "Overall Currency Exposure",
                f"TWD Exposure: {money(value.twd_exposure)}",
                f"USD Exposure in TWD: {money(value.usd_exposure_twd)}",
                f"Total Quoted Market Value: {money(value.total_quoted_market_value)}",
                "USD Portfolio Weight: "
                + (
                    _percent(value.usd_portfolio_weight)
                    if value.usd_portfolio_weight is not None
                    else "N/A"
                ),
                "USD/TWD Rate: "
                + (
                    f"{value.usd_twd_rate:,.4f}".rstrip("0").rstrip(".")
                    if value.usd_twd_rate is not None
                    else "N/A"
                ),
                f"FX Rate Date: {value.fx_rate_date or 'N/A'}",
                "Estimated impact of a 1% USD/TWD move: "
                + money(value.estimated_one_percent_usd_move),
                "This is a sensitivity estimate, not a forecast.",
            )
        )

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
