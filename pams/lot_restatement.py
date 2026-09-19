"""Isolated lot-correction preview and explicitly guarded atomic apply CLI.

This module intentionally bypasses normal application composition: composition
initializes the configured schema, which would migrate production during preview.
"""

import argparse
import hashlib
import json
import sqlite3
import zipfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from config import get_settings
from database.provider import open_database
from database.schema import SCHEMA_VERSION, initialize_schema
from domain import LotAllocation
from pams.application.lot_correction import (
    BuyFeeCorrection,
    LotCorrectionRequest,
    LotCorrectionUseCase,
)
from repositories.provider import create_repositories


def _request(path: Path) -> LotCorrectionRequest:
    source = json.loads(path.read_text(encoding="utf-8"))
    fees = tuple(
        BuyFeeCorrection(
            item["transaction_id"],
            Decimal(str(item["expected_fee"])),
            Decimal(str(item["corrected_fee"])),
        )
        for item in source["buy_fees"]
    )
    allocations = tuple(
        LotAllocation.model_validate(
            {
                **item,
                "matched_quantity": Decimal(str(item["matched_quantity"])),
                "matched_trade_cost": Decimal(str(item["matched_trade_cost"])),
                "allocated_buy_fee": Decimal(str(item["allocated_buy_fee"])),
            }
        )
        for item in source["allocations"]
    )
    return LotCorrectionRequest(source["sell_transaction_id"], fees, allocations)


def _sqlite_clone(production: object) -> sqlite3.Connection:
    """Copy a repeatable-read PostgreSQL view into an isolated v15 memory DB."""
    local = sqlite3.connect(":memory:")
    local.row_factory = sqlite3.Row
    initialize_schema(local)
    local.execute("PRAGMA foreign_keys=OFF")
    tables = [
        row[0]
        for row in local.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT IN ('schema_version', 'lot_allocations')"
        ).fetchall()
    ]
    available = {
        row[0]
        for row in production.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE'"
        ).fetchall()
    }
    for table in tables:
        if table not in available:
            continue
        columns = [
            row[1] for row in local.execute(f"PRAGMA table_info({table})").fetchall()
        ]
        remote_columns = {
            row[0]
            for row in production.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=?",
                (table,),
            ).fetchall()
        }
        columns = [name for name in columns if name in remote_columns]
        if not columns:
            continue
        names = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        rows = production.execute(f"SELECT {names} FROM {table}").fetchall()
        try:
            local.executemany(
                f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
                [
                    tuple(
                        value.isoformat() if isinstance(value, datetime) else value
                        for value in (row[name] for name in columns)
                    )
                    for row in rows
                ],
            )
        except sqlite3.IntegrityError as error:
            raise ValueError(f"Isolated clone rejected {table}: {error}") from error
    local.commit()
    local.execute("PRAGMA foreign_keys=ON")
    return local


def _summary(plan: object) -> dict:
    def view(value: object) -> dict:
        return value.model_dump(mode="json")

    return {
        "fingerprint": plan.fingerprint,
        "already_applied": plan.already_applied,
        "transaction_updates": [
            {"before": view(before), "after": view(after)}
            for before, after in plan.transactions
        ],
        "allocation_inserts": [view(item) for item in plan.allocations],
        "holding": {
            "before": view(plan.holding[0]),
            "after": view(plan.holding[1]),
        },
        "position_updates": [
            {
                "date": when.isoformat(),
                "before": [view(item) for item in before],
                "after": [view(item) for item in after],
            }
            for when, before, after in plan.positions
        ],
        "daily_updates": [
            {"before": view(before), "after": view(after)}
            for before, after in plan.daily
        ],
        "annual_updates": [
            {"before": view(before), "after": view(after)}
            for before, after in plan.annual
        ],
        "rollback": {
            "transaction_fee_restore": [
                {
                    "transaction_id": before.id,
                    "expected_current_fee": str(after.fees),
                    "restore_fee": str(before.fees),
                }
                for before, after in plan.transactions
            ],
            "allocation_delete_pairs": [
                [item.sell_transaction_id, item.buy_transaction_id]
                for item in plan.allocations
            ],
            "holding_restore": view(plan.holding[0]),
            "position_restore": [
                {"date": when.isoformat(), "rows": [view(item) for item in before]}
                for when, before, _ in plan.positions
            ],
            "daily_restore": [view(before) for before, _ in plan.daily],
            "annual_restore": [view(before) for before, _ in plan.annual],
        },
    }


def _verified_backup(path: Path, expected_sha256: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("A nonempty pre-apply backup is required")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected_sha256:
        raise ValueError("Backup SHA-256 does not match")
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("Backup archive failed CRC validation")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version") not in (14, SCHEMA_VERSION):
            raise ValueError("Backup schema version is not the expected baseline")
        if not manifest.get("table_counts"):
            raise ValueError("Backup contains no table data")
        for entry, expected in manifest["sha256_by_entry"].items():
            if hashlib.sha256(archive.read(entry)).hexdigest() != expected:
                raise ValueError(f"Backup entry failed validation: {entry}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pams.lot_restatement")
    parser.add_argument("--manifest", required=True, type=Path)
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
    request = _request(args.manifest)
    configured = get_settings().database_url
    url = (
        configured.get_secret_value()
        if hasattr(configured, "get_secret_value")
        else str(configured)
    )
    target = open_database(url)
    try:
        if target.backend != "postgresql":
            raise ValueError(
                "This controlled production correction requires PostgreSQL"
            )
        if args.dry_run:
            target.connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            version = target.connection.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
            if version not in (14, SCHEMA_VERSION):
                raise ValueError("Unexpected production schema version")
            local = _sqlite_clone(target.connection)
            target.connection.rollback()
            try:
                repos = create_repositories("sqlite", local)
                plan = LotCorrectionUseCase(local, repos).preview(request)
            finally:
                local.close()
            print(
                json.dumps(
                    {"production_schema": version, **_summary(plan)},
                    ensure_ascii=True,
                    indent=2,
                )
            )
            return 0
        _verified_backup(args.backup, args.backup_sha256)
        version = target.connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        if version != SCHEMA_VERSION:
            raise ValueError(
                "Apply requires production schema v15; migration is separate"
            )
        target.connection.rollback()
        repos = create_repositories("postgresql", target.connection)
        plan = LotCorrectionUseCase(target.connection, repos).apply(
            request, reviewed_fingerprint=args.reviewed_fingerprint
        )
        print(json.dumps(_summary(plan), ensure_ascii=True, indent=2))
        return 0
    finally:
        target.connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
