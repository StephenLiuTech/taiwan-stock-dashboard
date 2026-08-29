import sqlite3
from pathlib import Path

import pytest

from database.schema import initialize_schema
from domain import (
    Currency,
    Holding,
    Liability,
    LiabilityPrincipalEvent,
    LiabilityPrincipalEventType,
    LiabilityType,
    Market,
    Transaction,
    TransactionType,
)
from pams.application import ReconcileBrokerUseCase
from pams.application.import_broker import (
    BrokerImportBlockedError,
    ImportBrokerUseCase,
)
from pams.brokerage import BrokerApplyStatus
from repositories import (
    SQLiteBrokerImportRecordRepository,
    SQLiteBrokerImportUnitOfWork,
    SQLiteHoldingRepository,
    SQLiteLiabilityPrincipalEventRepository,
    SQLiteLiabilityRepository,
    SQLiteTransactionRepository,
)
from services import TransactionEngine

SAFE = Path(__file__).parent / "fixtures" / "broker_future_safe_sanitized.csv"
BLOCKED = Path(__file__).parent / "fixtures" / "broker_future_incremental_sanitized.csv"
NON_TRADE = Path(__file__).parent / "fixtures" / "broker_non_trade_sanitized.csv"


def database() -> tuple[sqlite3.Connection, ImportBrokerUseCase]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    transactions = SQLiteTransactionRepository(connection)
    holdings = SQLiteHoldingRepository(connection)
    seed = Transaction(
        id="seed-2330",
        symbol="2330",
        market=Market.TWSE,
        transaction_type=TransactionType.BUY,
        trade_date="2026-01-01",
        settlement_date="2026-01-01",
        quantity="10",
        price="1000",
        currency=Currency.TWD,
    )
    transactions.add(seed)
    holdings.upsert(
        Holding(
            id="holding-2330",
            symbol="2330",
            name="Security A",
            market=Market.TWSE,
            currency=Currency.TWD,
            quantity="10",
            average_cost="1000",
        )
    )
    SQLiteLiabilityRepository(connection).upsert(
        Liability(
            id="liability-margin-financing",
            liability_type=LiabilityType.MARGIN_FINANCING,
            principal="100000",
            currency=Currency.TWD,
            financed_symbol="2027",
            financed_quantity="1000",
        )
    )
    SQLiteLiabilityPrincipalEventRepository(connection).insert_many_if_absent(
        [
            LiabilityPrincipalEvent(
                id="opening-margin",
                liability_id="liability-margin-financing",
                effective_date="2026-01-01",
                sequence=0,
                event_type=LiabilityPrincipalEventType.OPENING,
                principal_delta="100000",
                resulting_principal="100000",
                source="fixture",
            )
        ]
    )
    reconcile = ReconcileBrokerUseCase(holdings, transactions)
    return connection, ImportBrokerUseCase(
        reconcile,
        SQLiteBrokerImportUnitOfWork(connection),
        TransactionEngine(),
    )


def test_schema_13_contains_provenance_uniqueness() -> None:
    connection, _ = database()
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(broker_import_records)")
    }
    assert {
        "broker",
        "source_fingerprint",
        "source_row_reference",
        "domain_entity_type",
        "normalized_identity",
    } <= columns
    indexes = connection.execute("PRAGMA index_list(broker_import_records)").fetchall()
    assert any(row[2] for row in indexes)


def test_provenance_uniqueness_rejects_same_source_row_entity_type() -> None:
    connection, use_case = database()
    use_case.execute(SAFE, apply=True)
    record = SQLiteBrokerImportRecordRepository(connection).list_all()[0]
    duplicate = record.model_copy(update={"id": "different-id"})
    with pytest.raises(sqlite3.IntegrityError):
        SQLiteBrokerImportRecordRepository(connection).add(duplicate)


def test_dry_run_has_zero_writes_and_serializable_plan() -> None:
    connection, use_case = database()
    result = use_case.execute(SAFE, apply=False)
    assert result.plan.eligible_count == 4
    assert result.inserted_transactions == 0
    assert connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
    assert result.plan.items[0].proposed_entity_id is not None


def test_safe_cash_and_margin_rows_apply_atomically_and_idempotently() -> None:
    connection, use_case = database()
    first = use_case.execute(SAFE, apply=True)
    assert first.inserted_transactions == 4
    assert first.inserted_principal_events == 2
    assert first.inserted_provenance == 4
    assert connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 5
    liability = SQLiteLiabilityRepository(connection).get_by_id(
        "liability-margin-financing"
    )
    assert liability is not None
    assert liability.principal == 115000
    assert liability.financed_quantity == 1500
    assert len(SQLiteBrokerImportRecordRepository(connection).list_all()) == 4
    rows = SQLiteTransactionRepository(connection).list_all()
    cash_sell = next(
        item
        for item in rows
        if item.id.endswith("6bea") is False
        and item.trade_date.isoformat() == "2026-09-02"
    )
    assert cash_sell.fees == 5
    assert cash_sell.taxes == 10

    second = use_case.execute(SAFE, apply=True)
    assert second.inserted_transactions == 0
    assert second.inserted_principal_events == 0
    assert second.inserted_provenance == 0
    assert len(SQLiteTransactionRepository(connection).list_all()) == 5


def test_blocked_dependency_and_duplicate_source_prevent_all_writes() -> None:
    connection, use_case = database()
    plan = use_case.plan(BLOCKED)
    assert plan.blocked
    assert any(
        item.apply_status is BrokerApplyStatus.DEPENDENCY_BLOCKED for item in plan.items
    )
    with pytest.raises(BrokerImportBlockedError):
        use_case.execute(BLOCKED, apply=True)
    assert connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
    assert (
        connection.execute("SELECT COUNT(*) FROM broker_import_records").fetchone()[0]
        == 0
    )


def test_dividend_corporate_action_and_financing_rows_are_reconciliation_only() -> None:
    connection, use_case = database()
    plan = use_case.plan(NON_TRADE)
    assert plan.blocked
    assert plan.eligible_count == 0
    with pytest.raises(BrokerImportBlockedError):
        use_case.execute(NON_TRADE, apply=True)
    assert connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_overlapping_file_with_new_fingerprint_does_not_duplicate(
    tmp_path: Path,
) -> None:
    connection, use_case = database()
    use_case.execute(SAFE, apply=True)
    overlapping = tmp_path / "overlap.csv"
    overlapping.write_bytes(SAFE.read_bytes() + b"\r\n")
    result = use_case.execute(overlapping, apply=True)
    assert (
        result.plan.reconciliation.source_fingerprint
        != use_case.plan(SAFE).reconciliation.source_fingerprint
    )
    assert result.inserted_transactions == 0
    assert (
        connection.execute("SELECT COUNT(*) FROM broker_import_records").fetchone()[0]
        == 4
    )


def test_provenance_failure_rolls_back_domain_writes() -> None:
    connection, use_case = database()

    def fail(_record: object) -> None:
        raise RuntimeError("provenance failure")

    use_case.unit.broker_import_records.add = fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="provenance failure"):
        use_case.execute(SAFE, apply=True)
    assert connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM liability_principal_events"
        ).fetchone()[0]
        == 1
    )
    assert (
        connection.execute("SELECT COUNT(*) FROM broker_import_records").fetchone()[0]
        == 0
    )


def test_domain_replay_failure_rolls_back_transaction_and_provenance(
    tmp_path: Path,
) -> None:
    connection, use_case = database()
    source = tmp_path / "oversell.csv"
    source.write_text(
        "股名,日期,成交股數,淨收付,成交單價,成交價金,手續費,交易稅,稅款,委託書號,幣別,備註\n"
        "台積電,2026/09/01,20,19900,1000,20000,10,90,0,oversell,台幣,現股賣出\n",
        encoding="utf-8-sig",
    )
    with pytest.raises(ValueError):
        use_case.execute(source, apply=True)
    assert connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
    assert (
        connection.execute("SELECT COUNT(*) FROM broker_import_records").fetchone()[0]
        == 0
    )
