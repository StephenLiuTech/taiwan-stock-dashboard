"""Read-only operational status and verification services."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from database.schema import SCHEMA_VERSION
from market_calendar import MarketAvailability, MarketCalendar, MarketDateProvider
from market_data.engine import MarketDataEngine
from repositories.provider import RepositoryBundle, create_repositories


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
    twse_latest_source_date: date | None
    tpex_latest_source_date: date | None
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

    def __init__(
        self,
        connection: object,
        database_path: Path,
        repositories: RepositoryBundle | None = None,
        database_size: Callable[[], int] | None = None,
    ) -> None:
        self.connection = connection
        self.database_path = database_path
        self.repositories = repositories or create_repositories("sqlite", connection)
        self.database_size = database_size or (
            lambda: database_path.stat().st_size if database_path.exists() else 0
        )

    def read(self, availability: MarketAvailability | None) -> OperationalStatus:
        """Collect current database status."""
        daily = self.repositories.daily_snapshots.get_latest()
        schema = self.connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        return OperationalStatus(
            database_path=self.database_path,
            latest_quote_date=self.repositories.price_quotes.get_latest_date(),
            latest_daily_snapshot=daily.snapshot_date if daily else None,
            latest_position_snapshot=self.repositories.position_snapshots.get_latest_date(),
            holdings_count=len(self.repositories.holdings.list_all()),
            liabilities_count=len(self.repositories.liabilities.list_all()),
            schema_version=schema[0] if schema and schema[0] is not None else None,
            database_size_bytes=self.database_size(),
            twse_latest_source_date=availability.twse_date if availability else None,
            tpex_latest_source_date=availability.tpex_date if availability else None,
            commonly_ingestible_date=(
                availability.commonly_ingestible_date if availability else None
            ),
        )


class VerificationService:
    """Run explicit local and official-provider readiness checks."""

    def __init__(
        self,
        connection: object,
        database_path: Path,
        date_providers: tuple[MarketDateProvider, ...],
        calendar: MarketCalendar,
        engine: MarketDataEngine,
        repositories: RepositoryBundle | None = None,
        us_market_provider_status: str = "disabled",
        fx_provider_status: str = "disabled",
    ) -> None:
        self.connection = connection
        self.database_path = database_path
        self.date_providers = date_providers
        self.calendar = calendar
        self.engine = engine
        self.repositories = repositories
        self.us_market_provider_status = us_market_provider_status
        self.fx_provider_status = fx_provider_status

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
        checks.extend(self._optional_global_provider_checks())
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

    def _optional_global_provider_checks(self) -> list[VerificationCheck]:
        """Report optional-provider readiness without probing or exposing secrets."""
        if self.repositories is None:
            return []
        us_holdings = [
            item
            for item in self.repositories.holdings.list_all()
            if item.market.value == "US"
        ]
        if not us_holdings:
            return [
                VerificationCheck(
                    "US Market Provider",
                    CheckLevel.PASS,
                    f"{self.us_market_provider_status}; no active US holdings",
                ),
                VerificationCheck(
                    "FX Provider",
                    CheckLevel.PASS,
                    f"{self.fx_provider_status}; no active US holdings",
                ),
            ]
        us_dates = [
            quote.trade_date
            for holding in us_holdings
            if (
                quote := self.repositories.price_quotes.get_latest(holding.symbol, "US")
            )
            is not None
        ]
        fx = self.repositories.fx_rates.get_latest_on_or_before("USD", "TWD", date.max)
        us_ready = self.us_market_provider_status == "ready"
        fx_ready = self.fx_provider_status == "ready"
        return [
            VerificationCheck(
                "US Market Provider",
                CheckLevel.PASS if us_ready else CheckLevel.WARN,
                (
                    f"ready; latest persisted US quote {max(us_dates)}"
                    if us_ready and us_dates
                    else (
                        "ready; no persisted US quotes"
                        if us_ready
                        else "disabled with active US holdings"
                    )
                ),
            ),
            VerificationCheck(
                "FX Provider",
                CheckLevel.PASS if fx_ready else CheckLevel.WARN,
                (
                    f"ready; latest persisted USD/TWD rate {fx.rate_date}"
                    if fx_ready and fx
                    else (
                        "ready; no persisted USD/TWD rate"
                        if fx_ready
                        else "disabled with active US holdings"
                    )
                ),
            ),
        ]

    def _database_check(self) -> VerificationCheck:
        try:
            self.connection.execute("SELECT 1").fetchone()
        except Exception as error:
            return VerificationCheck("Database", CheckLevel.FAIL, str(error))
        return VerificationCheck("Database", CheckLevel.PASS, str(self.database_path))

    def _schema_check(self) -> VerificationCheck:
        try:
            row = self.connection.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()
            version = row[0] if row else None
        except Exception as error:
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
        except Exception as error:
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
                "latest publications differ; automatic ingestion will use "
                f"historical providers for {availability.commonly_ingestible_date} "
                f"(TWSE={availability.twse_date.isoformat()}, "
                f"TPEx={availability.tpex_date.isoformat()})",
            )
        return VerificationCheck(
            "Market Calendar",
            CheckLevel.PASS,
            f"commonly ingestible {availability.commonly_ingestible_date.isoformat()}",
        )
