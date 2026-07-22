"""Immutable data returned by PAMS application use cases."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path


class UpdateMode(StrEnum):
    """Stable operational outcomes for a portfolio update."""

    UPDATED = "updated"
    DRY_RUN = "dry_run"
    SOURCES_UNSYNCHRONIZED = "no_update_sources_unsynchronized"


class VerificationLevel(StrEnum):
    """Severity of one verification item."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"


@dataclass(frozen=True)
class MarketAvailabilitySummary:
    """Official latest-only market dates and their joint ingestibility."""

    twse_latest_date: date | None
    tpex_latest_date: date | None
    commonly_ingestible_date: date | None

    @property
    def synchronized(self) -> bool:
        return self.commonly_ingestible_date is not None

    @property
    def source_dates_available(self) -> bool:
        """Return whether both official source dates were reachable."""
        return self.twse_latest_date is not None and self.tpex_latest_date is not None


@dataclass(frozen=True)
class PositionSummary:
    """One valued holding prepared for presentation."""

    symbol: str
    name: str
    market: str
    shares: Decimal
    average_cost: Decimal
    close_price: Decimal
    previous_close: Decimal | None
    daily_change_percentage: Decimal | None
    market_value: Decimal
    unrealized_pnl: Decimal
    unrealized_return: Decimal
    portfolio_weight: Decimal


@dataclass(frozen=True)
class PortfolioTotals:
    """Portfolio-level values produced by an update."""

    total_market_value: Decimal
    total_cost_basis: Decimal
    total_unrealized_pnl: Decimal
    total_liabilities: Decimal
    net_asset_value: Decimal
    liability_ratio: Decimal
    position_count: int


@dataclass(frozen=True)
class HoldingOverview:
    """One persisted holding valuation for dashboard queries."""

    symbol: str
    name: str
    market: str
    shares: Decimal
    average_cost: Decimal
    latest_price: Decimal
    market_value: Decimal
    cost_basis: Decimal
    unrealized_pnl: Decimal
    unrealized_return: Decimal
    portfolio_weight: Decimal
    quote_date: date


@dataclass(frozen=True)
class UpdateResult:
    """Presentation-neutral result of the update workflow."""

    mode: UpdateMode
    database_path: Path
    requested_date: date | None
    verified_source_date: date | None
    availability: MarketAvailabilitySummary | None
    positions: tuple[PositionSummary, ...] = ()
    totals: PortfolioTotals | None = None


@dataclass(frozen=True)
class PortfolioOverview:
    """Latest persisted portfolio values and operational status."""

    database_path: Path
    latest_quote_date: date | None
    latest_daily_snapshot: date | None
    latest_position_snapshot: date | None
    holdings_count: int
    liabilities_count: int
    schema_version: int | None
    database_size_bytes: int
    market_availability: MarketAvailabilitySummary
    market_value: Decimal | None = None
    net_equity: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    todays_pnl: Decimal | None = None
    total_liabilities: Decimal | None = None
    leverage_ratio: Decimal | None = None
    holdings: tuple[HoldingOverview, ...] = ()


@dataclass(frozen=True)
class PortfolioHistoryPoint:
    """One persisted aggregate daily portfolio observation."""

    snapshot_date: date
    market_value: Decimal
    net_equity: Decimal
    total_liabilities: Decimal


@dataclass(frozen=True)
class PortfolioHistory:
    """Chronological immutable portfolio history."""

    points: tuple[PortfolioHistoryPoint, ...]


@dataclass(frozen=True)
class VerificationItem:
    """One named system verification outcome."""

    name: str
    level: VerificationLevel
    detail: str


@dataclass(frozen=True)
class VerificationReport:
    """Complete presentation-neutral system verification result."""

    items: tuple[VerificationItem, ...]

    @property
    def failed(self) -> bool:
        return any(item.level is VerificationLevel.FAIL for item in self.items)


@dataclass(frozen=True)
class DemoDataResult:
    """Result of atomically creating one synthetic demo database."""

    database_path: Path
    holdings_count: int
    liabilities_count: int
    quote_date: date
    history_points: int
    total_market_value: Decimal
    total_cost_basis: Decimal
    total_unrealized_pnl: Decimal
    total_liabilities: Decimal
    net_equity: Decimal
