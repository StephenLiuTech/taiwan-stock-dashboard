"""Offline tests for isolated deterministic demo-data generation."""

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app import parse_database_override
from database import initialize_database
from pams.application import (
    DemoDataUseCase,
    PortfolioHistoryUseCase,
    PortfolioStatusUseCase,
    ProductionDatabaseProtectedError,
)
from pams.cli import ExitCode, main
from pams.operations import OperationalStatusService
from repositories import (
    SQLiteHoldingRepository,
    SQLiteLiabilityRepository,
    SQLitePositionSnapshotRepository,
    SQLitePriceQuoteRepository,
    SQLiteSnapshotRepository,
)


class UnavailableCalendar:
    def market_availability(self) -> object:
        raise RuntimeError("offline by design")


def build_demo(tmp_path: Path) -> tuple[Path, object]:
    production = tmp_path / "production.db"
    production.write_bytes(b"production sentinel")
    demo = tmp_path / "demo.db"
    return demo, DemoDataUseCase(production).execute(demo)


def test_demo_database_contains_complete_deterministic_pipeline(tmp_path: Path) -> None:
    demo, result = build_demo(tmp_path)
    connection = initialize_database(f"sqlite:///{demo.as_posix()}")
    try:
        holdings = SQLiteHoldingRepository(connection).list_all()
        liabilities = SQLiteLiabilityRepository(connection).list_all()
        quotes = SQLitePriceQuoteRepository(connection).list_by_date(result.quote_date)
        daily = SQLiteSnapshotRepository(connection).list_between_dates(
            date.min, date.max
        )
        positions = SQLitePositionSnapshotRepository(connection).list_by_date(
            result.quote_date
        )
    finally:
        connection.close()
    assert len(holdings) == 5
    assert len(liabilities) == 2
    assert len(quotes) == 5
    assert {quote.source for quote in quotes} == {"demo_fixture"}
    assert len(daily) == 30
    assert len(positions) == 5
    assert {quote.symbol: quote.close_price for quote in quotes} == {
        "0050": Decimal("92.30"),
        "2027": Decimal("44.80"),
        "2330": Decimal("1955.00"),
        "3293": Decimal("910.00"),
        "8299": Decimal("2685.00"),
    }
    assert daily[-1].total_market_value == result.total_market_value
    assert sum((item.market_value for item in positions), Decimal("0")) == (
        result.total_market_value
    )


def test_demo_history_and_overview_are_available_through_use_cases(
    tmp_path: Path,
) -> None:
    demo, result = build_demo(tmp_path)
    connection = initialize_database(f"sqlite:///{demo.as_posix()}")
    try:
        holdings = SQLiteHoldingRepository(connection)
        snapshots = SQLiteSnapshotRepository(connection)
        positions = SQLitePositionSnapshotRepository(connection)
        quotes = SQLitePriceQuoteRepository(connection)
        history = PortfolioHistoryUseCase(snapshots).execute()
        overview = PortfolioStatusUseCase(
            UnavailableCalendar(),  # type: ignore[arg-type]
            OperationalStatusService(connection, demo),
            holdings,
            snapshots,
            positions,
            quotes,
        ).execute()
    finally:
        connection.close()
    assert len(history.points) == 30
    assert len(overview.holdings) == 5
    assert overview.market_value == result.total_market_value
    assert overview.net_equity == result.net_equity
    assert overview.latest_daily_snapshot == date(2026, 7, 22)


def test_production_database_target_is_rejected_without_modification(
    tmp_path: Path,
) -> None:
    production = tmp_path / "production.db"
    production.write_bytes(b"do not modify")
    before = production.read_bytes()
    with pytest.raises(ProductionDatabaseProtectedError):
        DemoDataUseCase(production).execute(production)
    assert production.read_bytes() == before


def test_demo_generation_is_repeatable_and_custom_path_works(tmp_path: Path) -> None:
    production = tmp_path / "production.db"
    production.write_bytes(b"production")
    custom = tmp_path / "nested" / "custom_demo.db"
    use_case = DemoDataUseCase(production)
    first = use_case.execute(custom)
    connection = sqlite3.connect(custom)
    try:
        connection.execute("DELETE FROM daily_snapshots")
        connection.commit()
    finally:
        connection.close()
    second = use_case.execute(custom)
    assert first.total_market_value == second.total_market_value
    connection = sqlite3.connect(custom)
    try:
        assert (
            connection.execute("SELECT COUNT(*) FROM daily_snapshots").fetchone()[0]
            == 30
        )
    finally:
        connection.close()


def test_demo_generation_does_not_call_market_providers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider must not be called")

    monkeypatch.setattr("market_data.providers.TWSEProvider.fetch", forbidden_call)
    monkeypatch.setattr("market_data.providers.TPExProvider.fetch", forbidden_call)
    build_demo(tmp_path)


def test_demo_cli_output_and_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    demo = tmp_path / "cli_demo.db"
    assert main(["demo-data", "--database", str(demo)]) == ExitCode.SUCCESS
    output = capsys.readouterr().out
    assert "PAMS Demo Data" in output
    assert "Holdings: 5" in output
    assert "History points: 30" in output
    assert f'--database "{demo.resolve()}"' in output


def test_demo_cli_rejects_configured_production_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    production = tmp_path / "production.db"
    monkeypatch.setenv("PAMS_DATABASE_URL", f"sqlite:///{production.as_posix()}")
    from config import get_settings

    get_settings.cache_clear()
    try:
        assert main(["demo-data", "--database", str(production)]) == 7
    finally:
        get_settings.cache_clear()


def test_dashboard_accepts_forwarded_database_override(tmp_path: Path) -> None:
    database = tmp_path / "demo.db"
    assert parse_database_override(["--database", str(database)]) == database
