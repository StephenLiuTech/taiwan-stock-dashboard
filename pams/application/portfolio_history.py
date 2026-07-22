"""Persisted portfolio history application workflow."""

from datetime import date

from pams.application.dto import PortfolioHistory, PortfolioHistoryPoint
from repositories.interfaces import SnapshotRepository


class PortfolioHistoryUseCase:
    """Read aggregate daily snapshots as immutable application DTOs."""

    def __init__(self, snapshots: SnapshotRepository) -> None:
        self.snapshots = snapshots

    def execute(self) -> PortfolioHistory:
        """Return all persisted history in chronological order."""
        snapshots = self.snapshots.list_between_dates(date.min, date.max)
        return PortfolioHistory(
            tuple(
                PortfolioHistoryPoint(
                    snapshot_date=item.snapshot_date,
                    market_value=item.total_market_value,
                    net_equity=item.net_asset_value,
                    total_liabilities=item.total_liabilities,
                )
                for item in snapshots
            )
        )
