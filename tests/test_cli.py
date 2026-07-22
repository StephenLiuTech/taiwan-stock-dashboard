"""Offline tests for CLI routing, reporting, and exit policies."""

import json
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import pams.cli
from domain import Currency, DailySnapshot, Holding, Market, PriceQuote
from market_calendar import MarketAvailability
from market_data import (
    ProviderDataError,
    SourceDateError,
    SourceDateMismatchError,
    SuspendedSecurityError,
    SymbolNotFoundError,
)
from market_data.engine import MarketDataRefreshResult
from pams.application import (
    PortfolioStatusUseCase,
    UpdatePortfolioUseCase,
    VerifySystemUseCase,
)
from pams.cli import ExitCode, _error_exit_code, main
from pams.composition import ApplicationContext
from pams.operations import (
    CheckLevel,
    OperationalStatus,
    VerificationCheck,
    VerificationReport,
)
from pams.reporting import (
    format_decimal,
    format_human_report,
    format_json_report,
    format_percentage,
)
from services import DuplicateSnapshotError, PortfolioService


def sample_result() -> MarketDataRefreshResult:
    """Build a deterministic two-position report result."""
    holdings = (
        Holding(
            id="h2",
            symbol="8299",
            name="Phison",
            market=Market.TPEX,
            currency=Currency.TWD,
            quantity=Decimal("2"),
            average_cost=Decimal("90"),
        ),
        Holding(
            id="h1",
            symbol="2330",
            name="TSMC",
            market=Market.TWSE,
            currency=Currency.TWD,
            quantity=Decimal("10"),
            average_cost=Decimal("80"),
        ),
    )
    quotes = (
        PriceQuote(
            symbol="8299",
            market=Market.TPEX,
            trade_date=date(2026, 7, 22),
            close_price=Decimal("100"),
            previous_close=None,
            currency=Currency.TWD,
            source="test",
        ),
        PriceQuote(
            symbol="2330",
            market=Market.TWSE,
            trade_date=date(2026, 7, 22),
            close_price=Decimal("100"),
            previous_close=Decimal("90"),
            currency=Currency.TWD,
            source="test",
        ),
    )
    summary = PortfolioService().value_portfolio(
        list(holdings), list(quotes), [], date(2026, 7, 22)
    )
    snapshot = DailySnapshot(
        snapshot_date=date(2026, 7, 22),
        total_market_value=summary.total_market_value,
        total_cost_basis=summary.total_cost_basis,
        total_unrealized_pnl=summary.total_unrealized_pnl,
        total_liabilities=summary.total_liabilities,
        net_asset_value=summary.net_asset_value,
        leverage_ratio=summary.leverage_ratio,
        high_water_mark=summary.net_asset_value,
        drawdown=Decimal("0"),
    )
    return MarketDataRefreshResult(
        quotes, holdings, summary, snapshot, date(2026, 7, 22)
    )


class FakeEngine:
    """Controllable engine used to isolate CLI behavior."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.preview_called = False
        self.refresh_called = False

    def preview(self, trade_date: date) -> MarketDataRefreshResult:
        self.preview_called = True
        if self.error:
            raise self.error
        return sample_result()

    def refresh(self, trade_date: date) -> MarketDataRefreshResult:
        self.refresh_called = True
        if self.error:
            raise self.error
        return sample_result()


class FakeCalendar:
    """Deterministic automatic-date resolver."""

    def __init__(
        self,
        twse_date: date = date(2026, 7, 22),
        tpex_date: date = date(2026, 7, 22),
    ) -> None:
        self.availability = MarketAvailability(twse_date, tpex_date)

    def market_availability(self) -> MarketAvailability:
        return self.availability


class FakeStatusService:
    def read(self, availability: MarketAvailability) -> OperationalStatus:
        return OperationalStatus(
            Path("configured.db"),
            None,
            None,
            None,
            5,
            2,
            3,
            4096,
            availability.twse_date,
            availability.tpex_date,
            availability.commonly_ingestible_date,
        )


class FakeVerificationService:
    def __init__(self, *, failed: bool = False) -> None:
        self.failed = failed

    def run(self) -> VerificationReport:
        return VerificationReport(
            (
                VerificationCheck(
                    "test",
                    CheckLevel.FAIL if self.failed else CheckLevel.PASS,
                    "offline",
                ),
            )
        )


def install_fake_composition(
    monkeypatch: pytest.MonkeyPatch,
    engine: FakeEngine,
    captured: dict[str, object] | None = None,
    calendar: FakeCalendar | None = None,
) -> None:
    @contextmanager
    def fake_compose(
        database_override: Path | None = None,
        *,
        verbose: bool = False,
        providers: object = None,
    ) -> Iterator[ApplicationContext]:
        del providers
        if captured is not None:
            captured["database"] = database_override
            captured["verbose"] = verbose
        connection = sqlite3.connect(":memory:")
        try:
            yield ApplicationContext(
                connection=connection,
                engine=engine,  # type: ignore[arg-type]
                database_path=database_override or Path("configured.db"),
                seeded=False,
                calendar=calendar or FakeCalendar(),  # type: ignore[arg-type]
                status=FakeStatusService(),  # type: ignore[arg-type]
                verification=FakeVerificationService(),  # type: ignore[arg-type]
                update_portfolio=UpdatePortfolioUseCase(
                    calendar or FakeCalendar(),  # type: ignore[arg-type]
                    engine,  # type: ignore[arg-type]
                    database_override or Path("configured.db"),
                ),
                portfolio_status=PortfolioStatusUseCase(
                    calendar or FakeCalendar(),  # type: ignore[arg-type]
                    FakeStatusService(),  # type: ignore[arg-type]
                ),
                verify_system=VerifySystemUseCase(
                    FakeVerificationService()  # type: ignore[arg-type]
                ),
            )
        finally:
            connection.close()

    monkeypatch.setattr(pams.cli, "compose_application", fake_compose)
    monkeypatch.setattr(pams.cli, "compose_operations", fake_compose)


def test_module_entry_point_help_works() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "pams", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "update" in completed.stdout


def test_valid_update_routes_to_refresh(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = FakeEngine()
    install_fake_composition(monkeypatch, engine)
    assert main(["update", "--date", "2026-07-22"]) == ExitCode.SUCCESS
    assert engine.refresh_called is True
    assert "PAMS Market Data Update" in capsys.readouterr().out


def test_invalid_date_returns_argparse_exit_two() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["update", "--date", "2026/07/22"])
    assert raised.value.code == ExitCode.CLI_ERROR


def test_update_without_date_uses_market_calendar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    install_fake_composition(monkeypatch, engine)
    assert main(["update", "--dry-run"]) == 0
    assert engine.preview_called is True


@pytest.mark.parametrize(
    ("twse_date", "tpex_date"),
    [
        (date(2026, 7, 21), date(2026, 7, 22)),
        (date(2026, 7, 22), date(2026, 7, 21)),
    ],
)
def test_unsynchronized_automatic_update_is_successful_no_op(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    twse_date: date,
    tpex_date: date,
) -> None:
    engine = FakeEngine()
    install_fake_composition(
        monkeypatch, engine, calendar=FakeCalendar(twse_date, tpex_date)
    )
    assert main(["update"]) == ExitCode.SUCCESS
    output = capsys.readouterr().out
    assert "no update performed" in output
    assert f"TWSE latest source date: {twse_date}" in output
    assert f"TPEx latest source date: {tpex_date}" in output
    assert engine.preview_called is False
    assert engine.refresh_called is False


def test_unsynchronized_automatic_update_json_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = FakeEngine()
    install_fake_composition(
        monkeypatch,
        engine,
        calendar=FakeCalendar(date(2026, 7, 21), date(2026, 7, 22)),
    )
    assert main(["update", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "no_update_sources_unsynchronized"
    assert payload["commonly_ingestible_date"] is None
    assert engine.refresh_called is False


def test_manual_requested_date_mismatch_remains_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = SourceDateMismatchError(Market.TWSE, date(2026, 7, 22), date(2026, 7, 21))
    engine = FakeEngine(error)
    install_fake_composition(
        monkeypatch,
        engine,
        calendar=FakeCalendar(date(2026, 7, 21), date(2026, 7, 22)),
    )
    assert main(["update", "--date", "2026-07-22"]) == ExitCode.SOURCE_DATE_ERROR
    assert engine.refresh_called is True


def test_json_output_is_machine_readable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install_fake_composition(monkeypatch, FakeEngine())
    assert main(["update", "--date", "2026-07-22", "--json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["requested_date"] == "2026-07-22"
    assert payload["totals"]["total_market_value"] == "1200"


def test_database_override_and_verbose_are_forwarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    install_fake_composition(monkeypatch, FakeEngine(), captured)
    database = tmp_path / "override.db"
    assert (
        main(
            [
                "update",
                "--date",
                "2026-07-22",
                "--database",
                str(database),
                "--verbose",
            ]
        )
        == 0
    )
    assert captured == {"database": database, "verbose": True}


def test_dry_run_routes_to_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine()
    install_fake_composition(monkeypatch, engine)
    assert main(["update", "--date", "2026-07-22", "--dry-run"]) == 0
    assert engine.preview_called is True
    assert engine.refresh_called is False


def test_status_command_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install_fake_composition(monkeypatch, FakeEngine())
    assert main(["status"]) == 0
    output = capsys.readouterr().out
    assert "PAMS Operational Status" in output
    assert "Holdings count: 5" in output
    assert "TWSE latest source date: 2026-07-22" in output
    assert "TPEx latest source date: 2026-07-22" in output
    assert "Commonly ingestible dataset: 2026-07-22" in output


def test_status_exposes_unsynchronized_source_dates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install_fake_composition(
        monkeypatch,
        FakeEngine(),
        calendar=FakeCalendar(date(2026, 7, 21), date(2026, 7, 22)),
    )
    assert main(["status"]) == 0
    output = capsys.readouterr().out
    assert "TWSE latest source date: 2026-07-21" in output
    assert "TPEx latest source date: 2026-07-22" in output
    assert "Commonly ingestible dataset: not currently available" in output


def test_verify_failed_returns_exit_eight(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    @contextmanager
    def failed_compose(
        database_override: Path | None = None,
        *,
        verbose: bool = False,
    ) -> Iterator[ApplicationContext]:
        del database_override, verbose
        connection = sqlite3.connect(":memory:")
        try:
            yield ApplicationContext(
                connection=connection,
                engine=FakeEngine(),  # type: ignore[arg-type]
                database_path=Path("configured.db"),
                seeded=False,
                calendar=FakeCalendar(),  # type: ignore[arg-type]
                status=FakeStatusService(),  # type: ignore[arg-type]
                verification=FakeVerificationService(failed=True),  # type: ignore[arg-type]
                update_portfolio=UpdatePortfolioUseCase(
                    FakeCalendar(), FakeEngine(), Path("configured.db")  # type: ignore[arg-type]
                ),
                portfolio_status=PortfolioStatusUseCase(
                    FakeCalendar(), FakeStatusService()  # type: ignore[arg-type]
                ),
                verify_system=VerifySystemUseCase(
                    FakeVerificationService(failed=True)  # type: ignore[arg-type]
                ),
            )
        finally:
            connection.close()

    monkeypatch.setattr(pams.cli, "compose_application", failed_compose)
    monkeypatch.setattr(pams.cli, "compose_operations", failed_compose)
    assert main(["verify"]) == ExitCode.VERIFICATION_FAILED
    assert "FAIL" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ProviderDataError("provider"), ExitCode.PROVIDER_ERROR),
        (SourceDateError("date"), ExitCode.SOURCE_DATE_ERROR),
        (
            SourceDateMismatchError(Market.TWSE, date(2026, 7, 22), date(2026, 7, 21)),
            ExitCode.SOURCE_DATE_ERROR,
        ),
        (SymbolNotFoundError("symbol"), ExitCode.SECURITY_ERROR),
        (SuspendedSecurityError("suspended"), ExitCode.SECURITY_ERROR),
        (DuplicateSnapshotError("duplicate"), ExitCode.DUPLICATE_SNAPSHOT),
        (sqlite3.DatabaseError("database"), ExitCode.CONFIG_OR_DATABASE_ERROR),
        (RuntimeError("unexpected"), ExitCode.INTERNAL_ERROR),
    ],
)
def test_typed_exception_exit_code_policy(error: Exception, expected: ExitCode) -> None:
    assert _error_exit_code(error) == expected


def test_verbose_error_prints_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install_fake_composition(monkeypatch, FakeEngine(ProviderDataError("offline")))
    assert main(["update", "--date", "2026-07-22", "--verbose"]) == 3
    assert "Traceback" in capsys.readouterr().err


def test_reporting_formats_decimals_and_percentages() -> None:
    assert format_decimal(Decimal("12345.6")) == "12,345.60"
    assert format_percentage(Decimal("0.125")) == "12.50%"
    assert format_percentage(None) == "N/A"


def test_human_report_handles_missing_previous_close_and_stable_order() -> None:
    result = UpdatePortfolioUseCase(
        FakeCalendar(), FakeEngine(), Path("test.db")  # type: ignore[arg-type]
    ).execute(date(2026, 7, 22), dry_run=True)
    report = format_human_report(result)
    assert report.index("2330 TSMC") < report.index("8299 Phison")
    assert "100.00 / N/A" in report
    assert "Mode: dry-run" in report


def test_json_report_serializes_all_decimals_as_strings() -> None:
    result = UpdatePortfolioUseCase(
        FakeCalendar(), FakeEngine(), Path("test.db")  # type: ignore[arg-type]
    ).execute(date(2026, 7, 22))
    payload = json.loads(format_json_report(result))
    assert payload["positions"][0]["average_cost"] == "80"
    assert payload["positions"][0]["portfolio_weight"] == str(Decimal("5") / 6)
    assert payload["mode"] == "updated"
