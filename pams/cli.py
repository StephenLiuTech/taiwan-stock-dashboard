"""Argument parsing and command routing for PAMS."""

import argparse
import sqlite3
import sys
import traceback
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
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
from pams.application import AddTransactionCommand
from pams.composition import (
    compose_application,
    compose_demo_data,
    compose_ledger_operations,
    compose_operations,
)
from pams.reporting import (
    DailyReportBuilder,
    HtmlReportRenderer,
    MarkdownReportRenderer,
    format_demo_data_report,
    format_holding_change_plan,
    format_holding_change_plan_json,
    format_human_report,
    format_json_report,
    format_portfolio_valuation,
    format_status_report,
    format_transaction_list,
    format_transaction_record,
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


def parse_decimal(value: str) -> Decimal:
    """Parse an exact base-10 CLI value without binary floating point."""
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("value must be a decimal number") from error


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
    holdings = commands.add_parser("holdings", help="manage projected holdings")
    holding_commands = holdings.add_subparsers(dest="holdings_command", required=True)
    rebuild = holding_commands.add_parser(
        "rebuild", help="preview or apply transaction-derived holdings"
    )
    mode = rebuild.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    rebuild.add_argument("--allow-unmatched", action="store_true")
    rebuild.add_argument("--json", action="store_true", dest="json_output")
    rebuild.add_argument("--database", type=Path)
    rebuild.add_argument("--verbose", action="store_true")

    transaction = commands.add_parser(
        "transaction", help="record and list transactions"
    )
    transaction_commands = transaction.add_subparsers(
        dest="transaction_command", required=True
    )
    add = transaction_commands.add_parser("add", help="record one transaction")
    add.add_argument("--id", dest="transaction_id")
    add.add_argument("--symbol", required=True)
    add.add_argument("--market", required=True, choices=("TWSE", "TPEX"))
    add.add_argument("--type", required=True, choices=("buy", "sell"))
    add.add_argument("--trade-date", required=True, type=parse_iso_date)
    add.add_argument("--settlement-date", required=True, type=parse_iso_date)
    add.add_argument("--quantity", required=True, type=parse_decimal)
    add.add_argument("--price", required=True, type=parse_decimal)
    add.add_argument("--fees", type=parse_decimal, default=Decimal("0"))
    add.add_argument("--taxes", type=parse_decimal, default=Decimal("0"))
    add.add_argument("--currency", default="TWD", choices=("TWD", "USD"))
    add.add_argument("--notes")
    add.add_argument("--database", type=Path)
    add.add_argument("--verbose", action="store_true")
    listing = transaction_commands.add_parser("list", help="list transactions")
    listing.add_argument("--symbol")
    listing.add_argument("--from-date", type=parse_iso_date)
    listing.add_argument("--to-date", type=parse_iso_date)
    listing.add_argument("--json", action="store_true", dest="json_output")
    listing.add_argument("--database", type=Path)
    listing.add_argument("--verbose", action="store_true")
    portfolio = commands.add_parser("portfolio", help="query portfolio values")
    portfolio_commands = portfolio.add_subparsers(
        dest="portfolio_command", required=True
    )
    valuate = portfolio_commands.add_parser(
        "valuate", help="value holdings with latest persisted quotes"
    )
    valuate.add_argument("--json", action="store_true", dest="json_output")
    valuate.add_argument("--database", type=Path)
    valuate.add_argument("--verbose", action="store_true")
    report = commands.add_parser("report", help="generate portfolio reports")
    report_commands = report.add_subparsers(dest="report_command", required=True)
    generate = report_commands.add_parser(
        "generate", help="generate a daily portfolio report"
    )
    generate.add_argument("--html", action="store_true")
    generate.add_argument("--output", type=Path)
    generate.add_argument("--database", type=Path)
    generate.add_argument("--verbose", action="store_true")
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
        if arguments.command == "update":
            composer = compose_application
        elif (arguments.command == "holdings" and arguments.apply) or (
            arguments.command == "transaction"
            and arguments.transaction_command == "add"
        ):
            composer = compose_ledger_operations
        else:
            composer = compose_operations
        with composer(arguments.database, verbose=arguments.verbose) as application:
            if arguments.command == "report":
                assert application.valuate_portfolio is not None
                daily_report = DailyReportBuilder().build(
                    application.valuate_portfolio.execute()
                )
                renderer = (
                    HtmlReportRenderer() if arguments.html else MarkdownReportRenderer()
                )
                rendered = renderer.render(daily_report)
                if arguments.output:
                    arguments.output.write_text(rendered, encoding="utf-8")
                else:
                    sys.stdout.write(rendered)
                return int(ExitCode.SUCCESS)
            if arguments.command == "portfolio":
                assert application.valuate_portfolio is not None
                print(
                    format_portfolio_valuation(
                        application.valuate_portfolio.execute(),
                        json_output=arguments.json_output,
                    )
                )
                return int(ExitCode.SUCCESS)
            if arguments.command == "holdings":
                assert application.apply_rebuilt_holdings is not None
                plan = application.apply_rebuilt_holdings.execute(
                    apply=arguments.apply,
                    allow_unmatched_holdings=arguments.allow_unmatched,
                )
                print(
                    format_holding_change_plan_json(plan)
                    if arguments.json_output
                    else format_holding_change_plan(plan)
                )
                return int(ExitCode.SUCCESS)
            if arguments.command == "transaction":
                if arguments.transaction_command == "add":
                    assert application.add_transaction is not None
                    record = application.add_transaction.execute(
                        AddTransactionCommand(
                            symbol=arguments.symbol,
                            market=("TPEx" if arguments.market == "TPEX" else "TWSE"),
                            transaction_type=arguments.type,
                            trade_date=arguments.trade_date,
                            settlement_date=arguments.settlement_date,
                            quantity=arguments.quantity,
                            price=arguments.price,
                            fees=arguments.fees,
                            taxes=arguments.taxes,
                            currency=arguments.currency,
                            transaction_id=arguments.transaction_id,
                            notes=arguments.notes,
                        )
                    )
                    print(format_transaction_record(record))
                    return int(ExitCode.SUCCESS)
                assert application.list_transactions is not None
                result = application.list_transactions.execute(
                    symbol=arguments.symbol,
                    start_date=arguments.from_date,
                    end_date=arguments.to_date,
                )
                print(
                    format_transaction_list(result, json_output=arguments.json_output)
                )
                return int(ExitCode.SUCCESS)
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
