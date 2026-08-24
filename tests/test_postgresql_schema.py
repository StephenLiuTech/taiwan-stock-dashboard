"""Offline PostgreSQL schema-upgrade tests."""

from __future__ import annotations

import pytest

from database.postgresql import initialize_postgresql_schema


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
    assert not any(statement.startswith("UPDATE ") for statement in sql)
    assert any(parameters == (8,) for _, parameters in connection.statements)
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_current_postgresql_schema_does_not_reapply_v8_delta() -> None:
    connection = PostgreSQLConnectionStub(8)

    initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    migration_alters = [
        statement
        for statement, _ in connection.statements
        if "ADD COLUMN IF NOT EXISTS financing_type" in statement
        or "ADD COLUMN IF NOT EXISTS financed_symbol" in statement
        or "ADD COLUMN IF NOT EXISTS financed_quantity" in statement
    ]
    assert migration_alters == []
    assert connection.commits == 1


def test_postgresql_schema_upgrade_rolls_back_on_failure() -> None:
    connection = PostgreSQLConnectionStub(7, fail_on="financed_quantity")

    with pytest.raises(RuntimeError, match="injected migration failure"):
        initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_newer_postgresql_schema_is_rejected_without_commit() -> None:
    connection = PostgreSQLConnectionStub(9)

    with pytest.raises(RuntimeError, match="newer than supported"):
        initialize_postgresql_schema(connection)  # type: ignore[arg-type]

    assert connection.commits == 0
    assert connection.rollbacks == 1
