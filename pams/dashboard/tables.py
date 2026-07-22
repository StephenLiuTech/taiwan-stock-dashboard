"""Pure table transformations from application DTOs."""

from pams.application import PortfolioOverview
from pams.dashboard.formatting import (
    format_number,
    format_percentage,
    format_twd,
)


def holdings_table_rows(overview: PortfolioOverview) -> list[dict[str, str]]:
    """Return display-ready holdings sorted by persisted market value."""
    return [
        {
            "Symbol": item.symbol,
            "Name": item.name,
            "Shares": format_number(item.shares),
            "Average Cost": format_twd(item.average_cost),
            "Latest Price": format_twd(item.latest_price),
            "Market Value": format_twd(item.market_value),
            "Cost Basis": format_twd(item.cost_basis),
            "Unrealized P/L": format_twd(item.unrealized_pnl, signed=True),
            "Unrealized Return": format_percentage(item.unrealized_return, signed=True),
            "Portfolio Weight": format_percentage(item.portfolio_weight),
            "Quote Date": item.quote_date.isoformat(),
        }
        for item in sorted(
            overview.holdings, key=lambda holding: holding.market_value, reverse=True
        )
    ]


def allocation_rows(overview: PortfolioOverview) -> list[dict[str, object]]:
    """Return chart inputs without recalculating portfolio values."""
    return [
        {"Holding": f"{item.symbol} {item.name}", "Market Value": item.market_value}
        for item in sorted(
            overview.holdings, key=lambda holding: holding.market_value, reverse=True
        )
        if item.market_value > 0
    ]
