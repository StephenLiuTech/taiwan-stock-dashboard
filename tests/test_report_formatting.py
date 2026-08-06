"""Shared report currency formatting contracts."""

import re
from datetime import date
from decimal import Decimal

from domain import Currency
from pams.application.send_daily_report import DailyEmailReport
from pams.delivery.formatting import (
    format_native_currency_amount,
    format_percentage,
    format_twd_amount,
)
from pams.delivery.rendering import DailyEmailReportRenderer


def test_twd_amounts_are_whole_signed_decimal_values() -> None:
    assert format_twd_amount(Decimal("45165.00"), signed=True) == "+NT$45,165"
    assert format_twd_amount(Decimal("-8250.00"), signed=True) == "-NT$8,250"
    assert format_twd_amount(Decimal("0.00"), signed=True) == "NT$0"
    assert format_twd_amount(Decimal("2669330.00")) == "NT$2,669,330"


def test_native_usd_and_percentage_precision_is_preserved() -> None:
    assert (
        format_native_currency_amount(Decimal("1139.12"), Currency.USD) == "US$1,139.12"
    )
    assert format_native_currency_amount(Decimal("53.9599"), Currency.USD) == "US$53.96"
    assert format_percentage(Decimal("0.0107"), signed=True) == "+1.07%"
    assert format_percentage(Decimal("-0.0806"), signed=True) == "-8.06%"


def test_daily_email_contains_no_twd_dot_zero_zero_suffix() -> None:
    rendered = DailyEmailReportRenderer().render(
        DailyEmailReport(
            report_date=date(2026, 8, 5),
            verified_source_date=date(2026, 8, 5),
            total_market_value=Decimal("1200.00"),
            total_cost_basis=Decimal("1000.00"),
            total_unrealized_pnl=Decimal("200.00"),
            total_return=Decimal("0.20"),
            total_liabilities=Decimal("0.00"),
            net_asset_value=Decimal("1200.00"),
            liability_ratio=Decimal("0"),
            daily_profit_loss=Decimal("20.00"),
            daily_profit_loss_percentage=Decimal("0.02"),
            history=(),
            positions=(),
        )
    )
    assert re.search(r"NT\$[\d,]+\.00", rendered.html) is None
    assert re.search(r"NT\$[\d,]+\.00", rendered.plain_text) is None
    assert "NT$1,200" in rendered.html
    assert "NT$0" in rendered.html
    assert "+2.00%" in rendered.html
