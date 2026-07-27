"""Database-provider selection tests."""

from pathlib import Path

import pytest

import database.provider
from database.provider import (
    database_url_for_override,
    open_database,
    redact_database_url,
)
from repositories.postgresql import PostgreSQLHoldingRepository
from repositories.provider import create_repositories
from repositories.sqlite import SQLiteHoldingRepository


def test_sqlite_url_selects_sqlite_repositories(tmp_path: Path) -> None:
    handle = open_database(f"sqlite:///{(tmp_path / 'provider.db').as_posix()}")
    try:
        handle.initialize_schema()
        bundle = create_repositories(handle.backend, handle.connection)
        assert handle.backend == "sqlite"
        assert isinstance(bundle.holdings, SQLiteHoldingRepository)
    finally:
        handle.connection.close()


def test_postgresql_url_selects_postgresql_repositories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = object()
    monkeypatch.setattr(
        database.provider, "initialize_postgresql_database", lambda url: connection
    )
    handle = open_database("postgresql://user:secret@db.example/pams")
    bundle = create_repositories(handle.backend, handle.connection)
    assert handle.backend == "postgresql"
    assert isinstance(bundle.holdings, PostgreSQLHoldingRepository)
    assert "secret" not in handle.display_url


def test_path_override_explicitly_selects_sqlite(tmp_path: Path) -> None:
    override = tmp_path / "override.db"
    result = database_url_for_override(override, "postgresql://db/pams")
    assert result == f"sqlite:///{override.resolve().as_posix()}"


def test_database_url_rejects_unknown_scheme() -> None:
    with pytest.raises(ValueError, match="sqlite:/// or postgresql://"):
        open_database("mysql://localhost/pams")


def test_postgresql_display_url_redacts_password() -> None:
    safe = redact_database_url(
        "postgresql://portfolio:super-secret@localhost:5432/pams?sslmode=require"
    )
    assert safe == "postgresql://portfolio@localhost:5432/pams?sslmode=require"
    assert "super-secret" not in safe
