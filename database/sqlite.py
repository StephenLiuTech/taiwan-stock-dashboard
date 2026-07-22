"""SQLite database initialization."""

import sqlite3
from pathlib import Path

from core.constants import PROJECT_ROOT

SQLITE_URL_PREFIX = "sqlite:///"


def initialize_database(database_url: str) -> sqlite3.Connection:
    """Create a configured SQLite connection and its parent directory."""
    if not database_url.startswith(SQLITE_URL_PREFIX):
        raise ValueError("Only sqlite:/// database URLs are supported")

    raw_path = database_url.removeprefix(SQLITE_URL_PREFIX)
    if not raw_path:
        raise ValueError("SQLite database URL must include a path")

    if raw_path != ":memory:":
        database_path = Path(raw_path)
        if not database_path.is_absolute():
            database_path = PROJECT_ROOT / database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path = str(database_path)

    connection = sqlite3.connect(raw_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
