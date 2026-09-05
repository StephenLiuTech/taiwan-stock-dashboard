"""PostgreSQL transaction boundaries for PAMS workflows."""

from collections.abc import Iterator
from contextlib import contextmanager

from repositories.postgresql import (
    PostgreSQLBrokerImportRecordRepository,
    PostgreSQLFxRateRepository,
    PostgreSQLHoldingRepository,
    PostgreSQLLiabilityPrincipalEventRepository,
    PostgreSQLLiabilityRepository,
    PostgreSQLPositionSnapshotRepository,
    PostgreSQLPriceQuoteRepository,
    PostgreSQLSnapshotRepository,
    PostgreSQLStockNetEquityHistoryRepository,
    PostgreSQLTransactionRepository,
)


class PostgreSQLStockNetEquityHistoryUnitOfWork:
    """Atomically insert historical equity observations without replacement."""

    def __init__(self, connection: object) -> None:
        self.connection = connection
        self.history = PostgreSQLStockNetEquityHistoryRepository(
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


class PostgreSQLMarginTransactionUnitOfWork:
    """Atomically persist a margin transaction, holding, and liability."""

    def __init__(self, connection: object) -> None:
        self.connection = connection
        self.holdings = PostgreSQLHoldingRepository(connection, auto_commit=False)
        self.transactions = PostgreSQLTransactionRepository(
            connection, auto_commit=False
        )
        self.liabilities = PostgreSQLLiabilityRepository(connection, auto_commit=False)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()


class PostgreSQLBrokerImportUnitOfWork:
    """Atomically apply broker source facts and structured provenance."""

    def __init__(self, connection: object) -> None:
        self.connection = connection
        self.holdings = PostgreSQLHoldingRepository(connection, auto_commit=False)
        self.transactions = PostgreSQLTransactionRepository(
            connection, auto_commit=False
        )
        self.liabilities = PostgreSQLLiabilityRepository(connection, auto_commit=False)
        self.liability_principal_events = PostgreSQLLiabilityPrincipalEventRepository(
            connection, auto_commit=False
        )
        self.broker_import_records = PostgreSQLBrokerImportRecordRepository(
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
