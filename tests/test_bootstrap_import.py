"""Broker-cost and read-only bootstrap reconciliation regression tests."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import pams.cli as cli
from database.schema import initialize_schema
from database.sqlite import initialize_database
from domain import Currency, Holding, Market, Transaction, TransactionType
from pams.application import BootstrapImportError, BootstrapImportUseCase
from pams.cli import build_parser
from repositories.holding_rebuild_uow import SQLiteHoldingRebuildUnitOfWork
from repositories.sqlite import SQLiteHoldingRepository, SQLiteTransactionRepository


class Holdings:
    def __init__(self, values: list[Holding]) -> None:
        self.values = values

    def list_all(self) -> list[Holding]:
        return list(self.values)


class Transactions:
    def __init__(self, values: list[Transaction] | None = None) -> None:
        self.values = values or []

    def list_all(self) -> list[Transaction]:
        return list(self.values)


def holding(symbol: str, quantity: str, average: str) -> Holding:
    return Holding(
        id=f"holding-{symbol}",
        symbol=symbol,
        name="元大台灣50",
        market=Market.TWSE,
        currency=Currency.TWD,
        quantity=Decimal(quantity),
        average_cost=Decimal(average),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def import_files(tmp_path: Path, *, total_cost: str = "516252") -> tuple[Path, Path]:
    source = tmp_path / "statement.csv"
    source.write_text(
        "股名,日期,成交股數,淨收付,成交單價,成交價金,手續費,交易稅,稅款,委託書號,幣別,備註\n"
        '元大台灣50,2026/07/24,100,"-10,174",101.7,"10,170",4,0,0,k06xU,台幣,\n',
        encoding="utf-8",
    )
    targets = tmp_path / "targets.csv"
    targets.write_text(
        "symbol,market,quantity,average_cost,total_cost\n"
        f"0050,TWSE,6100,84.63,{total_cost}\n",
        encoding="utf-8",
    )
    return source, targets


def sqlite_use_case(tmp_path: Path) -> tuple[BootstrapImportUseCase, object]:
    connection = initialize_database(f"sqlite:///{tmp_path / 'pams.db'}")
    initialize_schema(connection)
    holdings = SQLiteHoldingRepository(connection)
    transactions = SQLiteTransactionRepository(connection)
    holdings.upsert(holding("0050", "6100", "84.63"))
    transactions.upsert(
        Transaction(
            id="placeholder",
            symbol="0050",
            market=Market.TWSE,
            transaction_type=TransactionType.BUY,
            trade_date=date(2026, 7, 24),
            settlement_date=date(2026, 7, 24),
            quantity=Decimal("6100"),
            price=Decimal("84.63"),
            currency=Currency.TWD,
        )
    )
    uow = SQLiteHoldingRebuildUnitOfWork(connection)
    return (
        BootstrapImportUseCase(
            holdings,
            transactions,
            unit_of_work=uow,  # type: ignore[arg-type]
            database="sqlite:///test",
        ),
        connection,
    )


def test_bootstrap_preview_matches_broker_cost_and_tracks_fees(tmp_path: Path) -> None:
    source = tmp_path / "statement.csv"
    source.write_text(
        "股名,日期,成交股數,淨收付,成交單價,成交價金,手續費,交易稅,稅款,委託書號,幣別,備註\n"
        '元大台灣50,2026/07/24,100,"-10,174",101.7,"10,170",4,0,0,k06xU,台幣,\n',
        encoding="utf-8",
    )

    targets = tmp_path / "targets.csv"
    targets.write_text(
        "symbol,market,quantity,average_cost,total_cost\n"
        "0050,TWSE,6100,84.63,516252\n",
        encoding="utf-8",
    )
    result = BootstrapImportUseCase(
        Holdings([holding("0050", "6100", "84.63")]),  # type: ignore[arg-type]
        Transactions(),  # type: ignore[arg-type]
    ).preview(source, targets)

    assert result.passed
    assert len(result.imported_transactions) == 1
    assert len(result.bootstrap_transactions) == 1
    assert result.bootstrap_transactions[0].quantity == Decimal("6000")
    assert result.total_buy_fees == Decimal("4")
    assert result.total_trading_expenses == Decimal("4")
    assert result.reconciliations[0].actual_quantity == Decimal("6100")
    assert result.reconciliations[0].actual_average_cost == Decimal("84.63")
    assert result.reconciliations[0].accounting_cost_basis == Decimal("516252")


def test_bootstrap_preview_is_deterministic_and_read_only(tmp_path: Path) -> None:
    source = tmp_path / "statement.csv"
    source.write_text(
        "股名,日期,成交股數,淨收付,成交單價,成交價金,手續費,交易稅,稅款,委託書號,幣別,備註\n"
        '元大台灣50,2026/07/24,100,"-10,174",101.7,"10,170",4,0,0,k06xU,台幣,\n',
        encoding="utf-8",
    )
    existing = Transaction(
        id="placeholder",
        symbol="0050",
        market=Market.TWSE,
        transaction_type=TransactionType.BUY,
        trade_date=date(2026, 7, 24),
        settlement_date=date(2026, 7, 24),
        quantity=Decimal("6100"),
        price=Decimal("84.63"),
        currency=Currency.TWD,
    )
    transactions = Transactions([existing])
    targets = tmp_path / "targets.csv"
    targets.write_text(
        "symbol,market,quantity,average_cost,total_cost\n"
        "0050,TWSE,6100,84.63,516252\n",
        encoding="utf-8",
    )
    use_case = BootstrapImportUseCase(
        Holdings([holding("0050", "6100", "84.63")]),  # type: ignore[arg-type]
        transactions,  # type: ignore[arg-type]
    )

    first = use_case.preview(source, targets)
    second = use_case.preview(source, targets)

    assert first == second
    assert first.placeholder_transaction_ids == ("placeholder",)
    assert transactions.values == [existing]


def test_cli_requires_exactly_one_bootstrap_mode() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["transaction", "bootstrap-import"])
    with pytest.raises(SystemExit):
        parser.parse_args(["transaction", "bootstrap-import", "--dry-run", "--apply"])
    assert parser.parse_args(
        ["transaction", "bootstrap-import", "--apply", "--yes"]
    ).apply


def test_apply_success_and_duplicate_are_atomic_and_idempotent(tmp_path: Path) -> None:
    source, targets = import_files(tmp_path)
    use_case, connection = sqlite_use_case(tmp_path)

    applied = use_case.apply(source, targets)
    duplicate = use_case.apply(source, targets)

    assert applied.applied and not applied.duplicate
    assert duplicate.duplicate and not duplicate.applied
    assert connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    row = connection.execute(
        "SELECT quantity, average_cost FROM holdings WHERE symbol='0050'"
    ).fetchone()
    assert Decimal(row[0]) == Decimal("6100")
    assert Decimal(row[1]) * Decimal(row[0]) == Decimal("516252")


def test_reconciliation_failure_aborts_before_write(tmp_path: Path) -> None:
    source, targets = import_files(tmp_path, total_cost="1")
    use_case, connection = sqlite_use_case(tmp_path)

    with pytest.raises(BootstrapImportError, match="cannot reconcile"):
        use_case.apply(source, targets)

    assert (
        connection.execute("SELECT id FROM transactions").fetchone()[0] == "placeholder"
    )


def test_write_failure_rolls_back_entire_import(tmp_path: Path) -> None:
    source, targets = import_files(tmp_path)
    use_case, connection = sqlite_use_case(tmp_path)
    original = use_case.unit_of_work.transactions.upsert

    def fail_after_write(value: Transaction) -> None:
        original(value)
        raise RuntimeError("write failed")

    use_case.unit_of_work.transactions.upsert = fail_after_write  # type: ignore[method-assign,union-attr]
    with pytest.raises(RuntimeError, match="write failed"):
        use_case.apply(source, targets)

    assert (
        connection.execute("SELECT id FROM transactions").fetchone()[0] == "placeholder"
    )


def test_post_write_reconciliation_failure_rolls_back(tmp_path: Path) -> None:
    source, targets = import_files(tmp_path)
    use_case, connection = sqlite_use_case(tmp_path)
    original = use_case.unit_of_work.holdings.upsert

    def corrupt(value: Holding) -> None:
        original(value.model_copy(update={"average_cost": Decimal("1")}))

    use_case.unit_of_work.holdings.upsert = corrupt  # type: ignore[method-assign,union-attr]
    with pytest.raises(BootstrapImportError, match="Post-write"):
        use_case.apply(source, targets)

    row = connection.execute(
        "SELECT average_cost FROM holdings WHERE symbol='0050'"
    ).fetchone()
    assert Decimal(row[0]) == Decimal("84.63")


def test_user_confirmation_rejection_performs_no_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, targets = import_files(tmp_path)
    use_case, connection = sqlite_use_case(tmp_path)

    @contextmanager
    def compose(_database: Path | None) -> Iterator[BootstrapImportUseCase]:
        yield use_case

    monkeypatch.setattr(cli, "compose_bootstrap_import", compose)
    monkeypatch.setattr("builtins.input", lambda _prompt: "NO")

    result = cli.main(
        [
            "transaction",
            "bootstrap-import",
            "--apply",
            "--source",
            str(source),
            "--targets",
            str(targets),
        ]
    )

    assert result == 0
    assert "cancelled" in capsys.readouterr().out
    assert (
        connection.execute("SELECT id FROM transactions").fetchone()[0] == "placeholder"
    )
