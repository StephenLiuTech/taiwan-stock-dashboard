"""Repository bundles selected by database backend."""

from dataclasses import dataclass

from repositories.holding_rebuild_uow import (
    SQLiteHoldingRebuildUnitOfWork,
    SQLiteMarginTransactionUnitOfWork,
)
from repositories.interfaces import (
    AnnualPnlSnapshotRepository,
    CorporateActionRepository,
    DividendEventRepository,
    DividendRepository,
    FxRateRepository,
    HoldingRebuildUnitOfWork,
    HoldingRepository,
    InvestmentCostEventRepository,
    LiabilityPrincipalEventRepository,
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
    PostgreSQLAnnualPnlSnapshotRepository,
    PostgreSQLCorporateActionRepository,
    PostgreSQLDividendEventRepository,
    PostgreSQLDividendRepository,
    PostgreSQLFxRateRepository,
    PostgreSQLHoldingRepository,
    PostgreSQLInvestmentCostEventRepository,
    PostgreSQLLiabilityPrincipalEventRepository,
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
    SQLiteAnnualPnlSnapshotRepository,
    SQLiteCorporateActionRepository,
    SQLiteDividendEventRepository,
    SQLiteDividendRepository,
    SQLiteFxRateRepository,
    SQLiteHoldingRepository,
    SQLiteInvestmentCostEventRepository,
    SQLiteLiabilityPrincipalEventRepository,
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
    corporate_actions: CorporateActionRepository
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
    annual_pnl_snapshots: AnnualPnlSnapshotRepository
    investment_cost_events: InvestmentCostEventRepository
    liability_principal_events: LiabilityPrincipalEventRepository


def create_repositories(backend: str, connection: object) -> RepositoryBundle:
    """Create one consistent repository family for the selected backend."""
    if backend == "sqlite":
        return RepositoryBundle(
            SQLiteHoldingRepository(connection),
            SQLiteTransactionRepository(connection),
            SQLiteCorporateActionRepository(connection),
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
            SQLiteAnnualPnlSnapshotRepository(connection),
            SQLiteInvestmentCostEventRepository(connection),
            SQLiteLiabilityPrincipalEventRepository(connection),
        )
    if backend == "postgresql":
        return RepositoryBundle(
            PostgreSQLHoldingRepository(connection),
            PostgreSQLTransactionRepository(connection),
            PostgreSQLCorporateActionRepository(connection),
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
            PostgreSQLAnnualPnlSnapshotRepository(connection),
            PostgreSQLInvestmentCostEventRepository(connection),
            PostgreSQLLiabilityPrincipalEventRepository(connection),
        )
    raise ValueError(f"Unsupported database backend: {backend}")
