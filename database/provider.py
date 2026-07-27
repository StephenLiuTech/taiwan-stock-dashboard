"""Database-provider selection driven by ``PAMS_DATABASE_URL``."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from core.constants import PROJECT_ROOT
from database.postgresql import (
    PostgreSQLConnection,
    initialize_postgresql_database,
    initialize_postgresql_schema,
)
from database.schema import initialize_schema
from database.sqlite import SQLITE_URL_PREFIX, initialize_database

DatabaseBackend = Literal["sqlite", "postgresql"]


@dataclass(frozen=True)
class DatabaseHandle:
    """One selected database connection and safe display metadata."""

    url: str
    backend: DatabaseBackend
    connection: sqlite3.Connection | PostgreSQLConnection
    location: Path
    display_url: str

    def initialize_schema(self) -> None:
        if self.backend == "sqlite":
            initialize_schema(self.connection)  # type: ignore[arg-type]
        else:
            initialize_postgresql_schema(self.connection)  # type: ignore[arg-type]

    def exists(self) -> bool:
        return self.backend == "postgresql" or self.location.exists()

    def size_bytes(self) -> int:
        if self.backend == "sqlite":
            return self.location.stat().st_size if self.location.exists() else 0
        row = self.connection.execute(
            "SELECT pg_database_size(current_database())"
        ).fetchone()
        return int(row[0]) if row else 0


def resolve_sqlite_path(database_url: str) -> Path:
    """Resolve a file-backed SQLite URL."""
    if not database_url.startswith(SQLITE_URL_PREFIX):
        raise ValueError("SQLite database URL must start with sqlite:///")
    raw_path = database_url.removeprefix(SQLITE_URL_PREFIX)
    if not raw_path or raw_path == ":memory:":
        raise ValueError("CLI requires a file-backed SQLite database")
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def redact_database_url(database_url: str) -> str:
    """Remove credentials from a database URL used in operational output."""
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgresql", "postgres"}:
        return database_url
    hostname = parsed.hostname or ""
    if parsed.port:
        hostname = f"{hostname}:{parsed.port}"
    netloc = f"{parsed.username}@{hostname}" if parsed.username else hostname
    return urlunsplit(("postgresql", netloc, parsed.path, parsed.query, ""))


def open_database(database_url: str) -> DatabaseHandle:
    """Select and connect to SQLite or PostgreSQL from its URL scheme."""
    if database_url.startswith(SQLITE_URL_PREFIX):
        path = resolve_sqlite_path(database_url)
        return DatabaseHandle(
            database_url,
            "sqlite",
            initialize_database(database_url),
            path,
            f"sqlite:///{path.as_posix()}",
        )
    if database_url.startswith(("postgresql://", "postgres://")):
        return DatabaseHandle(
            database_url,
            "postgresql",
            initialize_postgresql_database(database_url),
            Path("PostgreSQL"),
            redact_database_url(database_url),
        )
    raise ValueError("PAMS_DATABASE_URL must use sqlite:/// or postgresql://")


def database_url_for_override(override: Path | None, configured_url: str) -> str:
    """Keep the legacy path override as an explicit SQLite test/local adapter."""
    if override is None:
        return configured_url
    return f"sqlite:///{override.expanduser().resolve().as_posix()}"
