"""Responsive HTML regression tests for the daily portfolio report."""

import re
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from domain import Currency, Market
from pams.application.send_daily_report import DailyEmailPosition, DailyEmailReport
from pams.delivery import DailyEmailReportRenderer


@pytest.fixture
def multi_market_mobile_report() -> DailyEmailReport:
    """Representative production-shaped report with all seven current positions."""
    symbols = (
        ("0050", "Yuanta Taiwan 50", Market.TWSE, Currency.TWD),
        ("2027", "Ta Chen", Market.TWSE, Currency.TWD),
        ("2330", "TSMC", Market.TWSE, Currency.TWD),
        ("3293", "International Games System", Market.TPEX, Currency.TWD),
        ("8299", "Phison", Market.TPEX, Currency.TWD),
        ("MU", "Micron Technology Inc", Market.US, Currency.USD),
        ("DRAM", "Roundhill Memory ETF", Market.US, Currency.USD),
    )
    positions = tuple(
        DailyEmailPosition(
            symbol=symbol,
            name=name,
            quantity=Decimal(index * 10),
            average_cost=Decimal("100.25"),
            close_price=Decimal("110.50"),
            daily_return=Decimal("0.0125"),
            market_value=Decimal(index * 10000),
            unrealized_pnl=Decimal(index * 500),
            unrealized_return=Decimal("0.1022443890"),
            portfolio_weight=Decimal("0.1428571429"),
            daily_profit_loss=Decimal(index * 125),
            daily_profit_loss_percentage=Decimal("0.0125"),
            daily_profit_loss_share=Decimal("0.1428571429"),
            market=market,
            native_currency=currency,
            quote_date=date(2026, 8, 7),
            fx_rate=Decimal("31.42") if market is Market.US else Decimal("1"),
            fx_rate_date=date(2026, 8, 7) if market is Market.US else None,
        )
        for index, (symbol, name, market, currency) in enumerate(symbols, start=1)
    )
    return DailyEmailReport(
        report_date=date(2026, 8, 7),
        verified_source_date=date(2026, 8, 7),
        total_market_value=Decimal("280000"),
        total_cost_basis=Decimal("250000"),
        total_unrealized_pnl=Decimal("14000"),
        total_return=Decimal("0.056"),
        total_liabilities=Decimal("1593000"),
        net_asset_value=Decimal("-1313000"),
        liability_ratio=Decimal("5.6892857143"),
        daily_profit_loss=Decimal("3500"),
        daily_profit_loss_percentage=Decimal("0.0125"),
        history=(),
        positions=positions,
    )


def test_desktop_holdings_retains_all_fourteen_columns(
    multi_market_mobile_report: DailyEmailReport,
) -> None:
    html = DailyEmailReportRenderer().render(multi_market_mobile_report).html
    holdings = html.split('<table class="pams-desktop-only"', 2)[2].split(
        "</table>", 1
    )[0]
    labels = (
        "Market",
        "Symbol",
        "Name",
        "Currency",
        "Quantity",
        "Average Cost",
        "Close",
        "Quote Date",
        "FX",
        "Today&#x27;s P/L",
        "Today&#x27;s P/L %",
        "Unrealized P/L",
        "Return %",
        "Market Value",
    )
    assert sum(holdings.count(f">{label}</th>") for label in labels) == 14


def test_mobile_contributors_use_the_simplified_four_columns(
    multi_market_mobile_report: DailyEmailReport,
) -> None:
    html = DailyEmailReportRenderer().render(multi_market_mobile_report).html
    mobile = html.split('<table class="pams-mobile-only"', 1)[1].split("</table>", 1)[0]
    assert all(
        f">{label}</th>" in mobile
        for label in ("Rank", "Symbol", "Today&#x27;s P/L", "Today&#x27;s P/L %")
    )
    assert all(
        f">{label}</th>" not in mobile
        for label in ("Market", "Name", "Quote Date", "Share of Net P/L")
    )
    assert "white-space:nowrap" in mobile


def test_mobile_holdings_render_seven_readable_cards_with_matching_values(
    multi_market_mobile_report: DailyEmailReport,
) -> None:
    html = DailyEmailReportRenderer().render(multi_market_mobile_report).html
    mobile = html.split('<div class="pams-mobile-only"', 1)[1].split("</div>", 1)[0]
    assert mobile.count('class="pams-mobile-holding-card"') == 7
    for position in multi_market_mobile_report.positions:
        assert f"<strong>{position.symbol}</strong>" in mobile
        value = f"+NT${position.daily_profit_loss:,.0f}"
        # The same calculated value is rendered in desktop and mobile presentations.
        assert html.count(value) >= 3
    assert "Micron Technology Inc" in mobile
    assert "&middot; FX 31.42" in mobile


def test_desktop_and_mobile_holdings_share_unrealized_pnl_order(
    multi_market_mobile_report: DailyEmailReport,
) -> None:
    values = {
        "0050": Decimal("154250"),
        "2027": Decimal("-990"),
        "2330": Decimal("89175"),
        "3293": Decimal("125468"),
        "8299": Decimal("-115118"),
        "MU": Decimal("-17446"),
        "DRAM": Decimal("-46350"),
    }
    report = replace(
        multi_market_mobile_report,
        positions=tuple(
            replace(position, unrealized_pnl=values[position.symbol])
            for position in reversed(multi_market_mobile_report.positions)
        ),
    )

    html = DailyEmailReportRenderer().render(report).html
    holdings_section = html.split(">Holdings</h2>", 1)[1]
    desktop = holdings_section.split('<table class="pams-desktop-only"', 1)[1].split(
        "</table>", 1
    )[0]
    mobile = holdings_section.split('<div class="pams-mobile-only"', 1)[1].split(
        "</div>", 1
    )[0]
    expected = ("0050", "3293", "2330", "2027", "MU", "DRAM", "8299")

    assert tuple(sorted(expected, key=desktop.index)) == expected
    assert (
        tuple(sorted(expected, key=lambda symbol: mobile.index(f">{symbol}</strong>")))
        == expected
    )


def test_holdings_order_ties_by_market_then_symbol_and_places_unavailable_last(
    multi_market_mobile_report: DailyEmailReport,
) -> None:
    report = replace(
        multi_market_mobile_report,
        positions=tuple(
            replace(
                position,
                unrealized_pnl=(None if position.symbol == "MU" else Decimal("100")),
            )
            for position in reversed(multi_market_mobile_report.positions)
        ),
    )

    html = DailyEmailReportRenderer().render(report).html
    holdings_section = html.split(">Holdings</h2>", 1)[1]
    desktop = holdings_section.split('<table class="pams-desktop-only"', 1)[1].split(
        "</table>", 1
    )[0]
    mobile = holdings_section.split('<div class="pams-mobile-only"', 1)[1].split(
        "</div>", 1
    )[0]
    expected = ("3293", "8299", "0050", "2027", "2330", "DRAM", "MU")

    assert tuple(sorted(expected, key=desktop.index)) == expected
    assert (
        tuple(sorted(expected, key=lambda symbol: mobile.index(f">{symbol}</strong>")))
        == expected
    )


def test_responsive_css_preserves_flow_and_avoids_overlap_constructs(
    multi_market_mobile_report: DailyEmailReport,
) -> None:
    html = DailyEmailReportRenderer().render(multi_market_mobile_report).html
    assert "@media only screen and (max-width:600px)" in html
    assert ".pams-desktop-only" in html
    assert ".pams-mobile-only" in html
    assert ".pams-responsive-table" in html
    assert "position:absolute" not in html.replace(" ", "").lower()
    assert "position:fixed" not in html.replace(" ", "").lower()
    assert re.search(r"(?<!line-)height:\s*\d+(?:px|em|rem)", html) is None
    assert re.search(r"margin(?:-\w+)?:\s*-", html) is None
    note = html.index("Ranked by absolute daily P/L")
    holdings_heading = html.index(">Holdings</h2>")
    assert note < holdings_heading
    assert "margin:10px 0 24px" in html[note - 150 : note]
