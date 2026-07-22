"""Concrete dependency composition for local PAMS commands."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from config import get_settings, load_logging_config
from core.constants import PROJECT_ROOT
from core.logging import configure_logging
from database import initialize_database, initialize_schema
from market_calendar import MarketCalendar, OfficialMarketDateProvider
from market_data.engine import MarketDataEngine
from market_data.providers import MarketDataProvider, TPExProvider, TWSEProvider
from pams.operations import OperationalStatusService, VerificationService
from repositories import (
    SQLiteHoldingRepository,
    SQLiteLiabilityRepository,
    SQLiteMarketDataUnitOfWork,
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
        yield ApplicationContext(
            connection,
            engine,
            database_path,
            seeded,
            calendar,
            OperationalStatusService(connection, database_path),
            VerificationService(
                connection, database_path, date_providers, calendar, engine
            ),
        )
    finally:
        connection.close()
