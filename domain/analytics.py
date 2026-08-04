"""Immutable portfolio analytics results."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class DailyPortfolioReturn:
    """Percentage change between two consecutive portfolio snapshots."""

    period_start: date
    period_end: date
    starting_value: Decimal
    ending_value: Decimal
    daily_return: Decimal


@dataclass(frozen=True)
class PortfolioAnalytics:
    """Basic deterministic performance statistics for snapshot history."""

    start_date: date
    end_date: date
    starting_value: Decimal
    ending_value: Decimal
    absolute_profit_loss: Decimal
    total_return: Decimal
    daily_returns: tuple[DailyPortfolioReturn, ...]
    peak_value: Decimal
    trough_value: Decimal
    max_drawdown: Decimal
    snapshot_count: int
    total_buy_fees: Decimal = Decimal("0")
    total_sell_fees: Decimal = Decimal("0")
    total_taxes: Decimal = Decimal("0")
    total_trading_expenses: Decimal = Decimal("0")
    net_investment_return_before_expenses: Decimal = Decimal("0")
    net_investment_return_after_expenses: Decimal = Decimal("0")
