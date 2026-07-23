"""Pure table projections from application valuation DTOs."""

from pams.application import HoldingValuation, PortfolioValuation
from pams.dashboard.formatting import format_number, format_percentage, format_twd


def _ranked(
    valuation: PortfolioValuation, *, key: str, reverse: bool, limit: int | None
) -> tuple[HoldingValuation, ...]:
    rows = sorted(
        valuation.holdings, key=lambda holding: getattr(holding, key), reverse=reverse
    )
    return tuple(rows if limit is None else rows[:limit])


def largest_position_rows(valuation: PortfolioValuation) -> list[dict[str, str]]:
    """Return the ten largest positions using application-provided values."""
    return [
        {
            "Symbol": item.symbol,
            "Quantity": format_number(item.quantity),
            "Last Price": format_twd(item.last_price),
            "Market Value": format_twd(item.market_value),
            "Weight %": format_percentage(item.portfolio_weight),
        }
        for item in _ranked(valuation, key="market_value", reverse=True, limit=10)
    ]


def performance_rows(
    valuation: PortfolioValuation, *, winners: bool
) -> list[dict[str, str]]:
    """Return the five highest or lowest unrealized results."""
    return [
        {
            "Symbol": item.symbol,
            "Unrealized": format_twd(item.unrealized_pl, signed=True),
            "Return %": format_percentage(item.unrealized_return, signed=True),
        }
        for item in _ranked(valuation, key="unrealized_pl", reverse=winners, limit=5)
    ]


def holdings_table_rows(valuation: PortfolioValuation) -> list[dict[str, object]]:
    """Return every holding; Streamlit provides interactive column sorting."""
    return [
        {
            "Symbol": item.symbol,
            "Quantity": item.quantity,
            "Average Cost": item.average_cost,
            "Last Price": item.last_price,
            "Cost Basis": item.cost_basis,
            "Market Value": item.market_value,
            "Unrealized": item.unrealized_pl,
            "Return %": item.unrealized_return,
        }
        for item in valuation.holdings
    ]


def allocation_rows(valuation: PortfolioValuation) -> list[dict[str, object]]:
    """Return extensible holding allocation chart inputs from the DTO."""
    return [
        {
            "Holding": item.symbol,
            "Market Value": item.market_value,
            "Weight": item.portfolio_weight,
        }
        for item in _ranked(valuation, key="market_value", reverse=True, limit=None)
        if item.market_value > 0
    ]
