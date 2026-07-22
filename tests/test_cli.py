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
from market_data import (
    ProviderDataError,
    SourceDateError,
    SourceDateMismatchError,
    SuspendedSecurityError,
    SymbolNotFoundError,
)
from market_data.engine import MarketDataRefreshResult
from pams.cli import ExitCode, _error_exit_code, main
from pams.composition import ApplicationContext
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


def install_fake_composition(
    monkeypatch: pytest.MonkeyPatch,
    engine: FakeEngine,
    captured: dict[str, object] | None = None,
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
                connection,
                engine,  # type: ignore[arg-type]
                database_override or Path("configured.db"),
                False,
            )
        finally:
            connection.close()

    monkeypatch.setattr(pams.cli, "compose_application", fake_compose)


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


@pytest.mark.parametrize("argv", [["update"], ["update", "--date", "2026/07/22"]])
def test_missing_or_invalid_date_returns_argparse_exit_two(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == ExitCode.CLI_ERROR


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
    report = format_human_report(
        sample_result(), date(2026, 7, 22), Path("test.db"), dry_run=True
    )
    assert report.index("2330 TSMC") < report.index("8299 Phison")
    assert "100.00 / N/A" in report
    assert "Mode: dry-run" in report


def test_json_report_serializes_all_decimals_as_strings() -> None:
    payload = json.loads(
        format_json_report(
            sample_result(), date(2026, 7, 22), Path("test.db"), dry_run=False
        )
    )
    assert payload["positions"][0]["average_cost"] == "80"
    assert payload["positions"][0]["portfolio_weight"] == str(Decimal("5") / 6)
    assert payload["mode"] == "persisted"
