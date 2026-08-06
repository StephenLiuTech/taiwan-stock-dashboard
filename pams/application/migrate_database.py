"""Atomic SQLite-to-PostgreSQL data migration."""

from dataclasses import dataclass
from time import perf_counter

from database.provider import open_database, redact_database_url

MIGRATION_TABLES = (
    "holdings",
    "liabilities",
    "transactions",
    "dividends",
    "dividend_events",
    "price_quotes",
    "fx_rates",
    "daily_snapshots",
    "position_snapshots",
    "report_deliveries",
    "watchlist",
    "schema_version",
)


@dataclass(frozen=True)
class DatabaseMigrationResult:
    """Presentation-neutral successful migration outcome."""

    source_database: str
    destination_database: str
    rows_copied: tuple[tuple[str, int], ...]
    elapsed_seconds: float

    @property
    def total_rows(self) -> int:
        return sum(count for _, count in self.rows_copied)


class DatabaseMigrationError(RuntimeError):
    """Raised when migration cannot complete atomically."""


class MigrateDatabaseUseCase:
    """Copy a complete SQLite database into an empty PostgreSQL schema."""

    def __init__(self, source_url: str, destination_url: str) -> None:
        self.source_url = source_url
        self.destination_url = destination_url

    def execute(self) -> DatabaseMigrationResult:
        started = perf_counter()
        source = open_database(self.source_url)
        destination = open_database(self.destination_url)
        try:
            if source.backend != "sqlite" or destination.backend != "postgresql":
                raise DatabaseMigrationError(
                    "migration requires a SQLite source and PostgreSQL destination"
                )
            destination.initialize_schema()
            copied = self._copy(source.connection, destination.connection)
            return DatabaseMigrationResult(
                source.display_url,
                destination.display_url,
                tuple(copied),
                perf_counter() - started,
            )
        finally:
            source.connection.close()
            destination.connection.close()

    def _copy(self, source: object, destination: object) -> list[tuple[str, int]]:
        try:
            self._require_empty_destination(destination)
            counts: list[tuple[str, int]] = []
            for table in MIGRATION_TABLES:
                columns = [
                    row[1]
                    for row in source.execute(f"PRAGMA table_info({table})").fetchall()
                ]
                rows = source.execute(f"SELECT * FROM {table}").fetchall()
                if table == "schema_version":
                    destination.execute("DELETE FROM schema_version")
                if rows:
                    placeholders = ", ".join("?" for _ in columns)
                    names = ", ".join(columns)
                    destination.executemany(
                        f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
                        [tuple(row[column] for column in columns) for row in rows],
                    )
                target_count = destination.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                if target_count != len(rows):
                    raise DatabaseMigrationError(
                        f"row-count validation failed for {table}: "
                        f"source={len(rows)}, destination={target_count}"
                    )
                counts.append((table, len(rows)))
            destination.commit()
            return counts
        except Exception as error:
            destination.rollback()
            if isinstance(error, DatabaseMigrationError):
                raise
            raise DatabaseMigrationError(
                f"database migration failed: {type(error).__name__}: {error}"
            ) from error

    @staticmethod
    def _require_empty_destination(destination: object) -> None:
        occupied = []
        for table in MIGRATION_TABLES:
            if table == "schema_version":
                continue
            count = destination.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count:
                occupied.append(f"{table}={count}")
        if occupied:
            raise DatabaseMigrationError(
                "PostgreSQL destination must be empty: " + ", ".join(occupied)
            )


def migration_display_url(database_url: str) -> str:
    """Expose a credential-safe URL for errors and rendering."""
    return redact_database_url(database_url)
