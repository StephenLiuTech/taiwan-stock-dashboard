"""Multi-market orchestration around the unchanged Taiwan market engine."""

import logging
from datetime import date
from decimal import Decimal

from domain import (
    Currency,
    FxRate,
    Holding,
    Market,
    PortfolioSummary,
    PositionSnapshot,
    PositionValuation,
)
from market_data.engine import MarketDataEngine, MarketDataRefreshResult
from market_data.exceptions import ProviderDataError
from market_data.providers import FXRateProvider, USMarketDataProvider
from repositories.interfaces import (
    FxRateRepository,
    HoldingRepository,
    LiabilityRepository,
    MarketDataUnitOfWork,
)
from services.multi_currency_valuation import MultiCurrencyValuationEngine
from services.snapshot import DuplicateSnapshotError, SnapshotService

_LOGGER = logging.getLogger(__name__)


class GlobalMarketDataEngine:
    """Create one TWD snapshot from independently dated Taiwan and US closes."""

    def __init__(
        self,
        taiwan_engine: MarketDataEngine,
        holdings: HoldingRepository,
        liabilities: LiabilityRepository,
        unit_of_work: MarketDataUnitOfWork,
        fx_rates: FxRateRepository,
        us_provider: USMarketDataProvider | None,
        fx_provider: FXRateProvider | None,
    ) -> None:
        self.taiwan_engine = taiwan_engine
        self.holdings = holdings
        self.liabilities = liabilities
        self.unit_of_work = unit_of_work
        self.fx_rates = fx_rates
        self.us_provider = us_provider
        self.fx_provider = fx_provider
        self.valuation = MultiCurrencyValuationEngine()

    def dependency_graph_ready(self) -> bool:
        return self.taiwan_engine.dependency_graph_ready()

    def requires_enrichment(
        self,
        trade_date: date,
        *,
        holdings_override: tuple[Holding, ...] | None = None,
    ) -> bool:
        """Return whether an existing snapshot can gain missing global coverage."""
        holdings = list(holdings_override or tuple(self.holdings.list_all()))
        us_holdings = [item for item in holdings if item.market is Market.US]
        if not us_holdings:
            return False
        quotes_complete = all(
            self.unit_of_work.price_quotes.get_latest_on_or_before(
                item.symbol, item.market.value, trade_date
            )
            is not None
            for item in us_holdings
        )
        fx_complete = (
            self.fx_rates.get_latest_on_or_before(
                Currency.USD.value, Currency.TWD.value, trade_date
            )
            is not None
        )
        covered_ids = {
            item.holding_id
            for item in self.unit_of_work.position_snapshots.list_by_date(trade_date)
        }
        positions_complete = all(item.id in covered_ids for item in holdings)
        fetchable_missing = (not quotes_complete and self.us_provider is not None) or (
            not fx_complete and self.fx_provider is not None
        )
        return fetchable_missing or (
            quotes_complete and fx_complete and not positions_complete
        )

    def enrich_existing(
        self,
        trade_date: date,
        *,
        holdings_override: tuple[Holding, ...] | None = None,
    ) -> MarketDataRefreshResult:
        """Enrich one existing Taiwan snapshot without refetching Taiwan data."""
        result, fx_rate = self._preview(
            trade_date, holdings_override, reuse_persisted_taiwan=True
        )
        with self.unit_of_work.transaction():
            self.unit_of_work.price_quotes.upsert_many(list(result.quotes))
            if fx_rate is not None:
                self.unit_of_work.fx_rates.upsert(fx_rate)
            self.unit_of_work.daily_snapshots.replace(result.snapshot)
            self.unit_of_work.position_snapshots.replace_many(
                trade_date, self._snapshots(result)
            )
        return result

    def refresh(
        self,
        trade_date: date,
        *,
        holdings_override: tuple[Holding, ...] | None = None,
    ) -> MarketDataRefreshResult:
        if self.unit_of_work.daily_snapshots.get_by_date(trade_date):
            raise DuplicateSnapshotError(
                f"Snapshot already exists for {trade_date.isoformat()}"
            )
        result, fx_rate = self._preview(trade_date, holdings_override)
        positions = self._snapshots(result)
        with self.unit_of_work.transaction():
            self.unit_of_work.price_quotes.upsert_many(list(result.quotes))
            if fx_rate is not None:
                self.unit_of_work.fx_rates.upsert(fx_rate)
            snapshot = SnapshotService(self.unit_of_work.daily_snapshots).create(
                result.summary
            )
            self.unit_of_work.position_snapshots.add_many(positions)
        return MarketDataRefreshResult(
            result.quotes,
            result.holdings,
            result.summary,
            snapshot,
            result.verified_source_date,
        )

    def rebuild(
        self,
        trade_date: date,
        *,
        holdings_override: tuple[Holding, ...] | None = None,
    ) -> MarketDataRefreshResult:
        result, fx_rate = self._preview(trade_date, holdings_override)
        with self.unit_of_work.transaction():
            self.unit_of_work.price_quotes.replace_many_for_date(
                trade_date, [q for q in result.quotes if q.trade_date == trade_date]
            )
            older_quotes = [q for q in result.quotes if q.trade_date != trade_date]
            if older_quotes:
                self.unit_of_work.price_quotes.upsert_many(older_quotes)
            if fx_rate is not None:
                self.unit_of_work.fx_rates.upsert(fx_rate)
            self.unit_of_work.daily_snapshots.replace(result.snapshot)
            self.unit_of_work.position_snapshots.replace_many(
                trade_date, self._snapshots(result)
            )
        return result

    def preview(
        self,
        trade_date: date,
        *,
        holdings_override: tuple[Holding, ...] | None = None,
    ) -> MarketDataRefreshResult:
        return self._preview(trade_date, holdings_override)[0]

    def _preview(
        self,
        trade_date: date,
        holdings_override: tuple[Holding, ...] | None,
        *,
        reuse_persisted_taiwan: bool = False,
    ) -> tuple[MarketDataRefreshResult, FxRate | None]:
        holdings = list(holdings_override or tuple(self.holdings.list_all()))
        us_holdings = [h for h in holdings if h.market is Market.US]
        taiwan_holdings = [h for h in holdings if h.market is not Market.US]
        if not us_holdings:
            return (
                self.taiwan_engine.preview(
                    trade_date, holdings_override=tuple(taiwan_holdings)
                ),
                None,
            )
        if reuse_persisted_taiwan:
            _LOGGER.info("Taiwan snapshot already exists; skipped Taiwan fetch")
            taiwan_quotes = tuple(
                item
                for item in self.unit_of_work.price_quotes.list_by_date(trade_date)
                if item.market is not Market.US
            )
            expected_keys = {(item.symbol, item.market) for item in taiwan_holdings}
            actual_keys = {(item.symbol, item.market) for item in taiwan_quotes}
            if actual_keys != expected_keys:
                raise ProviderDataError(
                    "Existing Taiwan quote coverage is incomplete for enrichment"
                )
        else:
            taiwan_quotes = self.taiwan_engine.preview(
                trade_date, holdings_override=tuple(taiwan_holdings)
            ).quotes
        persisted_us_quotes = tuple(
            quote
            for holding in us_holdings
            if (
                quote := self.unit_of_work.price_quotes.get_latest_on_or_before(
                    holding.symbol, holding.market.value, trade_date
                )
            )
            is not None
        )
        persisted_keys = {(item.symbol, item.market) for item in persisted_us_quotes}
        symbols_to_fetch = tuple(
            item.symbol
            for item in us_holdings
            if not reuse_persisted_taiwan
            or (item.symbol, item.market) not in persisted_keys
        )
        if reuse_persisted_taiwan and symbols_to_fetch:
            _LOGGER.info(
                "Missing US quotes detected; fetching symbols=%s",
                ",".join(symbols_to_fetch),
            )
        us_quotes: tuple = persisted_us_quotes if reuse_persisted_taiwan else ()
        if self.us_provider is not None and symbols_to_fetch:
            try:
                us_quotes = tuple(
                    [*us_quotes, *self.us_provider.fetch(symbols_to_fetch, trade_date)]
                )
            except ProviderDataError as error:
                _LOGGER.warning(
                    "US holdings remain unquoted: provider=%s error=%s",
                    self.us_provider.source,
                    type(error).__name__,
                )
        elif symbols_to_fetch:
            _LOGGER.warning("US holdings remain unquoted: provider=disabled")
        live_quote_keys = {(item.symbol, item.market) for item in us_quotes}
        persisted_quotes = tuple(
            quote
            for holding in us_holdings
            if (holding.symbol, holding.market) not in live_quote_keys
            if (
                quote := self.unit_of_work.price_quotes.get_latest_on_or_before(
                    holding.symbol, holding.market.value, trade_date
                )
            )
            is not None
        )
        if persisted_quotes:
            _LOGGER.warning(
                "Using persisted non-future US quotes: symbols=%s",
                ",".join(item.symbol for item in persisted_quotes),
            )
        us_quotes = tuple([*us_quotes, *persisted_quotes])
        try:
            if (
                reuse_persisted_taiwan
                and self.fx_rates.get_latest_on_or_before(
                    Currency.USD.value, Currency.TWD.value, trade_date
                )
                is None
            ):
                _LOGGER.info("Missing USD/TWD rate detected; fetching FX")
            fx_rate = self._resolve_fx(
                trade_date, prefer_persisted=reuse_persisted_taiwan
            )
        except ProviderDataError as error:
            _LOGGER.warning(
                "US holdings remain untranslated: error=%s", type(error).__name__
            )
            fx_rate = None
        quoted_us_keys = {(item.symbol, item.market) for item in us_quotes}
        valued_holdings = [
            *taiwan_holdings,
            *(
                item
                for item in us_holdings
                if fx_rate is not None and (item.symbol, item.market) in quoted_us_keys
            ),
        ]
        valuation = self.valuation.valuate(
            trade_date, valued_holdings, [*taiwan_quotes, *us_quotes], fx_rate
        )
        liabilities = self.liabilities.list_all()
        if any(item.currency is not Currency.TWD for item in liabilities):
            raise ValueError("Global portfolio liabilities must be denominated in TWD")
        principal = sum((item.principal for item in liabilities), Decimal("0"))
        positions = [
            PositionValuation(
                holding_id=holding.id,
                symbol=value.symbol,
                market=value.market,
                native_currency=value.native_currency,
                quote_date=value.quote_date,
                fx_rate=value.fx_rate,
                fx_rate_date=value.fx_rate_date,
                quantity=value.quantity,
                average_cost=value.native_average_cost,
                close_price=value.native_close,
                cost_basis=value.cost_basis_twd,
                market_value=value.market_value_twd,
                unrealized_pnl=value.unrealized_pnl_twd,
                unrealized_return=value.unrealized_return_pct,
                portfolio_weight=value.portfolio_weight,
                daily_value_change=value.daily_pnl_twd,
                daily_return=value.daily_return_pct,
            )
            for holding, value in (
                (
                    next(
                        h
                        for h in holdings
                        if (h.symbol, h.market) == (item.symbol, item.market)
                    ),
                    item,
                )
                for item in valuation.holdings
            )
        ]
        summary = PortfolioSummary(
            valuation_date=trade_date,
            positions=positions,
            total_market_value=valuation.total_market_value_twd,
            total_cost_basis=valuation.total_cost_twd,
            total_unrealized_pnl=valuation.total_unrealized_pnl_twd,
            total_liabilities=principal,
            net_asset_value=valuation.total_market_value_twd - principal,
            leverage_ratio=(
                principal / valuation.total_market_value_twd
                if valuation.total_market_value_twd
                else Decimal("0")
            ),
        )
        snapshot = SnapshotService(self.unit_of_work.daily_snapshots).preview(summary)
        return (
            MarketDataRefreshResult(
                tuple([*taiwan_quotes, *us_quotes]),
                tuple(holdings),
                summary,
                snapshot,
                trade_date,
            ),
            fx_rate,
        )

    def _resolve_fx(
        self, report_date: date, *, prefer_persisted: bool = False
    ) -> FxRate:
        persisted = self.fx_rates.get_latest_on_or_before(
            Currency.USD.value, Currency.TWD.value, report_date
        )
        if prefer_persisted and persisted is not None:
            return persisted
        if self.fx_provider is not None:
            try:
                return self.fx_provider.fetch(Currency.USD, Currency.TWD, report_date)
            except ProviderDataError as error:
                _LOGGER.warning(
                    "Live FX refresh failed; checking persisted non-future rate: "
                    "provider=%s error=%s",
                    self.fx_provider.source,
                    type(error).__name__,
                )
        if persisted is None:
            raise ProviderDataError(
                "USD/TWD FX rate is unavailable; no non-future persisted rate exists"
            )
        return persisted

    @staticmethod
    def _snapshots(result: MarketDataRefreshResult) -> list[PositionSnapshot]:
        return [
            PositionSnapshot(
                snapshot_date=result.snapshot.snapshot_date, **item.model_dump()
            )
            for item in result.summary.positions
        ]
