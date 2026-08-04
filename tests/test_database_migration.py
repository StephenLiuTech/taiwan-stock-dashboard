"""Offline tests for atomic database migration orchestration."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

import pams.application.migrate_database
from database import initialize_database, initialize_schema
from pams.application import DatabaseMigrationError, MigrateDatabaseUseCase


class HandleStub:
    def __init__(self, backend: str, connection: object, display_url: str) -> None:
        self.backend = backend
        self.connection = connection
        self.display_url = display_url

    def initialize_schema(self) -> None:
        return None


class FailingConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> object:
        if statement.startswith("INSERT INTO daily_snapshots"):
            raise RuntimeError("injected migration failure")
        return self.connection.execute(statement, parameters)

    def executemany(
        self, statement: str, parameters: list[tuple[object, ...]]
    ) -> object:
        if statement.startswith("INSERT INTO daily_snapshots"):
            raise RuntimeError("injected migration failure")
        return self.connection.executemany(statement, parameters)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        return None


class NoCloseConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def __getattr__(self, name: str) -> object:
        return getattr(self.connection, name)

    def close(self) -> None:
        return None


@pytest.fixture
def migration_connections(
    tmp_path: Path,
) -> Iterator[tuple[sqlite3.Connection, sqlite3.Connection]]:
    source = initialize_database(f"sqlite:///{(tmp_path / 'source.db').as_posix()}")
    destination = initialize_database(
        f"sqlite:///{(tmp_path / 'destination.db').as_posix()}"
    )
    initialize_schema(source)
    initialize_schema(destination)
    source.execute(
        """INSERT INTO liabilities
        VALUES ('liability-1', 'other', '100', NULL, 'TWD',
                NULL, NULL, NULL, NULL, '2026-07-27T00:00:00')"""
    )
    source.commit()
    try:
        yield source, destination
    finally:
        source.close()
        destination.close()


def _patch_handles(
    monkeypatch: pytest.MonkeyPatch,
    source: sqlite3.Connection,
    destination: object,
) -> None:
    handles = iter(
        (
            HandleStub("sqlite", NoCloseConnection(source), "sqlite:///source.db"),
            HandleStub("postgresql", destination, "postgresql://user@db/pams"),
        )
    )
    monkeypatch.setattr(
        pams.application.migrate_database,
        "open_database",
        lambda url: next(handles),
    )


def test_migration_copies_and_validates_every_table(
    monkeypatch: pytest.MonkeyPatch,
    migration_connections: tuple[sqlite3.Connection, sqlite3.Connection],
) -> None:
    source, destination = migration_connections
    _patch_handles(monkeypatch, source, NoCloseConnection(destination))
    result = MigrateDatabaseUseCase(
        "sqlite:///source.db", "postgresql://db/pams"
    ).execute()
    counts = dict(result.rows_copied)
    assert counts["liabilities"] == 1
    assert counts["schema_version"] == 6
    assert destination.execute("SELECT COUNT(*) FROM liabilities").fetchone()[0] == 1


def test_migration_rolls_back_all_tables_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    migration_connections: tuple[sqlite3.Connection, sqlite3.Connection],
) -> None:
    source, destination = migration_connections
    source.execute(
        """INSERT INTO daily_snapshots VALUES
        ('2026-07-27', '100', '90', '10', '0', '100', '0', '100', '0',
         '2026-07-27T00:00:00')"""
    )
    source.commit()
    _patch_handles(monkeypatch, source, FailingConnection(destination))
    with pytest.raises(DatabaseMigrationError, match="injected migration failure"):
        MigrateDatabaseUseCase("sqlite:///source.db", "postgresql://db/pams").execute()
    assert destination.execute("SELECT COUNT(*) FROM liabilities").fetchone()[0] == 0
    assert (
        destination.execute("SELECT COUNT(*) FROM daily_snapshots").fetchone()[0] == 0
    )
