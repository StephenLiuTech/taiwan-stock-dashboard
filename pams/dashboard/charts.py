"""Charts built exclusively from application DTO data."""

import plotly.express as px
import plotly.graph_objects as go

from pams.application import PortfolioHistory, PortfolioValuation
from pams.dashboard.tables import allocation_rows


def allocation_chart(valuation: PortfolioValuation) -> go.Figure | None:
    """Build a readable holding allocation donut, or an empty result."""
    rows = allocation_rows(valuation)
    if not rows:
        return None
    return px.pie(
        rows,
        names="Holding",
        values="Market Value",
        hole=0.55,
        title="Allocation by Holding",
    )


def history_chart(history: PortfolioHistory) -> go.Figure | None:
    """Build aggregate portfolio history without deriving new values."""
    if not history.points:
        return None
    figure = go.Figure()
    series = (
        ("Market Value", [point.market_value for point in history.points]),
        ("Net Equity", [point.net_equity for point in history.points]),
        ("Total Liabilities", [point.total_liabilities for point in history.points]),
    )
    dates = [point.snapshot_date for point in history.points]
    for name, values in series:
        figure.add_scatter(x=dates, y=values, mode="lines+markers", name=name)
    figure.update_layout(title="Portfolio History", yaxis_title="TWD")
    return figure
