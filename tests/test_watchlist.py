"""Watchlist repository, application, and CLI regression tests."""

# ruff: noqa: ANN001

from decimal import Decimal
from pathlib import Path

from database.schema import initialize_schema
from domain import Market, WatchlistItem
from pams.application import (
    DuplicateWatchlistItemError,
    WatchlistError,
    WatchlistItemNotFoundError,
    WatchlistUseCase,
)
from pams.cli import ExitCode, main
from repositories import SQLitePriceQuoteRepository, SQLiteWatchlistRepository


def test_watchlist_repository_add_list_show_remove(connection) -> None:
    initialize_schema(connection)
    repository = SQLiteWatchlistRepository(connection)
    item = WatchlistItem(
        symbol="2330",
        market=Market.TWSE,
        display_name="TSMC",
        target_price=Decimal("1200.5"),
    )
    repository.add(item)
    assert repository.list_all() == [item]
    assert repository.get("2330") == item
    assert repository.remove("2330") is True
    assert repository.remove("2330") is False


def test_watchlist_use_case_duplicate_missing_and_missing_quote(connection) -> None:
    initialize_schema(connection)
    use_case = WatchlistUseCase(
        SQLiteWatchlistRepository(connection), SQLitePriceQuoteRepository(connection)
    )
    result = use_case.add("2330", "TWSE", target_price=Decimal("1000"))
    assert result.latest_price is None
    try:
        use_case.add("2330", "TWSE")
    except DuplicateWatchlistItemError:
        pass
    else:
        raise AssertionError("duplicate watchlist entry was accepted")
    use_case.remove("2330")
    try:
        use_case.show("2330")
    except WatchlistItemNotFoundError:
        pass
    else:
        raise AssertionError("missing watchlist entry was returned")
    try:
        use_case.add("2330", "NYSE")
    except WatchlistError:
        pass
    else:
        raise AssertionError("unsupported watchlist market was accepted")


def test_watchlist_cli_round_trip(tmp_path: Path, capsys) -> None:
    database = tmp_path / "watchlist.db"
    common = ["--database", str(database)]
    assert main(["watchlist", "add", "2330", "--market", "TWSE", *common]) == 0
    assert main(["watchlist", "list", *common]) == 0
    assert "2330 | TWSE" in capsys.readouterr().out
    assert main(["watchlist", "show", "2330", *common]) == 0
    assert main(["watchlist", "remove", "2330", *common]) == 0
    assert main(["watchlist", "show", "2330", *common]) == ExitCode.SECURITY_ERROR
