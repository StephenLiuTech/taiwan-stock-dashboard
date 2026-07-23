"""Analytics CLI integration tests."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from database import initialize_database, initialize_schema
from domain import DailySnapshot
from pams.cli import ExitCode, main
from repositories import SQLiteSnapshotRepository


def add_snapshot(database: Path, snapshot_date: date, value: str) -> None:
    connection = initialize_database(f"sqlite:///{database.as_posix()}")
    initialize_schema(connection)
    portfolio_value = Decimal(value)
    SQLiteSnapshotRepository(connection).add(
        DailySnapshot(
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
    )
    connection.close()


def analytics_database(tmp_path: Path) -> Path:
    database = tmp_path / "analytics.db"
    add_snapshot(database, date(2026, 1, 1), "100")
    add_snapshot(database, date(2026, 1, 2), "120")
    add_snapshot(database, date(2026, 1, 3), "90")
    return database


def test_analytics_cli_human_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = analytics_database(tmp_path)
    assert main(["analytics", "portfolio", "--database", str(database)]) == 0
    output = capsys.readouterr().out
    assert "PAMS Portfolio Analytics" in output
    assert "Starting Value: NT$100.00" in output
    assert "Ending Value: NT$90.00" in output
    assert "Maximum Drawdown: -25.00%" in output


def test_analytics_cli_json_and_date_filtering(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = analytics_database(tmp_path)
    assert (
        main(
            [
                "analytics",
                "portfolio",
                "--from",
                "2026-01-02",
                "--to",
                "2026-01-03",
                "--json",
                "--database",
                str(database),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["start_date"] == "2026-01-02"
    assert payload["end_date"] == "2026-01-03"
    assert payload["starting_value"] == "120"
    assert payload["ending_value"] == "90"
    assert payload["total_return"] == "-0.25"
    assert payload["daily_returns"][0]["daily_return"] == "-0.25"


def test_analytics_cli_empty_dataset_returns_data_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "empty.db"
    connection = initialize_database(f"sqlite:///{database.as_posix()}")
    initialize_schema(connection)
    connection.close()
    assert main(["analytics", "portfolio", "--database", str(database)]) == int(
        ExitCode.CONFIG_OR_DATABASE_ERROR
    )
    captured = capsys.readouterr()
    assert "No portfolio snapshots" in captured.err
    assert "Traceback" not in captured.err


def test_analytics_cli_invalid_period_returns_cli_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = analytics_database(tmp_path)
    assert main(
        [
            "analytics",
            "portfolio",
            "--from",
            "2026-02-01",
            "--to",
            "2026-01-01",
            "--database",
            str(database),
        ]
    ) == int(ExitCode.CLI_ERROR)
    assert "start date must not be after end date" in capsys.readouterr().err


def test_analytics_cli_rejects_invalid_iso_date() -> None:
    with pytest.raises(SystemExit) as captured:
        main(["analytics", "portfolio", "--from", "2026/01/01"])
    assert captured.value.code == int(ExitCode.CLI_ERROR)
