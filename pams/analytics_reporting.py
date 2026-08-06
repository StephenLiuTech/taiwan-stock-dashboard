"""CLI rendering for portfolio analytics DTOs."""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pams.application import (
    AnalyticsDataUnavailableError,
    AnalyticsProcessingError,
    AnalyticsRepositoryError,
    InvalidAnalyticsPeriodError,
    PortfolioAnalytics,
)
from pams.delivery.formatting import format_twd_amount


@dataclass(frozen=True)
class DailyReturnView:
    """One application-provided daily return prepared for presentation."""

    period: str
    period_end: date
    value: Decimal
    formatted_value: str


@dataclass(frozen=True)
class AnalyticsViewModel:
    """Display-only projection of immutable portfolio analytics."""

    period: str
    starting_value: str
    ending_value: str
    absolute_profit_loss: str
    total_return: str
    peak_value: str
    trough_value: str
    max_drawdown: str
    snapshot_count: str
    daily_returns: tuple[DailyReturnView, ...]


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _money(value: Decimal, *, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}NT${value:,.2f}"


def _percentage(value: Decimal, *, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:,.2f}%"


def analytics_view_model(
    analytics: PortfolioAnalytics, *, whole_twd: bool = False
) -> AnalyticsViewModel:
    """Format and reshape analytics without deriving financial results."""
    money = format_twd_amount if whole_twd else _money
    return AnalyticsViewModel(
        period=f"{analytics.start_date} to {analytics.end_date}",
        starting_value=money(analytics.starting_value),
        ending_value=money(analytics.ending_value),
        absolute_profit_loss=money(analytics.absolute_profit_loss, signed=True),
        total_return=_percentage(analytics.total_return, signed=True),
        peak_value=money(analytics.peak_value),
        trough_value=money(analytics.trough_value),
        max_drawdown=_percentage(analytics.max_drawdown),
        snapshot_count=str(analytics.snapshot_count),
        daily_returns=tuple(
            DailyReturnView(
                period=f"{item.period_start} to {item.period_end}",
                period_end=item.period_end,
                value=item.daily_return,
                formatted_value=_percentage(item.daily_return, signed=True),
            )
            for item in analytics.daily_returns
        ),
    )


def analytics_error_message(error: Exception) -> str:
    """Translate typed application failures into safe presentation text."""
    if isinstance(error, AnalyticsDataUnavailableError):
        return "Portfolio analytics are unavailable until snapshots exist."
    if isinstance(error, InvalidAnalyticsPeriodError):
        return "The analytics start date must not be after the end date."
    if isinstance(error, AnalyticsRepositoryError):
        return "Portfolio analytics could not be loaded."
    if isinstance(error, AnalyticsProcessingError):
        return "Portfolio analytics could not be processed."
    raise error


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
                "total_buy_fees": analytics.total_buy_fees,
                "total_sell_fees": analytics.total_sell_fees,
                "total_taxes": analytics.total_taxes,
                "total_trading_expenses": analytics.total_trading_expenses,
                "net_investment_return_before_expenses": (
                    analytics.net_investment_return_before_expenses
                ),
                "net_investment_return_after_expenses": (
                    analytics.net_investment_return_after_expenses
                ),
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
        f"Total Buy Fees: {_money(analytics.total_buy_fees)}",
        f"Total Sell Fees: {_money(analytics.total_sell_fees)}",
        f"Total Taxes: {_money(analytics.total_taxes)}",
        f"Total Trading Expenses: {_money(analytics.total_trading_expenses)}",
        "Net Investment Return (before expenses): "
        f"{_percentage(analytics.net_investment_return_before_expenses)}",
        "Net Investment Return (after expenses): "
        f"{_percentage(analytics.net_investment_return_after_expenses)}",
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
