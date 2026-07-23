"""CLI rendering for portfolio analytics DTOs."""

import json
from datetime import date
from decimal import Decimal

from pams.application import PortfolioAnalytics


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _money(value: Decimal) -> str:
    return f"NT${value:,.2f}"


def _percentage(value: Decimal) -> str:
    return f"{value * 100:,.2f}%"


def format_portfolio_analytics(
    analytics: PortfolioAnalytics, *, json_output: bool
) -> str:
    """Render analytics without performing business calculations."""
    if json_output:
        return json.dumps(
            {
                "start_date": analytics.start_date,
                "end_date": analytics.end_date,
                "starting_value": analytics.starting_value,
                "ending_value": analytics.ending_value,
                "absolute_profit_loss": analytics.absolute_profit_loss,
                "total_return": analytics.total_return,
                "daily_returns": [
                    {
                        "period_start": item.period_start,
                        "period_end": item.period_end,
                        "starting_value": item.starting_value,
                        "ending_value": item.ending_value,
                        "daily_return": item.daily_return,
                    }
                    for item in analytics.daily_returns
                ],
                "peak_value": analytics.peak_value,
                "trough_value": analytics.trough_value,
                "max_drawdown": analytics.max_drawdown,
                "snapshot_count": analytics.snapshot_count,
            },
            default=_json_default,
            ensure_ascii=True,
            sort_keys=True,
        )

    lines = [
        "PAMS Portfolio Analytics",
        f"Period: {analytics.start_date} to {analytics.end_date}",
        f"Snapshots: {analytics.snapshot_count}",
        f"Starting Value: {_money(analytics.starting_value)}",
        f"Ending Value: {_money(analytics.ending_value)}",
        f"Absolute P/L: {_money(analytics.absolute_profit_loss)}",
        f"Total Return: {_percentage(analytics.total_return)}",
        f"Peak Value: {_money(analytics.peak_value)}",
        f"Trough Value: {_money(analytics.trough_value)}",
        f"Maximum Drawdown: {_percentage(analytics.max_drawdown)}",
        "",
        "Daily Returns:",
    ]
    lines.extend(
        f"  {item.period_start} to {item.period_end}: "
        f"{_percentage(item.daily_return)}"
        for item in analytics.daily_returns
    )
    if not analytics.daily_returns:
        lines.append("  none")
    return "\n".join(lines)
