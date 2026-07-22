"""Concrete dependency composition for local PAMS commands."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from config import get_settings, load_logging_config
from core.constants import PROJECT_ROOT
from core.logging import configure_logging
from database import initialize_database, initialize_schema
from market_calendar import MarketCalendar, OfficialMarketDateProvider
from market_data.engine import MarketDataEngine
from market_data.providers import (
    HistoricalTPExProvider,
    HistoricalTWSEProvider,
    MarketDataProvider,
    TPExProvider,
    TWSEProvider,
)
from pams.application import (
    DemoDataUseCase,
    PortfolioHistoryUseCase,
    PortfolioStatusUseCase,
    UpdatePortfolioUseCase,
    VerifySystemUseCase,
)
from pams.operations import OperationalStatusService, VerificationService
from repositories import (
    SQLiteHoldingRepository,
    SQLiteLiabilityRepository,
    SQLiteMarketDataUnitOfWork,
    SQLitePositionSnapshotRepository,
    SQLitePriceQuoteRepository,
    SQLiteSnapshotRepository,
)
from services import BootstrapService


@dataclass(frozen=True)
class ApplicationContext:
    """Resources composed for one local CLI invocation."""

    connection: sqlite3.Connection
    engine: MarketDataEngine
    database_path: Path
    seeded: bool
    calendar: MarketCalendar
    status: OperationalStatusService
    verification: VerificationService
    update_portfolio: UpdatePortfolioUseCase
    portfolio_status: PortfolioStatusUseCase
    verify_system: VerifySystemUseCase
    portfolio_history: PortfolioHistoryUseCase | None = None


def resolve_database_path(override: Path | None = None) -> Path:
    """Resolve an override or configured SQLite URL to an absolute path."""
    if override is not None:
        return override.expanduser().resolve()
    database_url = get_settings().database_url
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("CLI supports only sqlite:/// database URLs")
    raw_path = database_url.removeprefix(prefix)
    if not raw_path or raw_path == ":memory:":
        raise ValueError("CLI requires a file-backed SQLite database")
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def compose_demo_data() -> DemoDataUseCase:
    """Build the isolated demo-data workflow with production protection."""
    return DemoDataUseCase(resolve_database_path())


@contextmanager
def compose_application(
    database_override: Path | None = None,
    *,
    verbose: bool = False,
    providers: tuple[MarketDataProvider, ...] | None = None,
) -> Iterator[ApplicationContext]:
    """Initialize storage, bootstrap data, and wire a market-data engine."""
    with _compose(
        database_override, verbose=verbose, providers=providers, initialize=True
    ) as context:
        yield context


@contextmanager
def compose_operations(
    database_override: Path | None = None,
    *,
    verbose: bool = False,
    providers: tuple[MarketDataProvider, ...] | None = None,
) -> Iterator[ApplicationContext]:
    """Wire read-only operational commands without migration or bootstrap."""
    with _compose(
        database_override, verbose=verbose, providers=providers, initialize=False
    ) as context:
        yield context


@contextmanager
def _compose(
    database_override: Path | None,
    *,
    verbose: bool,
    providers: tuple[MarketDataProvider, ...] | None,
    initialize: bool,
) -> Iterator[ApplicationContext]:
    logging_config = load_logging_config()
    settings = get_settings()
    configure_logging("DEBUG" if verbose else settings.log_level, logging_config.format)
    database_path = resolve_database_path(database_override)
    connection = initialize_database(f"sqlite:///{database_path.as_posix()}")
    try:
        if initialize:
            initialize_schema(connection)
        holdings = SQLiteHoldingRepository(connection)
        liabilities = SQLiteLiabilityRepository(connection)
        seeded = (
            BootstrapService(connection, holdings, liabilities).initialize()
            if initialize
            else False
        )
        market_providers = providers or (TWSEProvider(), TPExProvider())
        engine = MarketDataEngine(
            market_providers,
            holdings,
            liabilities,
            SQLiteMarketDataUnitOfWork(connection),
        )
        date_providers = tuple(
            OfficialMarketDateProvider(provider) for provider in market_providers
        )
        calendar = MarketCalendar(date_providers)
        status_service = OperationalStatusService(connection, database_path)
        verification_service = VerificationService(
            connection, database_path, date_providers, calendar, engine
        )
        snapshots = SQLiteSnapshotRepository(connection)
        position_snapshots = SQLitePositionSnapshotRepository(connection)
        quotes = SQLitePriceQuoteRepository(connection)

        def historical_engine(trade_date: date) -> MarketDataEngine:
            return MarketDataEngine(
                (
                    HistoricalTWSEProvider(trade_date),
                    HistoricalTPExProvider(trade_date),
                ),
                holdings,
                liabilities,
                SQLiteMarketDataUnitOfWork(connection),
            )

        yield ApplicationContext(
            connection=connection,
            engine=engine,
            database_path=database_path,
            seeded=seeded,
            calendar=calendar,
            status=status_service,
            verification=verification_service,
            update_portfolio=UpdatePortfolioUseCase(
                calendar, engine, database_path, historical_engine
            ),
            portfolio_status=PortfolioStatusUseCase(
                calendar,
                status_service,
                holdings,
                snapshots,
                position_snapshots,
                quotes,
            ),
            verify_system=VerifySystemUseCase(verification_service),
            portfolio_history=PortfolioHistoryUseCase(snapshots),
        )
    finally:
        connection.close()
