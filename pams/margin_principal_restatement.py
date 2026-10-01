"""Read-only preview and guarded atomic apply for margin principal correction."""

import argparse
import json
from pathlib import Path

from config import get_settings
from database.provider import open_database
from database.schema import SCHEMA_VERSION
from pams.application.margin_principal_correction import (
    MarginPrincipalCorrectionUseCase,
    approved_2027_request,
)
from pams.lot_restatement import _sqlite_clone, _verified_backup
from repositories.provider import create_repositories


def _view(value: object) -> dict:
    return value.model_dump(mode="json")


def _pairs(values: tuple) -> list[dict]:
    return [
        {"before": _view(before), "after": _view(after)} for before, after in values
    ]


def _summary(plan: object) -> dict:
    return {
        "fingerprint": plan.fingerprint,
        "already_applied": plan.already_applied,
        "counts": {
            "insert_events": len(plan.event_inserts),
            "update_events": len(plan.event_updates),
            "master": int(plan.master[0] != plan.master[1]),
            "daily_snapshots": len(plan.daily),
            "stock_net_equity_history": len(plan.history),
            "interest_events": len(plan.interest),
            "annual_snapshots": len(plan.annual),
            "position_snapshots": 0,
        },
        "event_updates": _pairs(plan.event_updates),
        "event_inserts": [_view(item) for item in plan.event_inserts],
        "master": {
            "before": _view(plan.master[0]),
            "after": _view(plan.master[1]),
        },
        "daily_updates": _pairs(plan.daily),
        "history_updates": _pairs(plan.history),
        "interest_updates": _pairs(plan.interest),
        "annual_updates": _pairs(plan.annual),
        "position_fingerprint": plan.position_fingerprint,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pams.margin_principal_restatement")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--reviewed-fingerprint")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--backup-sha256")
    args = parser.parse_args(argv)
    if args.apply and not (
        args.reviewed_fingerprint and args.backup and args.backup_sha256
    ):
        parser.error("--apply requires reviewed fingerprint and verified backup")
    configured = get_settings().database_url
    url = (
        configured.get_secret_value()
        if hasattr(configured, "get_secret_value")
        else str(configured)
    )
    target = open_database(url)
    try:
        if target.backend != "postgresql":
            raise ValueError("controlled correction requires PostgreSQL")
        request = approved_2027_request()
        if args.dry_run:
            target.connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
        version = target.connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        if version != SCHEMA_VERSION:
            raise ValueError(f"expected schema v{SCHEMA_VERSION}, found v{version}")
        if args.dry_run:
            local = _sqlite_clone(target.connection)
            target.connection.rollback()
            try:
                repos = create_repositories("sqlite", local)
                plan = MarginPrincipalCorrectionUseCase(local, repos).preview(request)
            finally:
                local.close()
        else:
            _verified_backup(args.backup, args.backup_sha256)
            target.connection.rollback()
            repos = create_repositories("postgresql", target.connection)
            plan = MarginPrincipalCorrectionUseCase(target.connection, repos).apply(
                request, reviewed_fingerprint=args.reviewed_fingerprint
            )
        print(json.dumps(_summary(plan), ensure_ascii=True, indent=2))
        return 0
    finally:
        target.connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
