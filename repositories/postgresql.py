"""PostgreSQL implementations of every persisted repository contract."""

from repositories.sqlite import (
    SQLiteDividendRepository,
    SQLiteHoldingRepository,
    SQLiteLiabilityRepository,
    SQLitePositionSnapshotRepository,
    SQLitePriceQuoteRepository,
    SQLiteReportDeliveryRepository,
    SQLiteSnapshotRepository,
    SQLiteTransactionRepository,
)


class PostgreSQLHoldingRepository(SQLiteHoldingRepository):
    """Persist holdings in PostgreSQL."""


class PostgreSQLTransactionRepository(SQLiteTransactionRepository):
    """Persist transactions in PostgreSQL."""


class PostgreSQLDividendRepository(SQLiteDividendRepository):
    """Persist dividends in PostgreSQL."""


class PostgreSQLLiabilityRepository(SQLiteLiabilityRepository):
    """Persist liabilities in PostgreSQL."""


class PostgreSQLSnapshotRepository(SQLiteSnapshotRepository):
    """Persist aggregate daily snapshots in PostgreSQL."""


class PostgreSQLPriceQuoteRepository(SQLitePriceQuoteRepository):
    """Persist normalized quotes in PostgreSQL."""


class PostgreSQLPositionSnapshotRepository(SQLitePositionSnapshotRepository):
    """Persist position-grain snapshots in PostgreSQL."""


class PostgreSQLReportDeliveryRepository(SQLiteReportDeliveryRepository):
    """Persist report-delivery claims and outcomes in PostgreSQL."""
