"""PostgreSQL transaction boundaries for PAMS workflows."""

from collections.abc import Iterator
from contextlib import contextmanager

from repositories.postgresql import (
    PostgreSQLFxRateRepository,
    PostgreSQLHoldingRepository,
    PostgreSQLPositionSnapshotRepository,
    PostgreSQLPriceQuoteRepository,
    PostgreSQLSnapshotRepository,
    PostgreSQLTransactionRepository,
)


class PostgreSQLMarketDataUnitOfWork:
    """Atomically persist one complete market-data ingestion."""

    def __init__(self, connection: object) -> None:
        self.connection = connection
        self.price_quotes = PostgreSQLPriceQuoteRepository(
            connection, auto_commit=False
        )
        self.fx_rates = PostgreSQLFxRateRepository(connection, auto_commit=False)
        self.daily_snapshots = PostgreSQLSnapshotRepository(
            connection, auto_commit=False
        )
        self.position_snapshots = PostgreSQLPositionSnapshotRepository(
            connection, auto_commit=False
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()


class PostgreSQLHoldingRebuildUnitOfWork:
    """Atomically rebuild transaction-derived holdings."""

    def __init__(self, connection: object) -> None:
        self.connection = connection
        self.holdings = PostgreSQLHoldingRepository(connection, auto_commit=False)
        self.transactions = PostgreSQLTransactionRepository(
            connection, auto_commit=False
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
