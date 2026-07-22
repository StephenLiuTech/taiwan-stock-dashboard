"""Read-only operational status and verification services."""

import sqlite3
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from database.schema import SCHEMA_VERSION
from market_calendar import MarketAvailability, MarketCalendar, MarketDateProvider
from market_data.engine import MarketDataEngine
from repositories import (
    SQLiteHoldingRepository,
    SQLiteLiabilityRepository,
    SQLitePositionSnapshotRepository,
    SQLitePriceQuoteRepository,
    SQLiteSnapshotRepository,
)


@dataclass(frozen=True)
class OperationalStatus:
    """Current local database operational state."""

    database_path: Path
    latest_quote_date: date | None
    latest_daily_snapshot: date | None
    latest_position_snapshot: date | None
    holdings_count: int
    liabilities_count: int
    schema_version: int | None
    database_size_bytes: int
    twse_latest_source_date: date
    tpex_latest_source_date: date
    commonly_ingestible_date: date | None


class CheckLevel(StrEnum):
    """Verification result severity."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"


@dataclass(frozen=True)
class VerificationCheck:
    """One named operational verification result."""

    name: str
    level: CheckLevel
    detail: str


@dataclass(frozen=True)
class VerificationReport:
    """Complete operational verification report."""

    checks: tuple[VerificationCheck, ...]

    @property
    def failed(self) -> bool:
        return any(check.level is CheckLevel.FAIL for check in self.checks)


class OperationalStatusService:
    """Read local storage health without changing application data."""

    def __init__(self, connection: sqlite3.Connection, database_path: Path) -> None:
        self.connection = connection
        self.database_path = database_path

    def read(self, availability: MarketAvailability) -> OperationalStatus:
        """Collect current database status."""
        daily = SQLiteSnapshotRepository(self.connection).get_latest()
        schema = self.connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        return OperationalStatus(
            database_path=self.database_path,
            latest_quote_date=SQLitePriceQuoteRepository(
                self.connection
            ).get_latest_date(),
            latest_daily_snapshot=daily.snapshot_date if daily else None,
            latest_position_snapshot=SQLitePositionSnapshotRepository(
                self.connection
            ).get_latest_date(),
            holdings_count=len(SQLiteHoldingRepository(self.connection).list_all()),
            liabilities_count=len(
                SQLiteLiabilityRepository(self.connection).list_all()
            ),
            schema_version=schema[0] if schema and schema[0] is not None else None,
            database_size_bytes=(
                self.database_path.stat().st_size if self.database_path.exists() else 0
            ),
            twse_latest_source_date=availability.twse_date,
            tpex_latest_source_date=availability.tpex_date,
            commonly_ingestible_date=availability.commonly_ingestible_date,
        )


class VerificationService:
    """Run explicit local and official-provider readiness checks."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        database_path: Path,
        date_providers: tuple[MarketDateProvider, ...],
        calendar: MarketCalendar,
        engine: MarketDataEngine,
    ) -> None:
        self.connection = connection
        self.database_path = database_path
        self.date_providers = date_providers
        self.calendar = calendar
        self.engine = engine

    def run(self) -> VerificationReport:
        """Execute all checks and retain failures as report rows."""
        checks = [
            VerificationCheck("Configuration", CheckLevel.PASS, "configuration loaded"),
            self._database_check(),
            self._schema_check(),
            self._count_check("Holdings", "holdings", required=True),
            self._count_check("Liabilities", "liabilities", required=False),
        ]
        for provider in self.date_providers:
            checks.append(self._provider_check(provider))
        checks.append(self._calendar_check())
        checks.append(
            VerificationCheck(
                "Market Data Engine",
                (
                    CheckLevel.PASS
                    if self.engine.dependency_graph_ready()
                    else CheckLevel.FAIL
                ),
                (
                    "dependency graph ready"
                    if self.engine.dependency_graph_ready()
                    else "dependency graph incomplete"
                ),
            )
        )
        return VerificationReport(tuple(checks))

    def _database_check(self) -> VerificationCheck:
        try:
            self.connection.execute("SELECT 1").fetchone()
        except sqlite3.Error as error:
            return VerificationCheck("Database", CheckLevel.FAIL, str(error))
        return VerificationCheck("Database", CheckLevel.PASS, str(self.database_path))

    def _schema_check(self) -> VerificationCheck:
        try:
            row = self.connection.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()
            version = row[0] if row else None
        except sqlite3.Error as error:
            return VerificationCheck("Schema", CheckLevel.FAIL, str(error))
        level = CheckLevel.PASS if version == SCHEMA_VERSION else CheckLevel.FAIL
        return VerificationCheck(
            "Schema", level, f"version {version}; expected {SCHEMA_VERSION}"
        )

    def _count_check(
        self, name: str, table: str, *, required: bool
    ) -> VerificationCheck:
        try:
            count = self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[
                0
            ]
        except sqlite3.Error as error:
            return VerificationCheck(name, CheckLevel.FAIL, str(error))
        level = (
            CheckLevel.PASS
            if count
            else (CheckLevel.FAIL if required else CheckLevel.WARN)
        )
        return VerificationCheck(name, level, f"{count} records")

    @staticmethod
    def _provider_check(provider: MarketDateProvider) -> VerificationCheck:
        name = f"{provider.market.value} endpoint"
        try:
            available = provider.latest_available_date()
        except Exception as error:
            return VerificationCheck(name, CheckLevel.FAIL, str(error))
        return VerificationCheck(name, CheckLevel.PASS, available.isoformat())

    def _calendar_check(self) -> VerificationCheck:
        try:
            availability = self.calendar.market_availability()
        except Exception as error:
            return VerificationCheck("Market Calendar", CheckLevel.FAIL, str(error))
        if not availability.synchronized:
            return VerificationCheck(
                "Market Calendar",
                CheckLevel.WARN,
                "automatic ingestion is waiting for synchronization "
                f"(TWSE={availability.twse_date.isoformat()}, "
                f"TPEx={availability.tpex_date.isoformat()})",
            )
        return VerificationCheck(
            "Market Calendar",
            CheckLevel.PASS,
            f"commonly ingestible {availability.twse_date.isoformat()}",
        )
