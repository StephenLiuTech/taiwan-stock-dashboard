"""Application orchestration for optional modular daily-report sections."""

# ruff: noqa: ANN001, ANN202, ANN205

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from domain import (
    DailyReportSections,
    MarketSnapshotSection,
    NewsItem,
    NewsSection,
    PositionSnapshot,
    UpcomingEventItem,
    WatchlistSection,
    WatchlistView,
)
from repositories import (
    DividendEventRepository,
    HoldingRepository,
    LiabilityRepository,
    PriceQuoteRepository,
    TransactionRepository,
    WatchlistRepository,
)
from services import NewsService, ReportSectionService, TransactionEngine

LOGGER = logging.getLogger(__name__)


class MarketSnapshotProvider(Protocol):
    def get_snapshot(self, report_date: date) -> MarketSnapshotSection: ...


class NewsProvider(Protocol):
    def get_news(self, report_date: date) -> tuple[NewsItem, ...]: ...


class EventProvider(Protocol):
    def get_events(self, report_date: date) -> tuple[UpcomingEventItem, ...]: ...


@dataclass(frozen=True)
class ReportSectionSettings:
    show_allocation: bool = True
    show_market_snapshot: bool = True
    show_upcoming_events: bool = True
    show_dividends: bool = True
    show_ai_news: bool = True
    show_semiconductor_news: bool = True
    show_insights: bool = True
    show_risk: bool = True
    show_watchlist: bool = True
    show_transactions: bool = True
    event_horizon_days: int = 30
    dividend_scope: str = "current_year"
    hide_empty_optional_sections: bool = True
    news_limit: int = 5
    risk_single_holding_warning: Decimal = Decimal("0.30")
    risk_top3_warning: Decimal = Decimal("0.70")
    risk_market_warning: Decimal = Decimal("0.80")


class BuildReportSectionsUseCase:
    """Load report inputs and delegate deterministic calculations to services."""

    def __init__(
        self,
        holdings: HoldingRepository,
        transactions: TransactionRepository,
        dividends: DividendEventRepository,
        quotes: PriceQuoteRepository,
        watchlist: WatchlistRepository,
        settings: ReportSectionSettings,
        *,
        market_provider: MarketSnapshotProvider | None = None,
        event_provider: EventProvider | None = None,
        ai_news_provider: NewsProvider | None = None,
        semiconductor_news_provider: NewsProvider | None = None,
        liabilities: LiabilityRepository | None = None,
    ) -> None:
        self.holdings = holdings
        self.transactions = transactions
        self.dividends = dividends
        self.quotes = quotes
        self.watchlist = watchlist
        self.settings = settings
        self.market_provider = market_provider
        self.event_provider = event_provider
        self.ai_news_provider = ai_news_provider
        self.semiconductor_news_provider = semiconductor_news_provider
        self.liabilities = liabilities

    def execute(
        self,
        report_date: date,
        positions: list[PositionSnapshot],
        daily_profit_loss: Decimal,
        *,
        total_market_value: Decimal | None = None,
        net_asset_value: Decimal | None = None,
        liability_ratio: Decimal | None = None,
    ) -> DailyReportSections:
        holdings = self.holdings.list_all()
        transactions = self.transactions.list_all()
        names_by_symbol = {item.symbol: item.name for item in holdings}
        allocation = ReportSectionService.allocation(positions, holdings)
        dividend_start, dividend_end = self._dividend_period(report_date)
        dividend_values = self.dividends.list_filtered(
            start_date=dividend_start, end_date=dividend_end
        )
        active_keys = {
            (item.symbol, item.market) for item in holdings if item.quantity > 0
        }
        dividend_values = [
            item
            for item in dividend_values
            if (item.symbol, item.market) in active_keys
        ]
        eligible = self._eligible_quantities(dividend_values, transactions, holdings)
        dividends = ReportSectionService.dividends(
            report_date,
            dividend_values,
            eligible,
        )
        if self.settings.hide_empty_optional_sections and not dividends.items:
            dividends_for_report = None
        else:
            dividends_for_report = dividends
        liability_values = self.liabilities.list_all() if self.liabilities else []
        financing = (
            ReportSectionService.financing(
                liability_values,
                total_market_value,
                net_asset_value,
                liability_ratio,
            )
            if liability_values
            and total_market_value is not None
            and net_asset_value is not None
            and liability_ratio is not None
            else None
        )
        currency_exposure = ReportSectionService.currency_exposure(positions, holdings)
        events_failed = False
        if self.event_provider is None:
            external_events = ()
        else:
            try:
                external_events = self.event_provider.get_events(report_date)
            except Exception as error:
                self._warn_optional("upcoming_events", error)
                external_events = ()
                events_failed = True
        upcoming = ReportSectionService.upcoming(
            report_date,
            self.settings.event_horizon_days,
            dividends,
            {item.symbol for item in holdings if item.quantity > 0},
            external_events,
        )
        if events_failed and not upcoming.items:
            upcoming = type(upcoming)(
                status="Upcoming events unavailable (provider failure)"
            )
        return DailyReportSections(
            allocation=allocation if self.settings.show_allocation else None,
            market_snapshot=(
                self._market(report_date)
                if self.settings.show_market_snapshot
                else None
            ),
            upcoming_events=upcoming if self.settings.show_upcoming_events else None,
            dividend_calendar=(
                dividends_for_report if self.settings.show_dividends else None
            ),
            financing_leverage=financing,
            currency_exposure=currency_exposure,
            ai_news=(
                self._news("ai_news", self.ai_news_provider, report_date)
                if self.settings.show_ai_news
                else None
            ),
            semiconductor_news=(
                self._news(
                    "semiconductor_news", self.semiconductor_news_provider, report_date
                )
                if self.settings.show_semiconductor_news
                else None
            ),
            insights=(
                ReportSectionService.insights(
                    allocation, positions, daily_profit_loss, dividends
                )
                if self.settings.show_insights
                else None
            ),
            risk=(
                ReportSectionService.risk(
                    allocation,
                    positions,
                    single_threshold=self.settings.risk_single_holding_warning,
                    top3_threshold=self.settings.risk_top3_warning,
                    market_threshold=self.settings.risk_market_warning,
                )
                if self.settings.show_risk
                else None
            ),
            watchlist=self._watchlist() if self.settings.show_watchlist else None,
            transactions=(
                ReportSectionService.transaction_summary(
                    report_date, transactions, names_by_symbol
                )
                if self.settings.show_transactions
                else None
            ),
        )

    def _eligible_quantities(self, dividends, transactions, holdings):
        engine = TransactionEngine()
        result = {}
        for dividend in dividends:
            filtered = [
                item
                for item in transactions
                if item.trade_date < dividend.ex_dividend_date
            ]
            projected = engine.project_transaction_holdings(filtered, holdings)
            result[(dividend.symbol, dividend.ex_dividend_date)] = sum(
                (
                    item.quantity
                    for item in projected
                    if item.symbol == dividend.symbol and item.market == dividend.market
                ),
                Decimal("0"),
            )
        return result

    def _dividend_period(self, report_date: date):
        if self.settings.dividend_scope == "current_year":
            return date(report_date.year, 1, 1), date(report_date.year, 12, 31)
        if self.settings.dividend_scope == "next_90_days":
            return report_date, report_date + timedelta(days=90)
        if self.settings.dividend_scope == "all":
            return None, None
        raise ValueError(f"Invalid dividend scope: {self.settings.dividend_scope}")

    def _market(self, report_date: date) -> MarketSnapshotSection:
        if self.market_provider is None:
            return MarketSnapshotSection()
        return self._optional(
            "market_snapshot",
            lambda: self.market_provider.get_snapshot(report_date),
            MarketSnapshotSection(status="Market data unavailable (provider failure)"),
        )

    def _news(
        self, name: str, provider: NewsProvider | None, report_date: date
    ) -> NewsSection:
        if provider is None:
            return NewsSection()
        try:
            values = provider.get_news(report_date)
        except Exception as error:
            self._warn_optional(name, error)
            return NewsSection(status="News unavailable (provider failure)")
        return NewsService.select(values, self.settings.news_limit)

    def _watchlist(self) -> WatchlistSection:
        items = []
        for item in self.watchlist.list_all():
            try:
                quote = self.quotes.get_latest(item.symbol, item.market.value)
            except Exception as error:
                LOGGER.warning(
                    "optional report section failed",
                    extra={
                        "section": "watchlist_quotes",
                        "error_type": type(error).__name__,
                    },
                )
                quote = None
            items.append(
                WatchlistView(
                    item.symbol,
                    item.market.value,
                    item.display_name,
                    item.target_price,
                    item.buy_below_price,
                    quote.close_price if quote else None,
                    quote.trade_date if quote else None,
                    item.notes,
                )
            )
        return WatchlistSection(tuple(items))

    @staticmethod
    def _optional(name: str, operation, fallback):
        try:
            return operation()
        except Exception as error:
            BuildReportSectionsUseCase._warn_optional(name, error)
            return fallback

    @staticmethod
    def _warn_optional(name: str, error: Exception) -> None:
        LOGGER.warning(
            "optional report section failed",
            extra={"section": name, "error_type": type(error).__name__},
        )
