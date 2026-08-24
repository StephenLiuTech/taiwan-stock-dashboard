"""Immutable data returned by PAMS application use cases."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from domain import HoldingValuation as HoldingValuation
from domain import PortfolioAnalytics as PortfolioAnalytics
from domain import PortfolioValuation as PortfolioValuation


class UpdateMode(StrEnum):
    """Stable operational outcomes for a portfolio update."""

    UPDATED = "updated"
    ENRICHED = "enriched_existing_snapshot"
    DRY_RUN = "dry_run"
    SNAPSHOT_EXISTS = "no_update_snapshot_exists"
    SOURCES_UNSYNCHRONIZED = "no_update_sources_unsynchronized"


class VerificationLevel(StrEnum):
    """Severity of one verification item."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"


class HoldingChangeAction(StrEnum):
    """Supported persisted-holding reconciliation actions."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    UNCHANGED = "UNCHANGED"
    CLOSE = "CLOSE"


@dataclass(frozen=True)
class MarketAvailabilitySummary:
    """Official latest dates, synchronization, and joint ingestibility."""

    twse_latest_date: date | None
    tpex_latest_date: date | None
    commonly_ingestible_date: date | None

    @property
    def synchronized(self) -> bool:
        return (
            self.source_dates_available
            and self.twse_latest_date == self.tpex_latest_date
        )

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
    us_market_provider_status: str = "disabled"
    fx_provider_status: str = "disabled"
    latest_us_quote_date: date | None = None
    latest_usd_twd_rate_date: date | None = None
    active_us_holding_count: int = 0
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


@dataclass(frozen=True)
class LedgerPositionResult:
    """Application representation of one active transaction position."""

    symbol: str
    market: str
    currency: str
    quantity: Decimal
    average_cost: Decimal
    cost_basis: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True)
class ProjectedHoldingResult:
    """Dry-run holding projection returned at the application boundary."""

    id: str
    symbol: str
    name: str
    market: str
    currency: str
    quantity: Decimal
    average_cost: Decimal
    holding_type: str


@dataclass(frozen=True)
class RebuildHoldingsResult:
    """Complete dry-run transaction-ledger projection result."""

    positions: tuple[LedgerPositionResult, ...]
    projected_holdings: tuple[ProjectedHoldingResult, ...]
    total_realized_pnl: Decimal
    total_buy_fees: Decimal
    total_sell_fees: Decimal
    total_taxes: Decimal
    total_trading_expenses: Decimal
    transaction_count: int
    persisted: bool = False


@dataclass(frozen=True)
class HoldingChangeItem:
    """One projected difference between transaction and persisted holdings."""

    symbol: str
    action: HoldingChangeAction
    old_quantity: Decimal | None
    new_quantity: Decimal
    old_average_cost: Decimal | None
    new_average_cost: Decimal
    old_cost_basis: Decimal | None
    new_cost_basis: Decimal


@dataclass(frozen=True)
class HoldingChangePlan:
    """Immutable preview or applied holding reconciliation plan."""

    created_holdings: tuple[HoldingChangeItem, ...]
    updated_holdings: tuple[HoldingChangeItem, ...]
    unchanged_holdings: tuple[HoldingChangeItem, ...]
    closed_holdings: tuple[HoldingChangeItem, ...]
    warnings: tuple[str, ...]
    transaction_count: int
    projected_total_cost_basis: Decimal
    applied: bool = False

    @property
    def items(self) -> tuple[HoldingChangeItem, ...]:
        return (
            self.created_holdings
            + self.updated_holdings
            + self.unchanged_holdings
            + self.closed_holdings
        )


@dataclass(frozen=True)
class AddTransactionCommand:
    """Validated-boundary input for recording one transaction."""

    symbol: str
    market: str
    transaction_type: str
    trade_date: date
    quantity: Decimal
    price: Decimal
    fees: Decimal
    taxes: Decimal
    currency: str
    settlement_date: date | None = None
    transaction_id: str | None = None
    notes: str | None = None
    financing_type: str | None = None


@dataclass(frozen=True)
class TransactionRecord:
    """Immutable application representation of a persisted transaction."""

    id: str
    symbol: str
    market: str
    transaction_type: str
    trade_date: date
    settlement_date: date
    quantity: Decimal
    price: Decimal
    fees: Decimal
    taxes: Decimal
    currency: str
    notes: str | None
    financing_type: str | None = None
    gross_purchase_value: Decimal | None = None
    self_funded_amount: Decimal | None = None
    financed_principal: Decimal | None = None
    updated_holding_quantity: Decimal | None = None
    updated_margin_quantity: Decimal | None = None
    updated_margin_principal: Decimal | None = None


@dataclass(frozen=True)
class TransactionList:
    """Immutable filtered transaction query result."""

    transactions: tuple[TransactionRecord, ...]


@dataclass(frozen=True)
class HoldingQueryItem:
    """Transaction-derived holding enriched by shared current valuation."""

    symbol: str
    market: str
    quantity: Decimal
    average_cost: Decimal
    total_cost: Decimal
    latest_price: Decimal | None
    market_value: Decimal | None
    unrealized_pl: Decimal | None
    unrealized_return: Decimal | None
    transaction_count: int
    first_trade_date: date
    latest_trade_date: date
    quote_date: date | None = None


@dataclass(frozen=True)
class HoldingsQueryResult:
    """Ordered active transaction-derived holdings query result."""

    valuation_date: date | None
    holdings: tuple[HoldingQueryItem, ...]
    as_of_date: date | None = None
