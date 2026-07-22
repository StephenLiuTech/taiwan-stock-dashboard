"""Shared real-SQLite test fixtures."""

import sqlite3
from collections.abc import Iterator

import pytest

from database import initialize_database, initialize_schema


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    """Provide an initialized in-memory SQLite database."""
    database = initialize_database("sqlite:///:memory:")
    initialize_schema(database)
    yield database
    database.close()
