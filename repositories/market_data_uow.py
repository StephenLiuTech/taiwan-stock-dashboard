"""Minimal SQLite transaction boundary for one market-data ingestion."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from repositories.sqlite import (
    SQLiteFxRateRepository,
    SQLitePositionSnapshotRepository,
    SQLitePriceQuoteRepository,
    SQLiteSnapshotRepository,
)


class SQLiteMarketDataUnitOfWork:
    """Atomically persist quotes, aggregate snapshot, and position rows."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.price_quotes = SQLitePriceQuoteRepository(connection, auto_commit=False)
        self.fx_rates = SQLiteFxRateRepository(connection, auto_commit=False)
        self.daily_snapshots = SQLiteSnapshotRepository(connection, auto_commit=False)
        self.position_snapshots = SQLitePositionSnapshotRepository(
            connection, auto_commit=False
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit all ingestion writes together or roll every write back."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
