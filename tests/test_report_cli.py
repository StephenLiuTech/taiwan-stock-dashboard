"""Daily report CLI integration tests."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from database import initialize_database, initialize_schema
from domain import Currency, Holding, Market, PriceQuote
from pams.cli import main
from repositories import SQLiteHoldingRepository, SQLitePriceQuoteRepository


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
    connection.commit()
    connection.close()


def test_report_generate_prints_markdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "report.db"
    report_database(database)
    assert main(["report", "generate", "--database", str(database)]) == 0
    assert capsys.readouterr().out.startswith("# Portfolio Report\n")


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
