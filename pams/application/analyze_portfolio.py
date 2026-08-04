"""Portfolio snapshot analytics application workflow."""

from datetime import date
from typing import Protocol

from domain import DailySnapshot, PortfolioAnalytics, TransactionExpenseSummary
from pams.application.exceptions import (
    AnalyticsDataUnavailableError,
    AnalyticsProcessingError,
    AnalyticsRepositoryError,
    InvalidAnalyticsPeriodError,
)
from repositories.interfaces import SnapshotRepository, TransactionRepository
from services import AnalyticsEngine, AnalyticsError, TransactionEngine


class AnalyticsCalculator(Protocol):
    """Calculation boundary used by the analytics application workflow."""

    def analyze(
        self,
        snapshots: list[DailySnapshot],
        expenses: TransactionExpenseSummary | None = None,
    ) -> PortfolioAnalytics: ...


class AnalyzePortfolioUseCase:
    """Load a requested snapshot period and invoke the pure analytics engine."""

    def __init__(
        self,
        snapshots: SnapshotRepository,
        engine: AnalyticsCalculator | None = None,
        transactions: TransactionRepository | None = None,
        transaction_engine: TransactionEngine | None = None,
    ) -> None:
        self.snapshots = snapshots
        self.engine = engine or AnalyticsEngine()
        self.transactions = transactions
        self.transaction_engine = transaction_engine or TransactionEngine()

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
            transactions = (
                self.transactions.list_filtered(
                    start_date=period_start if start_date is not None else None,
                    end_date=period_end if end_date is not None else None,
                )
                if self.transactions is not None
                else None
            )
        except Exception as error:
            raise AnalyticsRepositoryError(
                "Unable to load portfolio snapshot history"
            ) from error

        if not snapshots:
            raise AnalyticsDataUnavailableError(
                "No portfolio snapshots are available for the requested period"
            )

        try:
            if transactions is None:
                return self.engine.analyze(snapshots)
            expenses = self.transaction_engine.summarize_expenses(transactions)
            return self.engine.analyze(snapshots, expenses)
        except AnalyticsError as error:
            raise AnalyticsDataUnavailableError(
                f"Portfolio snapshot history is invalid: {error}"
            ) from error
        except Exception as error:
            raise AnalyticsProcessingError(
                "Unable to analyze portfolio snapshot history"
            ) from error
