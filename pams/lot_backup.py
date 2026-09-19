"""Read-only, checksummed pre-lot-correction PostgreSQL logical export.

The archive preserves every public table row plus catalog metadata. It is not
a substitute for a native pg_dump restore; a restore must be rehearsed before
any production apply is approved.
"""

import hashlib
import json
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from config import get_settings
from database.provider import open_database


def _json(value: object) -> str:
    return json.dumps(value, default=str, ensure_ascii=True, sort_keys=True)


def export() -> tuple[Path, int, int, str]:
    configured = get_settings().database_url
    url = (
        configured.get_secret_value()
        if hasattr(configured, "get_secret_value")
        else str(configured)
    )
    target = open_database(url)
    if target.backend != "postgresql":
        raise ValueError("Production logical export requires PostgreSQL")
    connection = target.connection
    try:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        version = connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()[0]
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE' "
                "ORDER BY table_name"
            ).fetchall()
        ]
        catalog = {
            "columns": [
                dict(row)
                for row in connection.execute(
                    "SELECT table_name,column_name,data_type,is_nullable,column_default "
                    "FROM information_schema.columns WHERE table_schema='public' "
                    "ORDER BY table_name,ordinal_position"
                ).fetchall()
            ],
            "constraints": [
                dict(row)
                for row in connection.execute(
                    "SELECT conrelid::regclass::text AS table_name,conname,"
                    "pg_get_constraintdef(oid) AS definition FROM pg_constraint "
                    "WHERE connamespace='public'::regnamespace ORDER BY conrelid,conname"
                ).fetchall()
            ],
            "indexes": [
                dict(row)
                for row in connection.execute(
                    "SELECT tablename,indexname,indexdef FROM pg_indexes "
                    "WHERE schemaname='public' ORDER BY tablename,indexname"
                ).fetchall()
            ],
        }
        folder = Path(tempfile.mkdtemp(prefix="pams-pre-lot-backup-"))
        archive = folder / f"production-schema-{version}-logical.zip"
        entries = {}
        counts = {}
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            schema_payload = _json({"schema_version": version, **catalog}).encode()
            output.writestr("schema_catalog.json", schema_payload)
            entries["schema_catalog.json"] = hashlib.sha256(schema_payload).hexdigest()
            for table in tables:
                columns = [
                    row["column_name"]
                    for row in catalog["columns"]
                    if row["table_name"] == table
                ]
                names = ",".join(columns)
                rows = connection.execute(f"SELECT {names} FROM {table}").fetchall()
                payload = "".join(
                    _json({name: row[name] for name in columns}) + "\n" for row in rows
                ).encode()
                entry = f"tables/{table}.jsonl"
                output.writestr(entry, payload)
                entries[entry] = hashlib.sha256(payload).hexdigest()
                counts[table] = len(rows)
            manifest = {
                "schema_version": version,
                "created_at": datetime.now().astimezone().isoformat(),
                "table_counts": counts,
                "sha256_by_entry": entries,
            }
            output.writestr("manifest.json", _json(manifest).encode())
        connection.rollback()
    finally:
        connection.close()
    with zipfile.ZipFile(archive) as source:
        if source.testzip() is not None:
            raise ValueError("Backup archive failed CRC validation")
        restored_manifest = json.loads(source.read("manifest.json"))
        for name, expected in restored_manifest["sha256_by_entry"].items():
            if hashlib.sha256(source.read(name)).hexdigest() != expected:
                raise ValueError(f"Backup entry failed SHA-256 validation: {name}")
        if set(restored_manifest["table_counts"]) != set(tables):
            raise ValueError("Backup table manifest is incomplete")
    if archive.stat().st_size == 0:
        raise ValueError("Backup archive is empty")
    file_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return archive, archive.stat().st_size, len(tables), file_digest


if __name__ == "__main__":
    path, size, count, digest = export()
    print(f"backup={path}\nsize_bytes={size}\ntables={count}\nsha256={digest}")
