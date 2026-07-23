"""Daily report CLI integration tests."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from database import initialize_database, initialize_schema
from domain import Currency, DailySnapshot, Holding, Market, PriceQuote
from pams.cli import main
from repositories import (
    SQLiteHoldingRepository,
    SQLitePriceQuoteRepository,
    SQLiteSnapshotRepository,
)


def report_database(path: Path) -> None:
    connection = initialize_database(f"sqlite:///{path.as_posix()}")
    initialize_schema(connection)
    SQLiteHoldingRepository(connection).upsert(
        Holding(
            id="holding-2330",
            symbol="2330",
            name="TSMC",
            market=Market.TWSE,
            currency=Currency.TWD,
            quantity=Decimal("10"),
            average_cost=Decimal("80"),
        )
    )
    SQLitePriceQuoteRepository(connection).upsert_many(
        [
            PriceQuote(
                symbol="2330",
                market=Market.TWSE,
                trade_date=date(2026, 7, 22),
                close_price=Decimal("100"),
                currency=Currency.TWD,
                source="test",
            )
        ]
    )
    snapshots = SQLiteSnapshotRepository(connection)
    for snapshot_date, value in (
        (date(2026, 7, 21), Decimal("900")),
        (date(2026, 7, 22), Decimal("1000")),
    ):
        snapshots.add(
            DailySnapshot(
                snapshot_date=snapshot_date,
                total_market_value=value,
                total_cost_basis=Decimal("0"),
                total_unrealized_pnl=value,
                total_liabilities=Decimal("0"),
                net_asset_value=value,
                leverage_ratio=Decimal("0"),
                high_water_mark=value,
                drawdown=Decimal("0"),
            )
        )
    connection.commit()
    connection.close()


def test_report_generate_prints_markdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "report.db"
    report_database(database)
    assert main(["report", "generate", "--database", str(database)]) == 0
    output = capsys.readouterr().out
    assert output.startswith("# Portfolio Report\n")
    assert "## Portfolio Analytics" in output
    assert "Period: 2026-07-21 to 2026-07-22" in output
    assert "Total Return: +11.11%" in output


@pytest.mark.parametrize(
    ("html", "filename", "prefix"),
    [
        (False, "report.md", "# Portfolio Report"),
        (True, "report.html", "<!doctype html>"),
    ],
)
def test_report_generate_writes_file(
    tmp_path: Path, html: bool, filename: str, prefix: str
) -> None:
    database = tmp_path / "report.db"
    output = tmp_path / filename
    report_database(database)
    arguments = [
        "report",
        "generate",
        "--output",
        str(output),
        "--database",
        str(database),
    ]
    if html:
        arguments.append("--html")
    assert main(arguments) == 0
    assert output.read_text(encoding="utf-8").startswith(prefix)


def test_report_generate_handles_missing_analytics_without_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "report-without-snapshots.db"
    connection = initialize_database(f"sqlite:///{database.as_posix()}")
    initialize_schema(connection)
    connection.close()
    assert main(["report", "generate", "--database", str(database)]) == 0
    output = capsys.readouterr().out
    assert "Portfolio analytics are unavailable until snapshots exist." in output
    assert "Traceback" not in output


def test_report_generate_handles_invalid_analytics_period(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "invalid-report-period.db"
    report_database(database)
    assert (
        main(
            [
                "report",
                "generate",
                "--from",
                "2026-07-22",
                "--to",
                "2026-07-21",
                "--database",
                str(database),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "start date must not be after the end date" in output
    assert "Traceback" not in output
