"""Pure portfolio snapshot performance analytics."""

from decimal import Decimal

from domain import DailyPortfolioReturn, DailySnapshot, PortfolioAnalytics


class AnalyticsError(ValueError):
    """Base class for invalid analytics input."""


class EmptySnapshotHistoryError(AnalyticsError):
    """Raised when analytics are requested without any snapshots."""


class DuplicateSnapshotDateError(AnalyticsError):
    """Raised when more than one aggregate snapshot exists for a date."""


class AnalyticsEngine:
    """Calculate deterministic portfolio performance from aggregate snapshots."""

    def analyze(self, snapshots: list[DailySnapshot]) -> PortfolioAnalytics:
        """Return basic performance statistics in chronological order."""
        if not snapshots:
            raise EmptySnapshotHistoryError(
                "Portfolio analytics require at least one snapshot"
            )

        ordered = sorted(snapshots, key=lambda item: item.snapshot_date)
        dates = [item.snapshot_date for item in ordered]
        if len(dates) != len(set(dates)):
            raise DuplicateSnapshotDateError(
                "Portfolio analytics require one snapshot per date"
            )

        values = [item.net_asset_value for item in ordered]
        starting_value = values[0]
        ending_value = values[-1]
        absolute_profit_loss = ending_value - starting_value
        total_return = self._return(absolute_profit_loss, starting_value)

        daily_returns = tuple(
            DailyPortfolioReturn(
                period_start=previous.snapshot_date,
                period_end=current.snapshot_date,
                starting_value=previous.net_asset_value,
                ending_value=current.net_asset_value,
                daily_return=self._return(
                    current.net_asset_value - previous.net_asset_value,
                    previous.net_asset_value,
                ),
            )
            for previous, current in zip(ordered, ordered[1:], strict=False)
        )

        running_peak = values[0]
        max_drawdown = Decimal("0")
        for value in values:
            running_peak = max(running_peak, value)
            drawdown = (
                (value - running_peak) / running_peak
                if running_peak > 0
                else Decimal("0")
            )
            max_drawdown = min(max_drawdown, drawdown)

        return PortfolioAnalytics(
            start_date=ordered[0].snapshot_date,
            end_date=ordered[-1].snapshot_date,
            starting_value=starting_value,
            ending_value=ending_value,
            absolute_profit_loss=absolute_profit_loss,
            total_return=total_return,
            daily_returns=daily_returns,
            peak_value=max(values),
            trough_value=min(values),
            max_drawdown=max_drawdown,
            snapshot_count=len(ordered),
        )

    @staticmethod
    def _return(change: Decimal, starting_value: Decimal) -> Decimal:
        return change / starting_value if starting_value else Decimal("0")
