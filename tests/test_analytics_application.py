"""Analytics application workflow tests."""

from datetime import date
from decimal import Decimal

import pytest

from domain import DailySnapshot, PortfolioAnalytics
from pams.application import (
    AnalyticsDataUnavailableError,
    AnalyticsRepositoryError,
    AnalyzePortfolioUseCase,
    InvalidAnalyticsPeriodError,
)
from services import DuplicateSnapshotDateError


def snapshot(snapshot_date: date, value: str) -> DailySnapshot:
    portfolio_value = Decimal(value)
    return DailySnapshot(
        snapshot_date=snapshot_date,
        total_market_value=portfolio_value,
        total_cost_basis=Decimal("0"),
        total_unrealized_pnl=portfolio_value,
        total_liabilities=Decimal("0"),
        net_asset_value=portfolio_value,
        leverage_ratio=Decimal("0"),
        high_water_mark=portfolio_value,
        drawdown=Decimal("0"),
    )


class SnapshotRepositoryStub:
    def __init__(
        self,
        values: list[DailySnapshot],
        *,
        error: Exception | None = None,
    ) -> None:
        self.values = values
        self.error = error
        self.requested_period: tuple[date, date] | None = None

    def list_between_dates(self, start: date, end: date) -> list[DailySnapshot]:
        self.requested_period = (start, end)
        if self.error:
            raise self.error
        return self.values


class AnalyticsEngineStub:
    def __init__(
        self,
        result: PortfolioAnalytics | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.received: list[DailySnapshot] | None = None

    def analyze(self, snapshots: list[DailySnapshot]) -> PortfolioAnalytics:
        self.received = snapshots
        if self.error:
            raise self.error
        assert self.result is not None
        return self.result


def analytics_result() -> PortfolioAnalytics:
    return PortfolioAnalytics(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        starting_value=Decimal("100"),
        ending_value=Decimal("110"),
        absolute_profit_loss=Decimal("10"),
        total_return=Decimal("0.1"),
        daily_returns=(),
        peak_value=Decimal("110"),
        trough_value=Decimal("100"),
        max_drawdown=Decimal("0"),
        snapshot_count=2,
    )


def test_repository_is_queried_with_requested_period() -> None:
    values = [snapshot(date(2026, 1, 1), "100")]
    repository = SnapshotRepositoryStub(values)
    engine = AnalyticsEngineStub(analytics_result())
    result = AnalyzePortfolioUseCase(repository, engine).execute(  # type: ignore[arg-type]
        date(2026, 1, 1), date(2026, 1, 31)
    )
    assert repository.requested_period == (date(2026, 1, 1), date(2026, 1, 31))
    assert engine.received is values
    assert result == analytics_result()


def test_open_period_uses_minimum_and_maximum_dates() -> None:
    repository = SnapshotRepositoryStub([snapshot(date(2026, 1, 1), "100")])
    AnalyzePortfolioUseCase(repository).execute()  # type: ignore[arg-type]
    assert repository.requested_period == (date.min, date.max)


def test_invalid_period_is_rejected_before_repository_access() -> None:
    repository = SnapshotRepositoryStub([])
    with pytest.raises(InvalidAnalyticsPeriodError):
        AnalyzePortfolioUseCase(repository).execute(  # type: ignore[arg-type]
            date(2026, 2, 1), date(2026, 1, 1)
        )
    assert repository.requested_period is None


def test_empty_result_is_translated_to_application_error() -> None:
    with pytest.raises(AnalyticsDataUnavailableError, match="No portfolio snapshots"):
        AnalyzePortfolioUseCase(SnapshotRepositoryStub([])).execute()  # type: ignore[arg-type]


def test_repository_failure_is_translated_without_leaking_error() -> None:
    with pytest.raises(AnalyticsRepositoryError, match="Unable to load") as captured:
        AnalyzePortfolioUseCase(  # type: ignore[arg-type]
            SnapshotRepositoryStub([], error=RuntimeError("driver detail"))
        ).execute()
    assert isinstance(captured.value.__cause__, RuntimeError)
    assert "driver detail" not in str(captured.value)


def test_engine_input_error_is_translated_at_application_boundary() -> None:
    repository = SnapshotRepositoryStub([snapshot(date(2026, 1, 1), "100")])
    engine = AnalyticsEngineStub(error=DuplicateSnapshotDateError("duplicate"))
    with pytest.raises(AnalyticsDataUnavailableError, match="history is invalid"):
        AnalyzePortfolioUseCase(repository, engine).execute()  # type: ignore[arg-type]
