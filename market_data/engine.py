"""Market-data ingestion and portfolio snapshot orchestration."""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from domain import (
    DailySnapshot,
    Holding,
    Market,
    PortfolioSummary,
    PositionSnapshot,
    PriceQuote,
)
from market_data.exceptions import (
    MarketDataError,
    ProviderDataError,
    SourceDateError,
    SourceDateMismatchError,
    SymbolNotFoundError,
)
from market_data.normalizer import QuoteNormalizer
from market_data.providers import MarketDataProvider
from repositories.interfaces import HoldingRepository, LiabilityRepository
from repositories.market_data_uow import SQLiteMarketDataUnitOfWork
from services.portfolio import PortfolioService
from services.snapshot import DuplicateSnapshotError, SnapshotService


@dataclass(frozen=True)
class MarketDataRefreshResult:
    """Result of one explicit end-of-day refresh."""

    quotes: tuple[PriceQuote, ...]
    holdings: tuple[Holding, ...]
    summary: PortfolioSummary
    snapshot: DailySnapshot
    verified_source_date: date


class MarketDataEngine:
    """Fetch, normalize, persist, value, and snapshot one trading day."""

    def __init__(
        self,
        providers: tuple[MarketDataProvider, ...],
        holdings: HoldingRepository,
        liabilities: LiabilityRepository,
        unit_of_work: SQLiteMarketDataUnitOfWork,
        normalizer: QuoteNormalizer | None = None,
        portfolio: PortfolioService | None = None,
    ) -> None:
        self.providers = {provider.market: provider for provider in providers}
        self.holdings = holdings
        self.liabilities = liabilities
        self.unit_of_work = unit_of_work
        self.normalizer = normalizer or QuoteNormalizer()
        self.portfolio = portfolio or PortfolioService()

    def refresh(self, trade_date: date) -> MarketDataRefreshResult:
        """Run one caller-triggered market-data refresh; no scheduling is implied."""
        if self.unit_of_work.daily_snapshots.get_by_date(trade_date):
            raise DuplicateSnapshotError(
                f"Snapshot already exists for {trade_date.isoformat()}"
            )
        result = self.preview(trade_date)
        position_snapshots = [
            PositionSnapshot(snapshot_date=trade_date, **position.model_dump())
            for position in result.summary.positions
        ]
        with self.unit_of_work.transaction():
            self.unit_of_work.price_quotes.upsert_many(list(result.quotes))
            snapshot = SnapshotService(self.unit_of_work.daily_snapshots).create(
                result.summary
            )
            self.unit_of_work.position_snapshots.add_many(position_snapshots)
        return MarketDataRefreshResult(
            quotes=result.quotes,
            holdings=result.holdings,
            summary=result.summary,
            snapshot=snapshot,
            verified_source_date=result.verified_source_date,
        )

    def preview(self, trade_date: date) -> MarketDataRefreshResult:
        """Run full validation and valuation without market-data persistence."""
        holdings = self.holdings.list_all()
        requested = {
            market: {holding.symbol for holding in holdings if holding.market == market}
            for market in Market
        }
        fetched_at = datetime.now(UTC)
        quotes: list[PriceQuote] = []
        for market, symbols in requested.items():
            if not symbols:
                continue
            provider = self.providers.get(market)
            if provider is None:
                raise ValueError(
                    f"No market-data provider configured for {market.value}"
                )
            try:
                source_records = provider.fetch()
            except MarketDataError:
                raise
            except Exception as error:
                raise ProviderDataError(
                    f"{market.value} provider request failed"
                ) from error
            if not source_records:
                raise ProviderDataError(f"{market.value} provider returned no records")
            source_dates = {
                self.normalizer.extract_trade_date(record) for record in source_records
            }
            if len(source_dates) != 1:
                raise SourceDateError(
                    f"{market.value} dataset contains ambiguous source dates"
                )
            source_date = source_dates.pop()
            if source_date != trade_date:
                raise SourceDateMismatchError(market, trade_date, source_date)
            records = {
                self.normalizer.extract_symbol(record): record
                for record in source_records
                if self.normalizer.extract_symbol(record) in symbols
            }
            missing = symbols - records.keys()
            if missing:
                missing_text = ", ".join(sorted(missing))
                raise SymbolNotFoundError(
                    f"{market.value} dataset does not contain: {missing_text}"
                )
            quotes.extend(
                self.normalizer.normalize(
                    records[symbol], market, trade_date, provider.source, fetched_at
                )
                for symbol in sorted(symbols)
            )

        summary = self.portfolio.value_portfolio(
            holdings, quotes, self.liabilities.list_all(), trade_date
        )
        snapshot = SnapshotService(self.unit_of_work.daily_snapshots).preview(summary)
        return MarketDataRefreshResult(
            quotes=tuple(quotes),
            holdings=tuple(holdings),
            summary=summary,
            snapshot=snapshot,
            verified_source_date=trade_date,
        )
