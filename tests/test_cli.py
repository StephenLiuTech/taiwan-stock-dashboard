"""Offline tests for CLI routing, reporting, and exit policies."""

import json
import smtplib
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
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
    DailyReportDeliveryError,
    DailyReportSendResult,
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
        self.rebuild_called = False

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

    def rebuild(self, trade_date: date) -> MarketDataRefreshResult:
        self.rebuild_called = True
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


class FakeSnapshots:
    def __init__(self, existing_date: date | None = None) -> None:
        self.existing_date = existing_date

    def get_by_date(self, snapshot_date: date) -> object | None:
        return object() if snapshot_date == self.existing_date else None


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
    def __init__(self, *, failed: bool = False, check_name: str = "test") -> None:
        self.failed = failed
        self.check_name = check_name

    def run(self) -> VerificationReport:
        return VerificationReport(
            (
                VerificationCheck(
                    self.check_name,
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
    existing_snapshot_date: date | None = None,
    verification_service: FakeVerificationService | None = None,
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
                verification=(  # type: ignore[arg-type]
                    verification_service or FakeVerificationService()
                ),
                update_portfolio=UpdatePortfolioUseCase(
                    calendar or FakeCalendar(),  # type: ignore[arg-type]
                    engine,  # type: ignore[arg-type]
                    database_override or Path("configured.db"),
                    lambda _: engine,  # type: ignore[arg-type]
                    FakeSnapshots(existing_snapshot_date),  # type: ignore[arg-type]
                ),
                portfolio_status=PortfolioStatusUseCase(
                    calendar or FakeCalendar(),  # type: ignore[arg-type]
                    FakeStatusService(),  # type: ignore[arg-type]
                ),
                verify_system=VerifySystemUseCase(
                    verification_service or FakeVerificationService()  # type: ignore[arg-type]
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


def test_repeated_automatic_update_exits_successfully_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = FakeEngine()
    install_fake_composition(
        monkeypatch, engine, existing_snapshot_date=date(2026, 7, 22)
    )
    assert main(["update", "--verbose"]) == ExitCode.SUCCESS
    captured = capsys.readouterr()
    assert "No update performed: snapshot already exists for 2026-07-22" in captured.out
    assert "Traceback" not in captured.err
    assert engine.refresh_called is False
    assert engine.preview_called is False


def test_repeated_automatic_update_force_routes_to_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    install_fake_composition(
        monkeypatch, engine, existing_snapshot_date=date(2026, 7, 22)
    )

    assert main(["update", "--force"]) == ExitCode.SUCCESS
    assert engine.rebuild_called is True
    assert engine.refresh_called is False
    assert engine.preview_called is False


@pytest.mark.parametrize(
    ("twse_date", "tpex_date"),
    [
        (date(2026, 7, 21), date(2026, 7, 22)),
        (date(2026, 7, 22), date(2026, 7, 21)),
    ],
)
def test_unsynchronized_automatic_update_uses_joint_date(
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
    assert f"Requested trade date: {min(twse_date, tpex_date)}" in output
    assert engine.preview_called is False
    assert engine.refresh_called is True


def test_unsynchronized_automatic_update_json_uses_joint_date(
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
    assert payload["mode"] == "updated"
    assert payload["requested_date"] == "2026-07-21"
    assert payload["commonly_ingestible_date"] == "2026-07-21"
    assert engine.refresh_called is True


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
    assert "Latest persisted quote date:" in output
    assert "Latest persisted daily snapshot:" in output
    assert "Latest persisted position snapshot:" in output
    assert "Latest live TWSE source date: 2026-07-22" in output
    assert "Latest live TPEx source date: 2026-07-22" in output
    assert "Latest live commonly ingestible date: 2026-07-22" in output


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
    assert "Latest live TWSE source date: 2026-07-21" in output
    assert "Latest live TPEx source date: 2026-07-22" in output
    assert "Latest live commonly ingestible date: 2026-07-21" in output


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


def test_verify_market_source_warning_mode_exits_successfully(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install_fake_composition(
        monkeypatch,
        FakeEngine(),
        verification_service=FakeVerificationService(
            failed=True, check_name="TWSE endpoint"
        ),
    )

    assert main(["verify"]) == ExitCode.VERIFICATION_FAILED
    assert "FAIL" in capsys.readouterr().out

    assert main(["verify", "--allow-market-source-warning"]) == ExitCode.SUCCESS
    output = capsys.readouterr().out
    assert "WARN" in output
    assert "TWSE endpoint" in output


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


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            DailyReportSendResult(
                date(2026, 7, 22),
                "recipient@example.com",
                "PAMS Daily Portfolio Report - 2026-07-22",
                "sent",
            ),
            "Result: sent",
        ),
        (
            DailyReportSendResult(
                date(2026, 7, 22), "recipient@example.com", "", "already_sent"
            ),
            "No email sent: report already delivered for 2026-07-22",
        ),
        (
            DailyReportSendResult(
                date(2026, 7, 22),
                "recipient@example.com",
                "PAMS Daily Portfolio Report - 2026-07-22",
                "dry_run",
            ),
            "Result: dry-run; no email sent",
        ),
    ],
)
def test_daily_report_cli_successful_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    result: DailyReportSendResult,
    expected: str,
) -> None:
    class SendStub:
        def execute(self, *_args: object, **_kwargs: object) -> DailyReportSendResult:
            return result

    @contextmanager
    def compose(*_args: object, **_kwargs: object) -> Iterator[object]:
        yield type("Context", (), {"send_daily_report": SendStub()})()

    monkeypatch.setattr(pams.cli, "compose_daily_report", compose)
    monkeypatch.setattr(pams.cli, "selected_email_transport", lambda: "resend")
    arguments = ["daily-report", "send"]
    if result.status == "dry_run":
        arguments.append("--dry-run")
    assert main(arguments) == ExitCode.SUCCESS
    output = capsys.readouterr().out
    assert "PAMS Daily Report" in output
    assert "Email Transport : resend" in output
    assert "Recipient: recipient@example.com" in output
    assert expected in output


@pytest.mark.parametrize("transport", ["resend", "microsoft_graph", "smtp"])
def test_daily_report_cli_prints_selected_transport(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    transport: str,
) -> None:
    class SendStub:
        def execute(self, *_args: object, **_kwargs: object) -> DailyReportSendResult:
            return DailyReportSendResult(
                date(2026, 7, 22),
                "recipient@example.com",
                "PAMS report",
                "sent",
            )

    @contextmanager
    def compose(*_args: object, **_kwargs: object) -> Iterator[object]:
        yield type("Context", (), {"send_daily_report": SendStub()})()

    monkeypatch.setattr(pams.cli, "compose_daily_report", compose)
    monkeypatch.setattr(pams.cli, "selected_email_transport", lambda: transport)

    assert main(["daily-report", "send"]) == ExitCode.SUCCESS
    assert f"Email Transport : {transport}" in capsys.readouterr().out


def test_daily_report_cli_failure_never_prints_smtp_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "never-print-this-password"

    class SendStub:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise DailyReportDeliveryError("daily report delivery failed")

    @contextmanager
    def compose(*_args: object, **_kwargs: object) -> Iterator[object]:
        yield type("Context", (), {"send_daily_report": SendStub()})()

    monkeypatch.setattr(pams.cli, "compose_daily_report", compose)
    monkeypatch.setenv("PAMS_SMTP_PASSWORD", secret)
    assert main(["daily-report", "send", "--verbose"]) == ExitCode.PROVIDER_ERROR
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err


def test_daily_report_debug_prints_complete_chained_smtp_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class SendStub:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            smtp_error = smtplib.SMTPAuthenticationError(535, b"authentication failed")
            raise DailyReportDeliveryError(
                "daily report delivery failed: SMTPAuthenticationError: "
                "(535, b'authentication failed')"
            ) from smtp_error

    @contextmanager
    def compose(*_args: object, **_kwargs: object) -> Iterator[object]:
        yield type("Context", (), {"send_daily_report": SendStub()})()

    monkeypatch.setattr(pams.cli, "compose_daily_report", compose)
    assert main(["daily-report", "send", "--debug"]) == ExitCode.PROVIDER_ERROR
    captured = capsys.readouterr()
    assert "Traceback" in captured.err
    assert "SMTPAuthenticationError" in captured.err
    assert "DailyReportDeliveryError" in captured.err
    assert "authentication failed" in captured.err


def test_daily_report_normal_failure_omits_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class SendStub:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise DailyReportDeliveryError(
                "daily report delivery failed: TimeoutError: timed out"
            )

    @contextmanager
    def compose(*_args: object, **_kwargs: object) -> Iterator[object]:
        yield type("Context", (), {"send_daily_report": SendStub()})()

    monkeypatch.setattr(pams.cli, "compose_daily_report", compose)
    assert main(["daily-report", "send"]) == ExitCode.PROVIDER_ERROR
    captured = capsys.readouterr()
    assert "daily report delivery failed: TimeoutError: timed out" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("diagnostic_flag", ["--debug", "--verbose"])
def test_email_authorize_cli_prints_device_prompt_without_tokens(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    diagnostic_flag: str,
) -> None:
    class AuthorizationStub:
        def execute(self, show_prompt: object) -> None:
            show_prompt(  # type: ignore[operator]
                "https://microsoft.com/devicelogin", "ABCD-EFGH"
            )

    monkeypatch.setattr(
        pams.cli, "compose_email_authorization", lambda: AuthorizationStub()
    )

    assert main(["email", "authorize", diagnostic_flag]) == ExitCode.SUCCESS
    captured = capsys.readouterr()
    assert "https://microsoft.com/devicelogin" in captured.out
    assert "ABCD-EFGH" in captured.out
    assert "token cache updated" in captured.out
    assert "access_token" not in captured.out
    assert "refresh_token" not in captured.out


@pytest.mark.parametrize("diagnostic_flag", ["--debug", "--verbose"])
def test_email_authorize_diagnostic_flags_print_failures_without_attribute_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    diagnostic_flag: str,
) -> None:
    class AuthorizationStub:
        def execute(self, _show_prompt: object) -> None:
            raise RuntimeError("authorization failed")

    monkeypatch.setattr(
        pams.cli, "compose_email_authorization", lambda: AuthorizationStub()
    )

    assert main(["email", "authorize", diagnostic_flag]) == ExitCode.INTERNAL_ERROR
    captured = capsys.readouterr()
    assert "authorization failed" in captured.err
    assert "Traceback" in captured.err
    assert "AttributeError" not in captured.err


def test_reporting_formats_decimals_and_percentages() -> None:
    assert format_decimal(Decimal("12345.6")) == "12,345.60"
    assert format_percentage(Decimal("0.125")) == "12.50%"
    assert format_percentage(None) == "N/A"


def test_human_report_handles_missing_previous_close_and_stable_order() -> None:
    engine = FakeEngine()
    result = UpdatePortfolioUseCase(
        FakeCalendar(),
        engine,  # type: ignore[arg-type]
        Path("test.db"),
        lambda _: engine,  # type: ignore[arg-type]
    ).execute(date(2026, 7, 22), dry_run=True)
    report = format_human_report(result)
    assert report.index("2330 TSMC") < report.index("8299 Phison")
    assert "100.00 / N/A" in report
    assert "Mode: dry-run" in report


def test_json_report_serializes_all_decimals_as_strings() -> None:
    engine = FakeEngine()
    result = UpdatePortfolioUseCase(
        FakeCalendar(),
        engine,  # type: ignore[arg-type]
        Path("test.db"),
        lambda _: engine,  # type: ignore[arg-type]
    ).execute(date(2026, 7, 22))
    payload = json.loads(format_json_report(result))
    assert payload["positions"][0]["average_cost"] == "80"
    assert payload["positions"][0]["portfolio_weight"] == str(Decimal("5") / 6)
    assert payload["mode"] == "updated"


def test_json_report_is_ascii_safe_for_windows_consoles() -> None:
    engine = FakeEngine()
    result = UpdatePortfolioUseCase(
        FakeCalendar(),
        engine,  # type: ignore[arg-type]
        Path("test.db"),
        lambda _: engine,  # type: ignore[arg-type]
    ).execute(date(2026, 7, 22))
    localized = replace(
        result,
        positions=(
            replace(result.positions[0], name="台積電"),
            *result.positions[1:],
        ),
    )
    rendered = format_json_report(localized)
    assert "台積電" not in rendered
    assert "\\u53f0" in rendered
    assert json.loads(rendered)["positions"][0]["name"] == "台積電"
