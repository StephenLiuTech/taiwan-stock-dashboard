"""Immutable presentation-neutral facts for modular daily-report sections."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class AllocationItem:
    label: str
    name: str | None
    market_value: Decimal
    weight: Decimal


@dataclass(frozen=True)
class PortfolioAllocationSection:
    by_holding: tuple[AllocationItem, ...]
    by_market: tuple[AllocationItem, ...]
    by_instrument: tuple[AllocationItem, ...]
    unquoted_holdings: tuple[str, ...]
    classification_status: str = "Classification unavailable"


@dataclass(frozen=True)
class MarketSnapshotItem:
    display_name: str
    value: Decimal | None
    daily_change: Decimal | None
    daily_change_percentage: Decimal | None
    quoted_at: datetime | None
    source_status: str


@dataclass(frozen=True)
class MarketSnapshotSection:
    items: tuple[MarketSnapshotItem, ...] = ()
    status: str = "Market data unavailable"


@dataclass(frozen=True)
class UpcomingEventItem:
    event_date: date
    event_type: str
    symbol_or_scope: str
    title: str
    relevant_to_holding: bool
    source_status: str


@dataclass(frozen=True)
class UpcomingEventsSection:
    items: tuple[UpcomingEventItem, ...] = ()
    status: str = "No upcoming events"


@dataclass(frozen=True)
class DividendCalendarItem:
    symbol: str
    name: str
    ex_dividend_date: date
    record_date: date | None
    payment_date: date | None
    event: str
    dividend_per_share: Decimal | None
    eligible_quantity: Decimal
    estimated_cash_dividend: Decimal | None
    actual_cash_received: Decimal | None
    status: str
    source_status: str


@dataclass(frozen=True)
class DividendCalendarSection:
    items: tuple[DividendCalendarItem, ...] = ()
    status: str = "No persisted dividend events"
    hide_when_empty: bool = True
    estimated_annual_dividend: Decimal = Decimal("0")
    already_received: Decimal = Decimal("0")
    waiting_for_payment: Decimal = Decimal("0")
    upcoming_ex_date: Decimal = Decimal("0")
    unknown_payment_date: Decimal = Decimal("0")


@dataclass(frozen=True)
class NewsItem:
    headline: str
    publisher: str
    published_at: datetime
    summary: str
    url: str
    relevance_reason: str


@dataclass(frozen=True)
class NewsSection:
    items: tuple[NewsItem, ...] = ()
    status: str = "News provider not configured"


@dataclass(frozen=True)
class PortfolioInsightsSection:
    insights: tuple[str, ...] = ()
    status: str = "No portfolio insights available"


@dataclass(frozen=True)
class RiskItem:
    label: str
    value: str
    warning: bool = False


@dataclass(frozen=True)
class RiskMonitorSection:
    items: tuple[RiskItem, ...] = ()


@dataclass(frozen=True)
class WatchlistView:
    symbol: str
    market: str
    display_name: str | None
    target_price: Decimal | None
    buy_below_price: Decimal | None
    latest_price: Decimal | None
    quote_date: date | None
    notes: str | None


@dataclass(frozen=True)
class WatchlistSection:
    items: tuple[WatchlistView, ...] = ()


@dataclass(frozen=True)
class TransactionSummaryItem:
    transaction_type: str
    symbol: str
    name: str
    market: str
    quantity: Decimal
    price: Decimal
    gross_amount: Decimal
    fees: Decimal
    taxes: Decimal
    net_cash_impact: Decimal


@dataclass(frozen=True)
class TransactionSummarySection:
    items: tuple[TransactionSummaryItem, ...] = ()
    total_buy_fees: Decimal = Decimal("0")
    total_sell_fees: Decimal = Decimal("0")
    total_taxes: Decimal = Decimal("0")

    @property
    def total_trading_expenses(self) -> Decimal:
        return self.total_buy_fees + self.total_sell_fees + self.total_taxes


@dataclass(frozen=True)
class DailyReportSections:
    allocation: PortfolioAllocationSection | None = None
    market_snapshot: MarketSnapshotSection | None = None
    upcoming_events: UpcomingEventsSection | None = None
    dividend_calendar: DividendCalendarSection | None = None
    ai_news: NewsSection | None = None
    semiconductor_news: NewsSection | None = None
    insights: PortfolioInsightsSection | None = None
    risk: RiskMonitorSection | None = None
    watchlist: WatchlistSection | None = None
    transactions: TransactionSummarySection | None = None
