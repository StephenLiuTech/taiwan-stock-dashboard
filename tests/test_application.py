"""Unit tests for presentation-neutral application workflows."""

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from market_calendar import MarketAvailability
from pams.application import (
    MarketAvailabilitySummary,
    PortfolioHistoryUseCase,
    PortfolioStatusUseCase,
    UpdateMode,
    UpdatePortfolioUseCase,
    VerificationLevel,
    VerifySystemUseCase,
)
from pams.operations import (
    CheckLevel,
    OperationalStatus,
    VerificationCheck,
    VerificationReport,
)
from tests.test_cli import FakeEngine, sample_result


class CalendarStub:
    def __init__(self, twse: date, tpex: date) -> None:
        self.value = MarketAvailability(twse, tpex)
        self.calls = 0

    def market_availability(self) -> MarketAvailability:
        self.calls += 1
        return self.value


class StatusStub:
    def read(self, availability: MarketAvailability) -> OperationalStatus:
        return OperationalStatus(
            Path("pams.db"),
            date(2026, 7, 20),
            date(2026, 7, 20),
            date(2026, 7, 20),
            5,
            2,
            3,
            1024,
            availability.twse_date,
            availability.tpex_date,
            availability.commonly_ingestible_date,
        )


class VerificationStub:
    def run(self) -> VerificationReport:
        return VerificationReport(
            (
                VerificationCheck("Database", CheckLevel.PASS, "ready"),
                VerificationCheck("Calendar", CheckLevel.WARN, "waiting"),
            )
        )


def test_update_automatic_date_runs_engine_when_sources_match() -> None:
    calendar = CalendarStub(date(2026, 7, 22), date(2026, 7, 22))
    engine = FakeEngine()
    result = UpdatePortfolioUseCase(
        calendar, engine, Path("pams.db")  # type: ignore[arg-type]
    ).execute()
    assert result.mode is UpdateMode.UPDATED
    assert result.requested_date == date(2026, 7, 22)
    assert engine.refresh_called is True
    assert result.totals is not None
    assert result.totals.position_count == 2


def test_update_unsynchronized_returns_without_engine_call() -> None:
    calendar = CalendarStub(date(2026, 7, 21), date(2026, 7, 22))
    engine = FakeEngine()
    result = UpdatePortfolioUseCase(
        calendar, engine, Path("pams.db")  # type: ignore[arg-type]
    ).execute()
    assert result.mode is UpdateMode.SOURCES_UNSYNCHRONIZED
    assert result.requested_date is None
    assert engine.refresh_called is False
    assert engine.preview_called is False


def test_update_explicit_date_bypasses_calendar_and_honors_dry_run() -> None:
    calendar = CalendarStub(date(2026, 7, 21), date(2026, 7, 22))
    engine = FakeEngine()
    result = UpdatePortfolioUseCase(
        calendar, engine, Path("pams.db")  # type: ignore[arg-type]
    ).execute(date(2026, 7, 22), dry_run=True)
    assert result.mode is UpdateMode.DRY_RUN
    assert calendar.calls == 0
    assert engine.preview_called is True
    assert engine.refresh_called is False


def test_update_maps_engine_result_to_presentation_dto() -> None:
    engine = FakeEngine()
    engine.refresh = lambda trade_date: sample_result()  # type: ignore[method-assign]
    result = UpdatePortfolioUseCase(
        CalendarStub(date(2026, 7, 22), date(2026, 7, 22)),
        engine,  # type: ignore[arg-type]
        Path("pams.db"),
    ).execute(date(2026, 7, 22))
    assert [position.symbol for position in result.positions] == ["2330", "8299"]
    assert result.positions[0].name == "TSMC"


def test_portfolio_status_use_case_returns_summary_dto() -> None:
    summary = PortfolioStatusUseCase(
        CalendarStub(date(2026, 7, 21), date(2026, 7, 22)),  # type: ignore[arg-type]
        StatusStub(),  # type: ignore[arg-type]
    ).execute()
    assert summary.holdings_count == 5
    assert summary.market_availability.commonly_ingestible_date is None


def test_portfolio_history_use_case_maps_persisted_snapshots() -> None:
    class SnapshotsStub:
        def list_between_dates(self, start: date, end: date) -> list[object]:
            assert start == date.min
            assert end == date.max
            return [sample_result().snapshot]

    history = PortfolioHistoryUseCase(SnapshotsStub()).execute()  # type: ignore[arg-type]
    assert len(history.points) == 1
    assert history.points[0].snapshot_date == date(2026, 7, 22)
    assert history.points[0].market_value == Decimal("1200")


def test_verify_system_use_case_returns_items() -> None:
    report = VerifySystemUseCase(VerificationStub()).execute()  # type: ignore[arg-type]
    assert [item.level for item in report.items] == [
        VerificationLevel.PASS,
        VerificationLevel.WARN,
    ]
    assert report.failed is False


def test_application_dtos_are_immutable() -> None:
    availability = MarketAvailabilitySummary(
        date(2026, 7, 22), date(2026, 7, 22), date(2026, 7, 22)
    )
    with pytest.raises(FrozenInstanceError):
        availability.commonly_ingestible_date = None  # type: ignore[misc]
