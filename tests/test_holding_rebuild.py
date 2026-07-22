"""Application and SQLite tests for safe holding rebuild persistence."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest

from domain import (
    Currency,
    DailySnapshot,
    Holding,
    HoldingType,
    Market,
    PositionSnapshot,
    Transaction,
    TransactionType,
)
from pams.application import (
    ApplyRebuiltHoldingsUseCase,
    EmptyTransactionHistoryError,
    HoldingChangeAction,
    UnmatchedHoldingsError,
)
from repositories import (
    SQLiteHoldingRebuildUnitOfWork,
    SQLiteHoldingRepository,
    SQLitePositionSnapshotRepository,
    SQLiteSnapshotRepository,
    SQLiteTransactionRepository,
)
from services import HoldingProjectionMetadata


def holding(identifier: str, symbol: str, quantity: str, average_cost: str) -> Holding:
    return Holding(
        id=identifier,
        symbol=symbol,
        name=symbol,
        market=Market.TWSE,
        currency=Currency.TWD,
        quantity=Decimal(quantity),
        average_cost=Decimal(average_cost),
    )


def buy(
    identifier: str,
    symbol: str,
    quantity: str,
    price: str,
    *,
    fees: str = "0",
) -> Transaction:
    return Transaction(
        id=identifier,
        symbol=symbol,
        market=Market.TWSE,
        transaction_type=TransactionType.BUY,
        trade_date=date(2026, 1, 2),
        settlement_date=date(2026, 1, 4),
        quantity=Decimal(quantity),
        price=Decimal(price),
        fees=Decimal(fees),
        taxes=Decimal("0"),
        currency=Currency.TWD,
    )


class FakeHoldingRepository:
    def __init__(self, holdings: list[Holding], fail_symbol: str | None = None) -> None:
        self.records = {item.id: item for item in holdings}
        self.fail_symbol = fail_symbol
        self.upsert_calls = 0
        self.delete_calls = 0

    def list_all(self) -> list[Holding]:
        return list(self.records.values())

    def upsert(self, item: Holding) -> None:
        self.upsert_calls += 1
        if item.symbol == self.fail_symbol:
            raise RuntimeError("forced holding failure")
        self.records[item.id] = item


class FakeTransactionRepository:
    def __init__(self, transactions: list[Transaction]) -> None:
        self.records = transactions

    def list_all(self) -> list[Transaction]:
        return list(self.records)


class FakeUnitOfWork:
    def __init__(
        self,
        holdings: list[Holding],
        transactions: list[Transaction],
        *,
        fail_symbol: str | None = None,
    ) -> None:
        self.holdings = FakeHoldingRepository(holdings, fail_symbol)
        self.transactions = FakeTransactionRepository(transactions)
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        before = dict(self.holdings.records)
        try:
            yield
        except Exception:
            self.holdings.records = before
            self.rollbacks += 1
            raise
        else:
            self.commits += 1


def metadata(
    *symbols: str,
) -> dict[tuple[str, Market, Currency], HoldingProjectionMetadata]:
    return {
        (symbol, Market.TWSE, Currency.TWD): HoldingProjectionMetadata(
            f"Name {symbol}", HoldingType.STOCK
        )
        for symbol in symbols
    }


def test_preview_builds_change_plan_without_writes() -> None:
    uow = FakeUnitOfWork(
        [
            holding("stable-a", "2330", "100", "10"),
            holding("stable-b", "2027", "50", "10"),
            holding("bootstrap", "0050", "20", "8"),
        ],
        [
            buy("a", "2330", "100", "10"),
            buy("b", "2027", "60", "12"),
            buy("c", "8299", "5", "20", fees="1"),
        ],
    )
    plan = ApplyRebuiltHoldingsUseCase(
        uow, metadata("2330", "2027", "8299")  # type: ignore[arg-type]
    ).execute()
    assert [item.action for item in plan.items] == [
        HoldingChangeAction.CREATE,
        HoldingChangeAction.UPDATE,
        HoldingChangeAction.UNCHANGED,
        HoldingChangeAction.CLOSE,
    ]
    assert plan.created_holdings[0].new_cost_basis == Decimal("101")
    assert plan.updated_holdings[0].old_cost_basis == Decimal("500")
    assert plan.projected_total_cost_basis == Decimal("1821")
    assert plan.transaction_count == 3
    assert plan.applied is False
    assert "0050" in plan.warnings[0]
    assert uow.holdings.upsert_calls == 0
    assert uow.commits == 0


def test_apply_rejects_warnings_then_override_updates_creates_and_closes() -> None:
    existing = [
        holding("stable", "2330", "1", "1"),
        holding("unmatched", "0050", "10", "5"),
    ]
    uow = FakeUnitOfWork(existing, [buy("buy", "2330", "2", "10")])
    use_case = ApplyRebuiltHoldingsUseCase(
        uow, metadata("2330")  # type: ignore[arg-type]
    )
    with pytest.raises(UnmatchedHoldingsError):
        use_case.execute(apply=True)
    assert uow.rollbacks == 1
    assert uow.holdings.records["stable"].quantity == Decimal("1")
    plan = use_case.execute(apply=True, allow_unmatched_holdings=True)
    assert plan.applied is True
    assert uow.commits == 1
    assert uow.holdings.records["stable"].id == "stable"
    assert uow.holdings.records["stable"].quantity == Decimal("2")
    closed = uow.holdings.records["unmatched"]
    assert closed.quantity == Decimal("0")
    assert closed.average_cost == Decimal("0")
    assert closed.name == "0050"
    assert uow.holdings.delete_calls == 0


def test_apply_creates_new_holding_and_unchanged_is_not_written() -> None:
    same = holding("stable", "2330", "1", "10")
    uow = FakeUnitOfWork(
        [same], [buy("a", "2330", "1", "10"), buy("b", "8299", "2", "20")]
    )
    plan = ApplyRebuiltHoldingsUseCase(
        uow, metadata("2330", "8299")  # type: ignore[arg-type]
    ).execute(apply=True)
    assert len(plan.unchanged_holdings) == 1
    assert len(plan.created_holdings) == 1
    assert uow.holdings.upsert_calls == 1
    created = next(
        item for item in uow.holdings.records.values() if item.symbol == "8299"
    )
    assert created.quantity == Decimal("2")


def test_empty_transaction_history_rejects_apply_even_with_override() -> None:
    uow = FakeUnitOfWork([holding("bootstrap", "2330", "1", "10")], [])
    use_case = ApplyRebuiltHoldingsUseCase(uow, {})  # type: ignore[arg-type]
    preview = use_case.execute()
    assert preview.transaction_count == 0
    assert preview.warnings
    with pytest.raises(EmptyTransactionHistoryError):
        use_case.execute(apply=True, allow_unmatched_holdings=True)
    assert uow.holdings.records["bootstrap"].quantity == Decimal("1")


def test_fake_repository_failure_rolls_back_all_writes() -> None:
    original = [
        holding("one", "2330", "1", "1"),
        holding("two", "2027", "1", "1"),
    ]
    uow = FakeUnitOfWork(
        original,
        [buy("a", "2330", "2", "10"), buy("b", "2027", "2", "20")],
        fail_symbol="2027",
    )
    with pytest.raises(RuntimeError, match="forced"):
        ApplyRebuiltHoldingsUseCase(
            uow, metadata("2330", "2027")  # type: ignore[arg-type]
        ).execute(apply=True)
    assert uow.rollbacks == 1
    assert uow.holdings.records["one"].quantity == Decimal("1")
    assert uow.holdings.records["two"].quantity == Decimal("1")


def test_sqlite_apply_is_atomic_and_snapshots_are_untouched(
    connection: sqlite3.Connection,
) -> None:
    holdings = SQLiteHoldingRepository(connection)
    transactions = SQLiteTransactionRepository(connection)
    holdings.upsert(holding("one", "2330", "1", "1"))
    holdings.upsert(holding("two", "2027", "1", "1"))
    transactions.add(buy("a", "2330", "2", "10"))
    transactions.add(buy("b", "2027", "2", "20"))
    snapshot = DailySnapshot(
        snapshot_date=date(2025, 12, 31),
        total_market_value=Decimal("100"),
        total_cost_basis=Decimal("90"),
        total_unrealized_pnl=Decimal("10"),
        total_liabilities=Decimal("0"),
        net_asset_value=Decimal("100"),
        leverage_ratio=Decimal("0"),
        high_water_mark=Decimal("100"),
        drawdown=Decimal("0"),
    )
    SQLiteSnapshotRepository(connection).add(snapshot)
    position_snapshot = PositionSnapshot(
        snapshot_date=snapshot.snapshot_date,
        holding_id="one",
        symbol="2330",
        quantity=Decimal("1"),
        average_cost=Decimal("1"),
        close_price=Decimal("10"),
        cost_basis=Decimal("1"),
        market_value=Decimal("10"),
        unrealized_pnl=Decimal("9"),
        unrealized_return=Decimal("9"),
        portfolio_weight=Decimal("1"),
        daily_value_change=Decimal("0"),
        daily_return=None,
    )
    SQLitePositionSnapshotRepository(connection).add_many([position_snapshot])
    connection.execute(
        """CREATE TRIGGER fail_second_holding BEFORE UPDATE ON holdings
        WHEN NEW.symbol = '2027'
        BEGIN SELECT RAISE(FAIL, 'forced holding rollback'); END"""
    )
    use_case = ApplyRebuiltHoldingsUseCase(
        SQLiteHoldingRebuildUnitOfWork(connection), metadata("2330", "2027")
    )
    with pytest.raises(Exception, match="forced holding rollback"):
        use_case.execute(apply=True)
    loaded = {item.id: item for item in holdings.list_all()}
    assert loaded["one"].quantity == Decimal("1")
    assert loaded["two"].quantity == Decimal("1")
    assert (
        SQLiteSnapshotRepository(connection).get_by_date(snapshot.snapshot_date)
        == snapshot
    )
    assert SQLitePositionSnapshotRepository(connection).list_by_date(
        snapshot.snapshot_date
    ) == [position_snapshot]
