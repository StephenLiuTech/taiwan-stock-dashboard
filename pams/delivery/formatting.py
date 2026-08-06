"""Shared presentation-boundary formatting for report financial values."""

from decimal import Decimal

from domain import Currency


def format_twd_amount(value: Decimal | None, *, signed: bool = False) -> str:
    """Render TWD monetary amounts as whole NT dollars without using float."""
    if value is None:
        return "N/A"
    if signed and value > 0:
        return f"+NT${value:,.0f}"
    if signed and value < 0:
        return f"-NT${abs(value):,.0f}"
    return f"NT${value:,.0f}"


def format_compact_decimal(value: Decimal | None) -> str:
    """Render a native price with at most two decimal places."""
    return "N/A" if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


def format_native_currency_amount(value: Decimal | None, currency: Currency) -> str:
    """Preserve native price precision while selecting its currency symbol."""
    if value is None:
        return "N/A"
    prefix = "US$" if currency is Currency.USD else "NT$"
    return f"{prefix}{format_compact_decimal(value)}"


def format_percentage(value: Decimal | None, *, signed: bool = False) -> str:
    """Render a decimal ratio as a two-decimal percentage."""
    if value is None:
        return "N/A"
    prefix = "+" if signed and value > 0 else ""
    return f"{prefix}{value * 100:,.2f}%"
