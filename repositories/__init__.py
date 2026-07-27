"""Repository contracts and SQLite implementations."""

from repositories.holding_rebuild_uow import SQLiteHoldingRebuildUnitOfWork
from repositories.interfaces import (
    DividendRepository,
    HoldingRebuildRepository,
    HoldingRebuildUnitOfWork,
    HoldingRepository,
    LiabilityRepository,
    MarketDataUnitOfWork,
    PositionSnapshotRepository,
    PriceQuoteRepository,
    ReportDeliveryRepository,
    SnapshotRepository,
    TransactionLedgerRepository,
    TransactionRepository,
)
from repositories.market_data_uow import SQLiteMarketDataUnitOfWork
from repositories.postgresql import (
    PostgreSQLDividendRepository,
    PostgreSQLHoldingRepository,
    PostgreSQLLiabilityRepository,
    PostgreSQLPositionSnapshotRepository,
    PostgreSQLPriceQuoteRepository,
    PostgreSQLReportDeliveryRepository,
    PostgreSQLSnapshotRepository,
    PostgreSQLTransactionRepository,
)
from repositories.postgresql_uow import (
    PostgreSQLHoldingRebuildUnitOfWork,
    PostgreSQLMarketDataUnitOfWork,
)
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

__all__ = [
    "DividendRepository",
    "HoldingRepository",
    "HoldingRebuildRepository",
    "HoldingRebuildUnitOfWork",
    "LiabilityRepository",
    "MarketDataUnitOfWork",
    "PositionSnapshotRepository",
    "PostgreSQLDividendRepository",
    "PostgreSQLHoldingRepository",
    "PostgreSQLHoldingRebuildUnitOfWork",
    "PostgreSQLLiabilityRepository",
    "PostgreSQLMarketDataUnitOfWork",
    "PostgreSQLPositionSnapshotRepository",
    "PostgreSQLPriceQuoteRepository",
    "PostgreSQLReportDeliveryRepository",
    "PostgreSQLSnapshotRepository",
    "PostgreSQLTransactionRepository",
    "PriceQuoteRepository",
    "ReportDeliveryRepository",
    "SQLiteDividendRepository",
    "SQLiteHoldingRepository",
    "SQLiteHoldingRebuildUnitOfWork",
    "SQLiteLiabilityRepository",
    "SQLiteMarketDataUnitOfWork",
    "SQLitePositionSnapshotRepository",
    "SQLitePriceQuoteRepository",
    "SQLiteReportDeliveryRepository",
    "SQLiteSnapshotRepository",
    "SQLiteTransactionRepository",
    "SnapshotRepository",
    "TransactionRepository",
    "TransactionLedgerRepository",
]
