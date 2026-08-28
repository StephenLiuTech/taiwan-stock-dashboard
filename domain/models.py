"""PAMS domain models with structural validation."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Self
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from domain.enums import (
    Currency,
    DividendStatus,
    FinancingType,
    HoldingType,
    LiabilityType,
    Market,
    TransactionType,
)

NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
UnitDecimal = Annotated[Decimal, Field(ge=0, le=1)]


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


class DomainModel(BaseModel):
    """Base behavior shared by immutable-boundary domain records."""

    model_config = {"use_enum_values": False}


class SymbolModel(DomainModel):
    """Base model for records identified by a security symbol."""

    symbol: str = Field(min_length=1, max_length=32)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        """Normalize symbols for stable matching and persistence."""
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol cannot be blank")
        return normalized


class Holding(SymbolModel):
    """An owned portfolio position."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1)
    market: Market
    currency: Currency
    quantity: NonNegativeDecimal
    average_cost: NonNegativeDecimal
    holding_type: HoldingType = HoldingType.STOCK
    is_pledged: bool = False
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_timestamps(self) -> Self:
        """Ensure the update timestamp does not precede creation."""
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


class Transaction(SymbolModel):
    """A purchase or sale of a security."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    market: Market
    transaction_type: TransactionType
    trade_date: date
    settlement_date: date
    quantity: NonNegativeDecimal
    price: NonNegativeDecimal
    fees: NonNegativeDecimal = Decimal("0")
    taxes: NonNegativeDecimal = Decimal("0")
    currency: Currency
    financing_type: FinancingType | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        """Ensure settlement is not earlier than the trade."""
        if self.settlement_date < self.trade_date:
            raise ValueError("settlement_date cannot precede trade_date")
        if self.financing_type is FinancingType.MARGIN:
            if self.transaction_type is not TransactionType.BUY:
                raise ValueError(
                    "margin financing is supported only for BUY transactions"
                )
            if self.market not in (Market.TWSE, Market.TPEX):
                raise ValueError(
                    "margin financing is supported only for Taiwan markets"
                )
            if self.currency is not Currency.TWD:
                raise ValueError("margin financing requires TWD currency")
            if self.quantity <= 0 or self.price <= 0:
                raise ValueError(
                    "margin financing requires positive quantity and price"
                )
        return self


class CorporateAction(SymbolModel):
    """A non-cash quantity conversion that preserves position cost basis."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    market: Market
    effective_date: date
    quantity_multiplier: Decimal = Field(gt=0)
    source: str = Field(min_length=1, max_length=128)
    reference: str | None = Field(default=None, max_length=256)
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class WatchlistItem(SymbolModel):
    """A manually maintained security of interest without advice semantics."""

    market: Market
    display_name: str | None = None
    target_price: NonNegativeDecimal | None = None
    buy_below_price: NonNegativeDecimal | None = None
    notes: str | None = None


class Dividend(SymbolModel):
    """A declared or received dividend."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    market: Market
    ex_dividend_date: date
    payment_date: date | None = None
    amount_per_share: NonNegativeDecimal
    currency: Currency
    shares_eligible: NonNegativeDecimal
    gross_amount: NonNegativeDecimal
    withholding_tax: NonNegativeDecimal = Decimal("0")
    net_amount: NonNegativeDecimal
    status: DividendStatus = DividendStatus.EXPECTED

    @model_validator(mode="after")
    def validate_dates_and_amounts(self) -> Self:
        """Ensure payment ordering and internally consistent totals."""
        if self.payment_date and self.payment_date < self.ex_dividend_date:
            raise ValueError("payment_date cannot precede ex_dividend_date")
        if self.withholding_tax > self.gross_amount:
            raise ValueError("withholding_tax cannot exceed gross_amount")
        if self.net_amount != self.gross_amount - self.withholding_tax:
            raise ValueError("net_amount must equal gross_amount minus withholding_tax")
        return self


class DividendEvent(SymbolModel):
    """One normalized official dividend distribution announcement."""

    source_event_id: str = Field(min_length=1)
    market: Market
    name: str
    dividend_year: int
    ex_dividend_date: date
    record_date: date | None = None
    payment_date: date | None = None
    cash_dividend_per_share: NonNegativeDecimal | None = None
    stock_dividend_per_share: NonNegativeDecimal | None = None
    source: str = Field(min_length=1)
    source_updated_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if self.payment_date and self.payment_date < self.ex_dividend_date:
            raise ValueError("payment_date cannot precede ex_dividend_date")
        return self


class Liability(DomainModel):
    """A debt included in net asset calculations."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    liability_type: LiabilityType
    principal: NonNegativeDecimal
    annual_interest_rate: UnitDecimal | None = None
    currency: Currency
    start_date: date | None = None
    maturity_date: date | None = None
    collateral_description: str | None = None
    financed_symbol: str | None = None
    financed_quantity: NonNegativeDecimal | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        """Ensure maturity does not precede the liability start."""
        if (
            self.start_date
            and self.maturity_date
            and self.maturity_date < self.start_date
        ):
            raise ValueError("maturity_date cannot precede start_date")
        return self


class PriceQuote(SymbolModel):
    """An end-of-day market price observation."""

    market: Market
    trade_date: date
    close_price: NonNegativeDecimal
    previous_close: NonNegativeDecimal | None = None
    currency: Currency
    source: str = Field(min_length=1)
    fetched_at: datetime = Field(default_factory=utc_now)


class FxRate(DomainModel):
    """One persisted native-to-reporting currency conversion rate."""

    base_currency: Currency
    quote_currency: Currency
    rate_date: date
    rate: Decimal = Field(gt=0)
    source: str = Field(min_length=1)
    fetched_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        if self.base_currency == self.quote_currency:
            raise ValueError("FX base and quote currencies must differ")
        return self


class PositionValuation(SymbolModel):
    """Calculated valuation for one holding."""

    holding_id: str
    quantity: NonNegativeDecimal
    average_cost: NonNegativeDecimal
    close_price: NonNegativeDecimal
    cost_basis: NonNegativeDecimal
    market_value: NonNegativeDecimal
    unrealized_pnl: Decimal
    unrealized_return: Decimal
    portfolio_weight: UnitDecimal
    daily_value_change: Decimal
    daily_return: Decimal | None
    market: Market = Market.TWSE
    native_currency: Currency = Currency.TWD
    quote_date: date | None = None
    fx_rate: Decimal = Field(default=Decimal("1"), gt=0)
    fx_rate_date: date | None = None


class PositionSnapshot(PositionValuation):
    """One holding's immutable valuation for one portfolio snapshot date."""

    snapshot_date: date


class DailyPositionPerformance(DomainModel):
    """One position's contribution to the portfolio's daily movement."""

    holding_id: str
    profit_loss: Decimal
    return_percentage: Decimal
    portfolio_profit_loss_share: Decimal | None


class DailyPortfolioPerformance(DomainModel):
    """Aggregate one-day movement derived from persisted position valuations."""

    profit_loss: Decimal
    return_percentage: Decimal
    previous_market_value: Decimal
    positions: tuple[DailyPositionPerformance, ...]


class DailySnapshot(DomainModel):
    """Persisted daily portfolio totals."""

    snapshot_date: date
    total_market_value: NonNegativeDecimal
    total_cost_basis: NonNegativeDecimal
    total_unrealized_pnl: Decimal
    total_liabilities: NonNegativeDecimal
    net_asset_value: Decimal
    leverage_ratio: NonNegativeDecimal
    high_water_mark: Decimal
    drawdown: Decimal = Field(le=0)
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def total_return(self) -> Decimal:
        """Return the persisted snapshot's unrealized return."""
        if self.total_cost_basis == 0:
            return Decimal("0")
        return self.total_unrealized_pnl / self.total_cost_basis


class PortfolioSummary(DomainModel):
    """Calculated portfolio totals for a valuation date."""

    valuation_date: date
    positions: list[PositionValuation]
    total_market_value: NonNegativeDecimal
    total_cost_basis: NonNegativeDecimal
    total_unrealized_pnl: Decimal
    total_liabilities: NonNegativeDecimal
    net_asset_value: Decimal
    leverage_ratio: NonNegativeDecimal
