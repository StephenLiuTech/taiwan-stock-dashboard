"""Portfolio snapshot analytics application workflow."""

from datetime import date
from typing import Protocol

from domain import DailySnapshot, PortfolioAnalytics
from pams.application.exceptions import (
    AnalyticsDataUnavailableError,
    AnalyticsProcessingError,
    AnalyticsRepositoryError,
    InvalidAnalyticsPeriodError,
)
from repositories.interfaces import SnapshotRepository
from services import AnalyticsEngine, AnalyticsError


class AnalyticsCalculator(Protocol):
    """Calculation boundary used by the analytics application workflow."""

    def analyze(self, snapshots: list[DailySnapshot]) -> PortfolioAnalytics: ...


class AnalyzePortfolioUseCase:
    """Load a requested snapshot period and invoke the pure analytics engine."""

    def __init__(
        self,
        snapshots: SnapshotRepository,
        engine: AnalyticsCalculator | None = None,
    ) -> None:
        self.snapshots = snapshots
        self.engine = engine or AnalyticsEngine()

    def execute(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> PortfolioAnalytics:
        """Return analytics for an inclusive date period."""
        period_start = start_date or date.min
        period_end = end_date or date.max
        if period_start > period_end:
            raise InvalidAnalyticsPeriodError(
                "Analytics start date must not be after end date"
            )

        try:
            snapshots = self.snapshots.list_between_dates(period_start, period_end)
        except Exception as error:
            raise AnalyticsRepositoryError(
                "Unable to load portfolio snapshot history"
            ) from error

        if not snapshots:
            raise AnalyticsDataUnavailableError(
                "No portfolio snapshots are available for the requested period"
            )

        try:
            return self.engine.analyze(snapshots)
        except AnalyticsError as error:
            raise AnalyticsDataUnavailableError(
                f"Portfolio snapshot history is invalid: {error}"
            ) from error
        except Exception as error:
            raise AnalyticsProcessingError(
                "Unable to analyze portfolio snapshot history"
            ) from error
