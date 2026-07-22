"""Daily portfolio snapshot application service."""

from decimal import Decimal

from domain import DailySnapshot, PortfolioSummary
from repositories.interfaces import SnapshotRepository


class DuplicateSnapshotError(ValueError):
    """Raised when a snapshot already exists for a date."""


class SnapshotService:
    """Calculate and persist daily portfolio history."""

    def __init__(self, repository: SnapshotRepository) -> None:
        self.repository = repository

    def create(self, summary: PortfolioSummary) -> DailySnapshot:
        """Create one snapshot while preserving the historical high-water mark."""
        if self.repository.get_by_date(summary.valuation_date):
            raise DuplicateSnapshotError(
                f"Snapshot already exists for {summary.valuation_date.isoformat()}"
            )

        highest = self.repository.get_highest()
        previous_high = highest.high_water_mark if highest else summary.net_asset_value
        high_water_mark = max(previous_high, summary.net_asset_value)
        drawdown = (
            (summary.net_asset_value - high_water_mark) / high_water_mark
            if high_water_mark > 0
            else Decimal("0")
        )
        snapshot = DailySnapshot(
            snapshot_date=summary.valuation_date,
            total_market_value=summary.total_market_value,
            total_cost_basis=summary.total_cost_basis,
            total_unrealized_pnl=summary.total_unrealized_pnl,
            total_liabilities=summary.total_liabilities,
            net_asset_value=summary.net_asset_value,
            leverage_ratio=summary.leverage_ratio,
            high_water_mark=high_water_mark,
            drawdown=drawdown,
        )
        self.repository.add(snapshot)
        return snapshot
