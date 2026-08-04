"""Repository bundles selected by database backend."""

from dataclasses import dataclass

from repositories.holding_rebuild_uow import SQLiteHoldingRebuildUnitOfWork
from repositories.interfaces import (
    DividendEventRepository,
    DividendRepository,
    HoldingRebuildUnitOfWork,
    HoldingRepository,
    LiabilityRepository,
    MarketDataUnitOfWork,
    PositionSnapshotRepository,
    PriceQuoteRepository,
    ReportDeliveryRepository,
    SnapshotRepository,
    TransactionRepository,
    WatchlistRepository,
)
from repositories.market_data_uow import SQLiteMarketDataUnitOfWork
from repositories.postgresql import (
    PostgreSQLDividendEventRepository,
    PostgreSQLDividendRepository,
    PostgreSQLHoldingRepository,
    PostgreSQLLiabilityRepository,
    PostgreSQLPositionSnapshotRepository,
    PostgreSQLPriceQuoteRepository,
    PostgreSQLReportDeliveryRepository,
    PostgreSQLSnapshotRepository,
    PostgreSQLTransactionRepository,
    PostgreSQLWatchlistRepository,
)
from repositories.postgresql_uow import (
    PostgreSQLHoldingRebuildUnitOfWork,
    PostgreSQLMarketDataUnitOfWork,
)
from repositories.sqlite import (
    SQLiteDividendEventRepository,
    SQLiteDividendRepository,
    SQLiteHoldingRepository,
    SQLiteLiabilityRepository,
    SQLitePositionSnapshotRepository,
    SQLitePriceQuoteRepository,
    SQLiteReportDeliveryRepository,
    SQLiteSnapshotRepository,
    SQLiteTransactionRepository,
    SQLiteWatchlistRepository,
)


@dataclass(frozen=True)
class RepositoryBundle:
    """All persistence ports needed by composition."""

    holdings: HoldingRepository
    transactions: TransactionRepository
    dividends: DividendRepository
    dividend_events: DividendEventRepository
    liabilities: LiabilityRepository
    price_quotes: PriceQuoteRepository
    daily_snapshots: SnapshotRepository
    position_snapshots: PositionSnapshotRepository
    report_deliveries: ReportDeliveryRepository
    watchlist: WatchlistRepository
    market_data_uow: MarketDataUnitOfWork
    holding_rebuild_uow: HoldingRebuildUnitOfWork


def create_repositories(backend: str, connection: object) -> RepositoryBundle:
    """Create one consistent repository family for the selected backend."""
    if backend == "sqlite":
        return RepositoryBundle(
            SQLiteHoldingRepository(connection),
            SQLiteTransactionRepository(connection),
            SQLiteDividendRepository(connection),
            SQLiteDividendEventRepository(connection),
            SQLiteLiabilityRepository(connection),
            SQLitePriceQuoteRepository(connection),
            SQLiteSnapshotRepository(connection),
            SQLitePositionSnapshotRepository(connection),
            SQLiteReportDeliveryRepository(connection),
            SQLiteWatchlistRepository(connection),
            SQLiteMarketDataUnitOfWork(connection),
            SQLiteHoldingRebuildUnitOfWork(connection),
        )
    if backend == "postgresql":
        return RepositoryBundle(
            PostgreSQLHoldingRepository(connection),
            PostgreSQLTransactionRepository(connection),
            PostgreSQLDividendRepository(connection),
            PostgreSQLDividendEventRepository(connection),
            PostgreSQLLiabilityRepository(connection),
            PostgreSQLPriceQuoteRepository(connection),
            PostgreSQLSnapshotRepository(connection),
            PostgreSQLPositionSnapshotRepository(connection),
            PostgreSQLReportDeliveryRepository(connection),
            PostgreSQLWatchlistRepository(connection),
            PostgreSQLMarketDataUnitOfWork(connection),
            PostgreSQLHoldingRebuildUnitOfWork(connection),
        )
    raise ValueError(f"Unsupported database backend: {backend}")
