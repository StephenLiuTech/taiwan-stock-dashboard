"""Offline PostgreSQL schema-upgrade tests."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from database.postgresql import PostgreSQLConnection, initialize_postgresql_schema
from repositories.postgresql import (
    PostgreSQLHoldingRepository,
    PostgreSQLLiabilityRepository,
    PostgreSQLMarginFinancingPositionRepository,
    PostgreSQLTransactionRepository,
)


class CursorStub:
    def __init__(self, row: tuple[object, ...] | None = None) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


class PostgreSQLConnectionStub:
    def __init__(self, version: int, fail_on: str | None = None) -> None:
        self.version = version
        self.fail_on = fail_on
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(
        self, statement: str, parameters: tuple[object, ...] = ()
    ) -> CursorStub:
        normalized = " ".join(statement.split())
        self.statements.append((normalized, parameters))
        if self.fail_on and self.fail_on in normalized:
            raise RuntimeError("injected migration failure")
        if normalized == "SELECT MAX(version) FROM schema_version":
            return CursorStub((self.version,))
        return CursorStub()

    def executemany(
        self, statement: str, parameters: list[tuple[object, ...]]
    ) -> CursorStub:
        for values in parameters:
            self.execute(statement, values)
        return CursorStub()

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


_NOT_SUPPLIED = object()


class StrictPsycopgCursor:
    """Reject the same malformed percent placeholders as Psycopg."""

    def __init__(self, owner: StrictPsycopgConnection) -> None:
        self.owner = owner
        self.description = [SimpleNamespace(name="max")]
        self.rowcount = 0
        self.rows: list[tuple[object, ...]] = []

    def execute(
        self,
        statement: str,
        parameters: object = _NOT_SUPPLIED,
    ) -> None:
        if parameters is not _NOT_SUPPLIED and re.search(r"%(?![sbt%])", statement):
            raise ValueError("invalid Psycopg percent placeholder")
        self.owner.statements.append((statement, parameters))
        self.rows = (
            [(self.owner.schema_version,)]
            if "SELECT MAX(version) FROM schema_version" in statement
            else []
        )

    def executemany(self, statement: str, parameters: list[tuple[object, ...]]) -> None:
        for values in parameters:
            self.execute(statement, values)

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class StrictPsycopgConnection:
    def __init__(self, schema_version: int = 15) -> None:
        self.schema_version = schema_version
        self.statements: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> StrictPsycopgCursor:
        return StrictPsycopgCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_postgresql_schema_7_to_8_is_nullable_and_version_gated() -> None:
    connection = PostgreSQLConnectionStub(7)

    initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    sql = [statement for statement, _ in connection.statements]
    assert any(
        "ADD COLUMN IF NOT EXISTS financing_type TEXT CHECK "
        "(financing_type IS NULL OR financing_type = 'margin')" in statement
        for statement in sql
    )
    assert any(
        "ADD COLUMN IF NOT EXISTS financed_symbol TEXT" in statement
        for statement in sql
    )
    assert any(
        "ADD COLUMN IF NOT EXISTS financed_quantity TEXT" in statement
        for statement in sql
    )
    assert not any(
        statement.startswith("UPDATE transactions")
        and "broker_order" not in statement
        and "financing_type = 'margin'" not in statement
        for statement in sql
    )
    assert any(parameters == (8,) for _, parameters in connection.statements)
    assert any(parameters == (9,) for _, parameters in connection.statements)
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_schema_8_to_9_adds_only_annual_pnl_tables() -> None:
    connection = PostgreSQLConnectionStub(8)

    initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    migration_alters = [
        statement
        for statement, _ in connection.statements
        if "ADD COLUMN IF NOT EXISTS financing_type" in statement
        or "ADD COLUMN IF NOT EXISTS financed_symbol" in statement
        or "ADD COLUMN IF NOT EXISTS financed_quantity TEXT" in statement
    ]
    assert migration_alters == []
    sql = [statement for statement, _ in connection.statements]
    assert any(
        "CREATE TABLE IF NOT EXISTS investment_cost_events" in item for item in sql
    )
    assert any(
        "CREATE TABLE IF NOT EXISTS annual_pnl_snapshots" in item for item in sql
    )
    assert any(parameters == (9,) for _, parameters in connection.statements)
    assert connection.commits == 1


def test_schema_9_to_10_adds_only_corporate_actions() -> None:
    connection = PostgreSQLConnectionStub(9)

    initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    sql = [statement for statement, _ in connection.statements]
    assert any("CREATE TABLE IF NOT EXISTS corporate_actions" in item for item in sql)
    assert any("ix_corporate_actions_symbol_date" in item for item in sql)
    assert not any(
        "ALTER TABLE transactions" in item and "broker_order" not in item
        for item in sql
    )
    assert any(parameters == (10,) for _, parameters in connection.statements)
    assert connection.commits == 1


def test_schema_10_to_11_adds_only_liability_principal_ledger() -> None:
    connection = PostgreSQLConnectionStub(10)

    initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    sql = [statement for statement, _ in connection.statements]
    assert any(
        "CREATE TABLE IF NOT EXISTS liability_principal_events" in item for item in sql
    )
    assert any("ix_liability_principal_events_replay" in item for item in sql)
    assert not any(
        "ALTER TABLE transactions" in item and "broker_order" not in item
        for item in sql
    )
    assert any("ALTER TABLE annual_pnl_snapshots" in item for item in sql)
    assert any(parameters == (11,) for _, parameters in connection.statements)
    assert connection.commits == 1


def test_schema_11_to_12_adds_and_backfills_valuation_date() -> None:
    connection = PostgreSQLConnectionStub(11)

    initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    sql = [statement for statement, _ in connection.statements]
    assert any("ADD COLUMN IF NOT EXISTS valuation_date TEXT" in item for item in sql)
    assert any("MAX(d.snapshot_date)" in item for item in sql)
    assert any("ALTER COLUMN valuation_date SET NOT NULL" in item for item in sql)
    assert any(parameters == (12,) for _, parameters in connection.statements)
    assert connection.commits == 1


def test_schema_12_to_13_adds_only_broker_provenance() -> None:
    connection = PostgreSQLConnectionStub(12)

    initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    sql = [statement for statement, _ in connection.statements]
    assert any(
        "CREATE TABLE IF NOT EXISTS broker_import_records" in item for item in sql
    )
    assert any("ix_broker_import_domain_entity" in item for item in sql)
    assert not any(
        "ALTER TABLE transactions" in item and "broker_order" not in item
        for item in sql
    )
    assert not any("annual_pnl_snapshots" in item for item in sql)
    assert any(parameters == (13,) for _, parameters in connection.statements)
    assert connection.commits == 1


def test_postgresql_schema_upgrade_rolls_back_on_failure() -> None:
    connection = PostgreSQLConnectionStub(7, fail_on="financed_quantity")

    with pytest.raises(RuntimeError, match="injected migration failure"):
        initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_schema_13_to_14_adds_only_stock_net_equity_history() -> None:
    connection = PostgreSQLConnectionStub(13)

    initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    sql = [statement for statement, _ in connection.statements]
    assert any(
        "CREATE TABLE IF NOT EXISTS stock_net_equity_history" in item for item in sql
    )
    assert any("ix_stock_net_equity_history_quality_date" in item for item in sql)
    assert any(parameters == (14,) for _, parameters in connection.statements)
    assert connection.commits == 1


def test_newer_postgresql_schema_is_rejected_without_commit() -> None:
    connection = PostgreSQLConnectionStub(17)

    with pytest.raises(RuntimeError, match="newer than supported"):
        initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_schema_14_to_15_adds_only_lot_allocation_ledger() -> None:
    connection = PostgreSQLConnectionStub(14)

    initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    sql = [statement for statement, _ in connection.statements]
    assert any("CREATE TABLE IF NOT EXISTS lot_allocations" in item for item in sql)
    assert any("ix_lot_allocations_buy" in item for item in sql)
    assert not any(
        "UPDATE transactions" in item
        and "broker_order" not in item
        and "financing_type = 'margin'" not in item
        for item in sql
    )
    assert any(parameters == (15,) for _, parameters in connection.statements)
    assert connection.commits == 1


def test_schema_14_to_15_failure_rolls_back() -> None:
    connection = PostgreSQLConnectionStub(
        14, fail_on="CREATE TABLE IF NOT EXISTS lot_allocations"
    )
    with pytest.raises(RuntimeError, match="injected migration failure"):
        initialize_postgresql_schema(connection)  # type: ignore[arg-type]
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_schema_15_to_16_adds_multi_symbol_margin_tracking() -> None:
    connection = PostgreSQLConnectionStub(15)

    initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    sql = [statement for statement, _ in connection.statements]
    assert any(
        "CREATE TABLE IF NOT EXISTS margin_financing_positions" in item for item in sql
    )
    assert any("ADD COLUMN IF NOT EXISTS broker_order TEXT" in item for item in sql)
    assert any("ux_transactions_broker_order" in item for item in sql)
    assert any(
        "ADD COLUMN IF NOT EXISTS margin_position_id TEXT" in item for item in sql
    )
    assert any(
        "ADD COLUMN IF NOT EXISTS financed_quantity_delta TEXT" in item for item in sql
    )
    assert any(
        "INSERT INTO margin_financing_positions" in item and "l.financed_symbol" in item
        for item in sql
    )
    assert any("transaction_cost_pool_openings" in item for item in sql)
    assert any(parameters == (16,) for _, parameters in connection.statements)
    legacy_margin_updates = [
        parameters
        for statement, parameters in connection.statements
        if statement == "UPDATE transactions SET financing_type = 'margin' WHERE id = ?"
    ]
    assert len(legacy_margin_updates) == 1
    opening_inserts = [
        parameters
        for statement, parameters in connection.statements
        if statement.startswith("INSERT INTO transaction_cost_pool_openings VALUES")
    ]
    assert len(opening_inserts) == 3
    assert connection.commits == 1


def test_psycopg_literal_percent_without_parameters_is_not_parsed() -> None:
    raw = StrictPsycopgConnection()
    connection = PostgreSQLConnection(raw)

    connection.execute("SELECT 1 WHERE 'Broker order AX494' LIKE 'Broker order %'")
    connection.execute("SELECT 1 WHERE 'AX494' LIKE ?", ("%AX494%",))

    assert raw.statements[0][1] is _NOT_SUPPLIED
    assert raw.statements[1][0].endswith("LIKE %s")
    assert raw.statements[1][1] == ("%AX494%",)


def test_shared_cli_startup_and_repository_queries_are_placeholder_safe() -> None:
    """Cover verify startup, holdings/transaction list, and margin reads."""
    raw = StrictPsycopgConnection(schema_version=15)
    connection = PostgreSQLConnection(raw)

    initialize_postgresql_schema(connection)
    assert PostgreSQLHoldingRepository(connection).list_all() == []
    assert PostgreSQLTransactionRepository(connection).list_all() == []
    assert PostgreSQLLiabilityRepository(connection).list_all() == []
    assert PostgreSQLMarginFinancingPositionRepository(connection).list_all() == []

    pattern_updates = [
        item
        for item in raw.statements
        if "UPDATE transactions SET broker_order" in item[0]
    ]
    assert len(pattern_updates) == 1
    assert "notes LIKE %s" in pattern_updates[0][0]
    assert pattern_updates[0][1] == ("Broker order %",)
    assert raw.commits == 1
    assert raw.rollbacks == 0
