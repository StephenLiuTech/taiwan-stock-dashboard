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
from market_data.engine import MarketDataEngine
from market_data.providers import MarketDataProvider, TPExProvider, TWSEProvider
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
    logging_config = load_logging_config()
    settings = get_settings()
    configure_logging("DEBUG" if verbose else settings.log_level, logging_config.format)
    database_path = resolve_database_path(database_override)
    connection = initialize_database(f"sqlite:///{database_path.as_posix()}")
    try:
        initialize_schema(connection)
        holdings = SQLiteHoldingRepository(connection)
        liabilities = SQLiteLiabilityRepository(connection)
        seeded = BootstrapService(connection, holdings, liabilities).initialize()
        engine = MarketDataEngine(
            providers or (TWSEProvider(), TPExProvider()),
            holdings,
            liabilities,
            SQLiteMarketDataUnitOfWork(connection),
        )
        yield ApplicationContext(connection, engine, database_path, seeded)
    finally:
        connection.close()
