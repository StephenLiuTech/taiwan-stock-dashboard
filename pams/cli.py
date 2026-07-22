"""Argument parsing and command routing for PAMS."""

import argparse
import sqlite3
import sys
import traceback
from collections.abc import Sequence
from datetime import date
from enum import IntEnum
from pathlib import Path

from config.yaml_loader import ConfigurationError
from market_data import (
    ProviderDataError,
    SourceDateError,
    SuspendedSecurityError,
    SymbolNotFoundError,
)
from pams.composition import compose_application
from pams.reporting import format_human_report, format_json_report
from services import DuplicateSnapshotError


class ExitCode(IntEnum):
    """Stable process exit-code policy."""

    SUCCESS = 0
    INTERNAL_ERROR = 1
    CLI_ERROR = 2
    PROVIDER_ERROR = 3
    SOURCE_DATE_ERROR = 4
    SECURITY_ERROR = 5
    DUPLICATE_SNAPSHOT = 6
    CONFIG_OR_DATABASE_ERROR = 7


def parse_iso_date(value: str) -> date:
    """Parse an exact ISO calendar date for argparse."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the PAMS command parser without side effects."""
    parser = argparse.ArgumentParser(prog="pams")
    commands = parser.add_subparsers(dest="command", required=True)
    update = commands.add_parser("update", help="fetch and value official market data")
    update.add_argument("--date", required=True, type=parse_iso_date)
    update.add_argument("--database", type=Path)
    update.add_argument("--dry-run", action="store_true")
    update.add_argument("--json", action="store_true", dest="json_output")
    update.add_argument("--verbose", action="store_true")
    return parser


def _error_exit_code(error: Exception) -> ExitCode:
    if isinstance(error, SourceDateError):
        return ExitCode.SOURCE_DATE_ERROR
    if isinstance(error, ProviderDataError):
        return ExitCode.PROVIDER_ERROR
    if isinstance(error, (SymbolNotFoundError, SuspendedSecurityError)):
        return ExitCode.SECURITY_ERROR
    if isinstance(error, DuplicateSnapshotError):
        return ExitCode.DUPLICATE_SNAPSHOT
    if isinstance(error, (ConfigurationError, sqlite3.Error, OSError, ValueError)):
        return ExitCode.CONFIG_OR_DATABASE_ERROR
    return ExitCode.INTERNAL_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    """Run a PAMS command and return its explicit process exit code."""
    arguments = build_parser().parse_args(argv)
    try:
        with compose_application(
            arguments.database, verbose=arguments.verbose
        ) as application:
            result = (
                application.engine.preview(arguments.date)
                if arguments.dry_run
                else application.engine.refresh(arguments.date)
            )
            report = (
                format_json_report(
                    result,
                    arguments.date,
                    application.database_path,
                    dry_run=arguments.dry_run,
                )
                if arguments.json_output
                else format_human_report(
                    result,
                    arguments.date,
                    application.database_path,
                    dry_run=arguments.dry_run,
                )
            )
            print(report)
            return int(ExitCode.SUCCESS)
    except Exception as error:
        print(f"pams: {error}", file=sys.stderr)
        if arguments.verbose:
            traceback.print_exc()
        return int(_error_exit_code(error))
