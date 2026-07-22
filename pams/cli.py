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
from core.constants import PROJECT_ROOT
from market_data import (
    ProviderDataError,
    SourceDateError,
    SuspendedSecurityError,
    SymbolNotFoundError,
)
from pams.composition import compose_application, compose_demo_data, compose_operations
from pams.reporting import (
    format_demo_data_report,
    format_human_report,
    format_json_report,
    format_status_report,
    format_verification_report,
)
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
    VERIFICATION_FAILED = 8


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
    update.add_argument("--date", type=parse_iso_date)
    update.add_argument("--database", type=Path)
    update.add_argument("--dry-run", action="store_true")
    update.add_argument("--json", action="store_true", dest="json_output")
    update.add_argument("--verbose", action="store_true")
    demo = commands.add_parser("demo-data", help="create an isolated demo database")
    demo.add_argument(
        "--database", type=Path, default=PROJECT_ROOT / "data" / "pams_demo.db"
    )
    demo.add_argument("--verbose", action="store_true")
    for command_name in ("status", "verify"):
        command = commands.add_parser(command_name)
        command.add_argument("--database", type=Path)
        command.add_argument("--verbose", action="store_true")
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
        if arguments.command == "demo-data":
            result = compose_demo_data().execute(arguments.database)
            print(format_demo_data_report(result))
            return int(ExitCode.SUCCESS)
        composer = (
            compose_application if arguments.command == "update" else compose_operations
        )
        with composer(arguments.database, verbose=arguments.verbose) as application:
            if arguments.command == "status":
                print(format_status_report(application.portfolio_status.execute()))
                return int(ExitCode.SUCCESS)
            if arguments.command == "verify":
                verification = application.verify_system.execute()
                print(format_verification_report(verification))
                return int(
                    ExitCode.VERIFICATION_FAILED
                    if verification.failed
                    else ExitCode.SUCCESS
                )
            result = application.update_portfolio.execute(
                arguments.date, dry_run=arguments.dry_run
            )
            report = (
                format_json_report(result)
                if arguments.json_output
                else format_human_report(result)
            )
            print(report)
            return int(ExitCode.SUCCESS)
    except Exception as error:
        print(f"pams: {error}", file=sys.stderr)
        if arguments.verbose:
            traceback.print_exc()
        return int(_error_exit_code(error))
