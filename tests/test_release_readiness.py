"""Release-critical packaging and first-run behavior."""

import tomllib
from pathlib import Path

import pytest

import pams
from database import initialize_database, initialize_schema
from pams.cli import ExitCode, main


def test_release_version_is_consistent() -> None:
    project = Path(__file__).parents[1]
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == pams.__version__ == "1.0.0"


def test_cli_exposes_release_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as captured:
        main(["--version"])
    assert captured.value.code == 0
    assert capsys.readouterr().out.strip() == "pams 1.0.0"


def test_missing_database_is_controlled_and_not_created(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "missing.db"
    result = main(["portfolio", "valuate", "--database", str(database)])
    captured = capsys.readouterr()
    assert result == int(ExitCode.CONFIG_OR_DATABASE_ERROR)
    assert "Database does not exist" in captured.err
    assert "demo-data" in captured.err
    assert "Traceback" not in captured.err
    assert not database.exists()


def test_initialized_empty_database_reports_missing_holdings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "empty.db"
    connection = initialize_database(f"sqlite:///{database.as_posix()}")
    initialize_schema(connection)
    connection.close()
    result = main(["portfolio", "valuate", "--database", str(database)])
    captured = capsys.readouterr()
    assert result == int(ExitCode.SECURITY_ERROR)
    assert "No portfolio holdings are available" in captured.err
    assert "Market Value: 0" not in captured.out
    assert "Traceback" not in captured.err


def test_initialized_empty_database_reports_missing_analytics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "empty-analytics.db"
    connection = initialize_database(f"sqlite:///{database.as_posix()}")
    initialize_schema(connection)
    connection.close()
    result = main(["analytics", "portfolio", "--database", str(database)])
    captured = capsys.readouterr()
    assert result == int(ExitCode.CONFIG_OR_DATABASE_ERROR)
    assert "No portfolio snapshots" in captured.err
    assert "Traceback" not in captured.err
