"""Pure presentation formatting for the PAMS dashboard."""

from decimal import Decimal

from pams.application import PortfolioValuation

MISSING_VALUE = "—"


def format_twd(value: Decimal | None, *, signed: bool = False) -> str:
    """Format a TWD amount without inventing missing values."""
    if value is None:
        return MISSING_VALUE
    sign = "+" if signed and value > 0 else ""
    return f"{sign}NT$ {value:,.0f}"


def format_number(value: Decimal | None) -> str:
    """Format a quantity with thousands separators."""
    return MISSING_VALUE if value is None else f"{value:,.2f}"


def format_percentage(value: Decimal | None, *, signed: bool = False) -> str:
    """Format a decimal fraction as a percentage."""
    if value is None:
        return MISSING_VALUE
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:,.2f}%"


def kpi_view_model(valuation: PortfolioValuation) -> tuple[tuple[str, str], ...]:
    """Map application-provided KPI values to display strings."""
    return (
        ("Market Value", format_twd(valuation.total_market_value)),
        ("Cost", format_twd(valuation.total_cost)),
        ("Unrealized", format_twd(valuation.total_unrealized_pl, signed=True)),
        ("Return", format_percentage(valuation.total_return, signed=True)),
    )
