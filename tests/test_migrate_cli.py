"""Database migration CLI tests."""

import pytest

from pams.application import DatabaseMigrationResult
from pams.cli import ExitCode, main


class MigrationStub:
    def execute(self) -> DatabaseMigrationResult:
        return DatabaseMigrationResult(
            "sqlite:///source.db",
            "postgresql://user@localhost/pams",
            (("holdings", 2), ("daily_snapshots", 1)),
            0.125,
        )


def test_migrate_cli_renders_operational_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("pams.cli.compose_database_migration", lambda: MigrationStub())
    exit_code = main(["migrate"])
    output = capsys.readouterr().out
    assert exit_code == ExitCode.SUCCESS
    assert "Source database: sqlite:///source.db" in output
    assert "Destination database: postgresql://user@localhost/pams" in output
    assert "Rows copied: 3" in output
    assert "Result: success" in output


def test_migrate_cli_failure_is_explicit_and_redacts_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from config import get_settings

    monkeypatch.setenv("PAMS_MIGRATION_SOURCE_URL", "sqlite:///source.db")
    monkeypatch.setenv("PAMS_DATABASE_URL", "postgresql://user:secret@localhost/pams")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "pams.cli.compose_database_migration",
        lambda: (_ for _ in ()).throw(RuntimeError("failed")),
    )
    try:
        exit_code = main(["migrate"])
        output = capsys.readouterr().err
        assert exit_code == ExitCode.INTERNAL_ERROR
        assert "Source database: sqlite:///source.db" in output
        assert "Destination database: postgresql://user@localhost/pams" in output
        assert "Result: failure" in output
        assert "secret" not in output
    finally:
        get_settings.cache_clear()
