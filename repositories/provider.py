"""Repository bundles selected by database backend."""

from dataclasses import dataclass

from repositories.holding_rebuild_uow import (
    SQLiteHoldingRebuildUnitOfWork,
    SQLiteMarginTransactionUnitOfWork,
)
from repositories.interfaces import (
    DividendEventRepository,
    DividendRepository,
    FxRateRepository,
    HoldingRebuildUnitOfWork,
    HoldingRepository,
    LiabilityRepository,
    MarginTransactionUnitOfWork,
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
    PostgreSQLFxRateRepository,
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
    PostgreSQLMarginTransactionUnitOfWork,
    PostgreSQLMarketDataUnitOfWork,
)
from repositories.sqlite import (
    SQLiteDividendEventRepository,
    SQLiteDividendRepository,
    SQLiteFxRateRepository,
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
    fx_rates: FxRateRepository
    liabilities: LiabilityRepository
    price_quotes: PriceQuoteRepository
    daily_snapshots: SnapshotRepository
    position_snapshots: PositionSnapshotRepository
    report_deliveries: ReportDeliveryRepository
    watchlist: WatchlistRepository
    market_data_uow: MarketDataUnitOfWork
    holding_rebuild_uow: HoldingRebuildUnitOfWork
    margin_transaction_uow: MarginTransactionUnitOfWork


def create_repositories(backend: str, connection: object) -> RepositoryBundle:
    """Create one consistent repository family for the selected backend."""
    if backend == "sqlite":
        return RepositoryBundle(
            SQLiteHoldingRepository(connection),
            SQLiteTransactionRepository(connection),
            SQLiteDividendRepository(connection),
            SQLiteDividendEventRepository(connection),
            SQLiteFxRateRepository(connection),
            SQLiteLiabilityRepository(connection),
            SQLitePriceQuoteRepository(connection),
            SQLiteSnapshotRepository(connection),
            SQLitePositionSnapshotRepository(connection),
            SQLiteReportDeliveryRepository(connection),
            SQLiteWatchlistRepository(connection),
            SQLiteMarketDataUnitOfWork(connection),
            SQLiteHoldingRebuildUnitOfWork(connection),
            SQLiteMarginTransactionUnitOfWork(connection),
        )
    if backend == "postgresql":
        return RepositoryBundle(
            PostgreSQLHoldingRepository(connection),
            PostgreSQLTransactionRepository(connection),
            PostgreSQLDividendRepository(connection),
            PostgreSQLDividendEventRepository(connection),
            PostgreSQLFxRateRepository(connection),
            PostgreSQLLiabilityRepository(connection),
            PostgreSQLPriceQuoteRepository(connection),
            PostgreSQLSnapshotRepository(connection),
            PostgreSQLPositionSnapshotRepository(connection),
            PostgreSQLReportDeliveryRepository(connection),
            PostgreSQLWatchlistRepository(connection),
            PostgreSQLMarketDataUnitOfWork(connection),
            PostgreSQLHoldingRebuildUnitOfWork(connection),
            PostgreSQLMarginTransactionUnitOfWork(connection),
        )
    raise ValueError(f"Unsupported database backend: {backend}")
