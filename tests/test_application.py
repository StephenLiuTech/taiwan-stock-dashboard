"""Unit tests for presentation-neutral application workflows."""

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from market_calendar import MarketAvailability
from market_data.exceptions import MarketDateUnavailableError
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


class SnapshotsStub:
    def __init__(self, existing_date: date | None = None) -> None:
        self.existing_date = existing_date
        self.queries: list[date] = []

    def get_by_date(self, snapshot_date: date) -> object | None:
        self.queries.append(snapshot_date)
        return object() if snapshot_date == self.existing_date else None


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


class EnrichmentEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.enrich_called = False

    def requires_enrichment(self, trade_date: date, **kwargs: object) -> bool:
        del trade_date, kwargs
        return True

    def enrich_existing(self, trade_date: date, **kwargs: object) -> object:
        del trade_date, kwargs
        self.enrich_called = True
        return sample_result()


class AnnualPnlWriterStub:
    def __init__(self) -> None:
        self.calls: list[tuple[date, object | None, bool]] = []

    def ensure(
        self,
        snapshot_date: date,
        *,
        unrealized_pnl: object | None = None,
        persist: bool = True,
        valuation_date: date | None = None,
    ) -> object:
        del valuation_date
        self.calls.append((snapshot_date, unrealized_pnl, persist))
        return object()


class FinancingInterestWriterStub:
    def __init__(self, order: list[str] | None = None) -> None:
        self.calls: list[tuple[date, bool]] = []
        self.order = order

    def ensure_through(self, end_date: date, *, persist: bool = True) -> object:
        self.calls.append((end_date, persist))
        if self.order is not None:
            self.order.append("financing")
        return object()


class ClosedMarketEngine(FakeEngine):
    def refresh(self, trade_date: date, **kwargs: object) -> object:
        del trade_date, kwargs
        raise MarketDateUnavailableError("confirmed closed")

    def rebuild(self, trade_date: date, **kwargs: object) -> object:
        return self.refresh(trade_date, **kwargs)

    def preview(self, trade_date: date, **kwargs: object) -> object:
        return self.refresh(trade_date, **kwargs)


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


def test_repeated_automatic_update_returns_no_op_without_refresh() -> None:
    selected_date = date(2026, 7, 22)
    calendar = CalendarStub(selected_date, selected_date)
    engine = FakeEngine()
    snapshots = SnapshotsStub(selected_date)
    result = UpdatePortfolioUseCase(
        calendar,  # type: ignore[arg-type]
        engine,  # type: ignore[arg-type]
        Path("pams.db"),
        snapshot_repository=snapshots,  # type: ignore[arg-type]
    ).execute()
    assert result.mode is UpdateMode.SNAPSHOT_EXISTS
    assert result.requested_date == selected_date
    assert snapshots.queries == [selected_date]
    assert engine.refresh_called is False
    assert engine.preview_called is False


def test_update_generates_daily_annual_pnl_snapshot() -> None:
    selected_date = date(2026, 7, 22)
    writer = AnnualPnlWriterStub()
    result = UpdatePortfolioUseCase(
        CalendarStub(selected_date, selected_date),  # type: ignore[arg-type]
        FakeEngine(),  # type: ignore[arg-type]
        Path("pams.db"),
        annual_pnl=writer,  # type: ignore[arg-type]
    ).execute()

    assert result.mode is UpdateMode.UPDATED
    assert writer.calls == [(selected_date, Decimal("220"), True)]


def test_update_ensures_financing_before_annual_pnl_even_for_existing_snapshot() -> (
    None
):
    selected_date = date(2026, 8, 29)
    order: list[str] = []
    financing = FinancingInterestWriterStub(order)

    class OrderedAnnual(AnnualPnlWriterStub):
        def ensure(self, *args: object, **kwargs: object) -> object:
            order.append("annual")
            return super().ensure(*args, **kwargs)  # type: ignore[arg-type]

    result = UpdatePortfolioUseCase(
        CalendarStub(selected_date, selected_date),  # type: ignore[arg-type]
        FakeEngine(),  # type: ignore[arg-type]
        Path("pams.db"),
        snapshot_repository=SnapshotsStub(selected_date),  # type: ignore[arg-type]
        annual_pnl=OrderedAnnual(),  # type: ignore[arg-type]
        financing_interest=financing,  # type: ignore[arg-type]
    ).execute()

    assert result.mode is UpdateMode.SNAPSHOT_EXISTS
    assert financing.calls == [(selected_date, True)]
    assert order == ["financing", "annual"]


def test_confirmed_non_trading_day_runs_accounting_only_without_market_snapshot() -> (
    None
):
    accounting_date = date(2026, 8, 29)
    financing = FinancingInterestWriterStub()
    annual = AnnualPnlWriterStub()
    result = UpdatePortfolioUseCase(
        CalendarStub(date(2026, 8, 28), date(2026, 8, 28)),  # type: ignore[arg-type]
        ClosedMarketEngine(),  # type: ignore[arg-type]
        Path("pams.db"),
        historical_engine_factory=lambda _: ClosedMarketEngine(),  # type: ignore[arg-type]
        annual_pnl=annual,  # type: ignore[arg-type]
        financing_interest=financing,  # type: ignore[arg-type]
        is_non_trading_day=lambda _: True,
    ).execute(accounting_date)

    assert result.mode is UpdateMode.ACCOUNTING_UPDATED
    assert financing.calls == [(accounting_date, True)]
    assert annual.calls == [(accounting_date, None, True)]


def test_trading_day_missing_data_remains_a_failure() -> None:
    requested = date(2026, 8, 31)
    use_case = UpdatePortfolioUseCase(
        CalendarStub(date(2026, 8, 28), date(2026, 8, 28)),  # type: ignore[arg-type]
        ClosedMarketEngine(),  # type: ignore[arg-type]
        Path("pams.db"),
        historical_engine_factory=lambda _: ClosedMarketEngine(),  # type: ignore[arg-type]
        annual_pnl=AnnualPnlWriterStub(),  # type: ignore[arg-type]
        financing_interest=FinancingInterestWriterStub(),  # type: ignore[arg-type]
        is_non_trading_day=lambda _: False,
    )

    with pytest.raises(MarketDateUnavailableError):
        use_case.execute(requested)


def test_existing_snapshot_routes_incomplete_global_data_to_enrichment() -> None:
    selected_date = date(2026, 7, 22)
    engine = EnrichmentEngine()
    result = UpdatePortfolioUseCase(
        CalendarStub(selected_date, selected_date),  # type: ignore[arg-type]
        engine,  # type: ignore[arg-type]
        Path("pams.db"),
        snapshot_repository=SnapshotsStub(selected_date),  # type: ignore[arg-type]
    ).execute()
    assert result.mode is UpdateMode.ENRICHED
    assert engine.enrich_called is True
    assert engine.refresh_called is False


def test_repeated_automatic_update_force_rebuilds_existing_snapshot() -> None:
    selected_date = date(2026, 7, 22)
    engine = FakeEngine()
    result = UpdatePortfolioUseCase(
        CalendarStub(selected_date, selected_date),  # type: ignore[arg-type]
        engine,  # type: ignore[arg-type]
        Path("pams.db"),
        snapshot_repository=SnapshotsStub(selected_date),  # type: ignore[arg-type]
    ).execute(force=True)

    assert result.mode is UpdateMode.UPDATED
    assert result.requested_date == selected_date
    assert engine.rebuild_called is True
    assert engine.refresh_called is False


def test_repeated_explicit_update_is_idempotent() -> None:
    selected_date = date(2026, 7, 22)
    calendar = CalendarStub(selected_date, selected_date)
    engine = FakeEngine()
    result = UpdatePortfolioUseCase(
        calendar,  # type: ignore[arg-type]
        engine,  # type: ignore[arg-type]
        Path("pams.db"),
        lambda _: engine,  # type: ignore[arg-type]
        SnapshotsStub(selected_date),  # type: ignore[arg-type]
    ).execute(selected_date)
    assert result.mode is UpdateMode.SNAPSHOT_EXISTS
    assert calendar.calls == 0
    assert engine.refresh_called is False
    assert engine.preview_called is False


@pytest.mark.parametrize(
    ("twse_date", "tpex_date", "expected_date"),
    [
        (date(2026, 7, 21), date(2026, 7, 22), date(2026, 7, 21)),
        (date(2026, 7, 23), date(2026, 7, 22), date(2026, 7, 22)),
    ],
)
def test_update_automatic_uses_historical_engine_for_joint_date(
    twse_date: date, tpex_date: date, expected_date: date
) -> None:
    calendar = CalendarStub(twse_date, tpex_date)
    latest_engine = FakeEngine()
    historical_engine = FakeEngine()
    selected_dates = []
    result = UpdatePortfolioUseCase(
        calendar,  # type: ignore[arg-type]
        latest_engine,  # type: ignore[arg-type]
        Path("pams.db"),
        lambda selected: selected_dates.append(selected) or historical_engine,  # type: ignore[arg-type]
    ).execute()
    assert result.mode is UpdateMode.UPDATED
    assert result.requested_date == expected_date
    assert selected_dates == [expected_date]
    assert historical_engine.refresh_called is True
    assert latest_engine.refresh_called is False


def test_update_automatic_uses_date_bound_engine_for_live_resolved_date() -> None:
    selected_date = date(2026, 7, 27)
    latest_engine = FakeEngine()
    historical_engine = FakeEngine()
    selected_dates: list[date] = []
    result = UpdatePortfolioUseCase(
        CalendarStub(selected_date, selected_date),  # type: ignore[arg-type]
        latest_engine,  # type: ignore[arg-type]
        Path("pams.db"),
        lambda selected: selected_dates.append(selected) or historical_engine,  # type: ignore[arg-type]
        prefer_historical_for_automatic=True,
    ).execute()

    assert result.requested_date == selected_date
    assert selected_dates == [selected_date]
    assert historical_engine.refresh_called is True
    assert latest_engine.refresh_called is False


def test_update_explicit_date_bypasses_calendar_and_honors_dry_run() -> None:
    calendar = CalendarStub(date(2026, 7, 21), date(2026, 7, 22))
    engine = FakeEngine()
    result = UpdatePortfolioUseCase(
        calendar,
        engine,
        Path("pams.db"),
        lambda _: engine,  # type: ignore[arg-type]
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
        lambda _: engine,  # type: ignore[arg-type]
    ).execute(date(2026, 7, 22))
    assert [position.symbol for position in result.positions] == ["2330", "8299"]
    assert result.positions[0].name == "TSMC"


def test_portfolio_status_use_case_returns_summary_dto() -> None:
    summary = PortfolioStatusUseCase(
        CalendarStub(date(2026, 7, 21), date(2026, 7, 22)),  # type: ignore[arg-type]
        StatusStub(),  # type: ignore[arg-type]
    ).execute()
    assert summary.holdings_count == 5
    assert summary.market_availability.commonly_ingestible_date == date(2026, 7, 21)
    assert summary.market_availability.synchronized is False


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


@pytest.mark.parametrize(
    "check_name",
    ["TWSE endpoint", "TPEx endpoint", "Market Calendar"],
)
def test_verify_system_can_downgrade_only_market_source_failures(
    check_name: str,
) -> None:
    class FailedCheckStub:
        def run(self) -> VerificationReport:
            return VerificationReport(
                (
                    VerificationCheck(
                        check_name, CheckLevel.FAIL, "temporarily unavailable"
                    ),
                )
            )

    use_case = VerifySystemUseCase(FailedCheckStub())  # type: ignore[arg-type]

    strict_report = use_case.execute()
    relaxed_report = use_case.execute(allow_market_source_warning=True)

    assert strict_report.items[0].level is VerificationLevel.FAIL
    assert strict_report.failed is True
    assert relaxed_report.items[0].level is VerificationLevel.WARN
    assert relaxed_report.failed is False


@pytest.mark.parametrize(
    "check_name",
    [
        "Configuration",
        "Database",
        "Schema",
        "Holdings",
        "Liabilities",
        "Market Data Engine",
    ],
)
def test_verify_system_relaxed_policy_keeps_local_failures_fatal(
    check_name: str,
) -> None:
    class FailedCheckStub:
        def run(self) -> VerificationReport:
            return VerificationReport(
                (VerificationCheck(check_name, CheckLevel.FAIL, "not ready"),)
            )

    report = VerifySystemUseCase(FailedCheckStub()).execute(  # type: ignore[arg-type]
        allow_market_source_warning=True
    )

    assert report.items[0].level is VerificationLevel.FAIL
    assert report.failed is True


def test_application_dtos_are_immutable() -> None:
    availability = MarketAvailabilitySummary(
        date(2026, 7, 22), date(2026, 7, 22), date(2026, 7, 22)
    )
    with pytest.raises(FrozenInstanceError):
        availability.commonly_ingestible_date = None  # type: ignore[misc]
