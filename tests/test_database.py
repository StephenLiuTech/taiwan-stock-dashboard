"""Smoke tests for local database initialization."""

import sqlite3
from pathlib import Path

from database import initialize_database, initialize_schema


def test_database_initialization_creates_usable_sqlite_database(tmp_path: Path) -> None:
    """Initialization creates a database with foreign keys enabled."""
    database_path = tmp_path / "nested" / "pams.db"

    with initialize_database(f"sqlite:///{database_path.as_posix()}") as connection:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
        connection.execute("CREATE TABLE smoke_test (id INTEGER PRIMARY KEY)")

    assert database_path.is_file()
    assert foreign_keys[0] == 1
    with sqlite3.connect(database_path) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    assert ("smoke_test",) in tables


def test_schema_initialization_creates_market_data_version(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "schema.db"
    with initialize_database(f"sqlite:///{database_path.as_posix()}") as connection:
        initialize_schema(connection)
        version = connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert version[0] == 2
    assert {"price_quotes", "daily_snapshots", "position_snapshots"} <= tables
