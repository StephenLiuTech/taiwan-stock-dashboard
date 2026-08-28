"""PostgreSQL implementations of every persisted repository contract."""

from repositories.sqlite import (
    SQLiteAnnualPnlSnapshotRepository,
    SQLiteCorporateActionRepository,
    SQLiteDividendEventRepository,
    SQLiteDividendRepository,
    SQLiteFxRateRepository,
    SQLiteHoldingRepository,
    SQLiteInvestmentCostEventRepository,
    SQLiteLiabilityRepository,
    SQLitePositionSnapshotRepository,
    SQLitePriceQuoteRepository,
    SQLiteReportDeliveryRepository,
    SQLiteSnapshotRepository,
    SQLiteTransactionRepository,
    SQLiteWatchlistRepository,
)


class PostgreSQLCorporateActionRepository(SQLiteCorporateActionRepository):
    """Persist replayable corporate actions in PostgreSQL."""


class PostgreSQLAnnualPnlSnapshotRepository(SQLiteAnnualPnlSnapshotRepository):
    """Persist immutable annual P/L snapshots in PostgreSQL."""


class PostgreSQLInvestmentCostEventRepository(SQLiteInvestmentCostEventRepository):
    """Persist dated investment costs in PostgreSQL."""


class PostgreSQLHoldingRepository(SQLiteHoldingRepository):
    """Persist holdings in PostgreSQL."""


class PostgreSQLTransactionRepository(SQLiteTransactionRepository):
    """Persist transactions in PostgreSQL."""


class PostgreSQLDividendRepository(SQLiteDividendRepository):
    """Persist dividends in PostgreSQL."""


class PostgreSQLDividendEventRepository(SQLiteDividendEventRepository):
    """Persist normalized official dividend events in PostgreSQL."""


class PostgreSQLFxRateRepository(SQLiteFxRateRepository):
    """Persist FX rates in PostgreSQL."""


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


class PostgreSQLWatchlistRepository(SQLiteWatchlistRepository):
    """Persist watchlist instruments in PostgreSQL."""
