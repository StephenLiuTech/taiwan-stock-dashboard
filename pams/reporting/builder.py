"""Structured daily portfolio report construction."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pams.application import HoldingValuation, PortfolioValuation


@dataclass(frozen=True)
class PortfolioReportSummary:
    """Aggregate values copied from one portfolio valuation."""

    market_value: Decimal
    cost: Decimal
    unrealized_pl: Decimal
    total_return: Decimal


@dataclass(frozen=True)
class DailyReport:
    """Presentation-neutral data prepared for daily report renderers."""

    report_date: date | None
    portfolio_summary: PortfolioReportSummary
    largest_positions: tuple[HoldingValuation, ...]
    top_gainers: tuple[HoldingValuation, ...]
    top_losers: tuple[HoldingValuation, ...]
    portfolio: tuple[HoldingValuation, ...]


class DailyReportBuilder:
    """Select and order valuation data without formatting it."""

    def build(self, valuation: PortfolioValuation) -> DailyReport:
        """Build a deterministic report model from an immutable valuation."""
        by_market_value = tuple(
            sorted(
                valuation.holdings,
                key=lambda item: (-item.market_value, item.symbol),
            )
        )
        by_unrealized = tuple(
            sorted(
                valuation.holdings,
                key=lambda item: (-item.unrealized_pl, item.symbol),
            )
        )
        return DailyReport(
            report_date=valuation.valuation_date,
            portfolio_summary=PortfolioReportSummary(
                market_value=valuation.total_market_value,
                cost=valuation.total_cost,
                unrealized_pl=valuation.total_unrealized_pl,
                total_return=valuation.total_return,
            ),
            largest_positions=by_market_value[:10],
            top_gainers=by_unrealized[:5],
            top_losers=tuple(
                sorted(
                    valuation.holdings,
                    key=lambda item: (item.unrealized_pl, item.symbol),
                )
            )[:5],
            portfolio=tuple(sorted(valuation.holdings, key=lambda item: item.symbol)),
        )
