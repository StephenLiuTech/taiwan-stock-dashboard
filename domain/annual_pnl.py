"""Immutable annual investment profit/loss domain values."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from domain.enums import Currency, InvestmentCostType
from domain.models import utc_now


@dataclass(frozen=True)
class RealizedSale:
    """One SELL transaction's moving-average realized result."""

    transaction_id: str
    trade_date: date
    symbol: str
    market: str
    currency: Currency
    quantity_sold: Decimal
    average_cost_basis: Decimal
    total_cost_basis: Decimal
    gross_proceeds: Decimal
    fees: Decimal
    taxes: Decimal
    net_proceeds: Decimal
    realized_pnl: Decimal
    realized_return: Decimal


class InvestmentCostEvent(BaseModel):
    """One dated investment expense not represented in SELL realized P/L."""

    model_config = {"frozen": True}

    id: str
    event_date: date
    cost_type: InvestmentCostType
    amount: Decimal = Field(ge=0)
    currency: Currency
    description: str | None = None
    source: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class AnnualPnlSnapshot(BaseModel):
    """Immutable calendar-year P/L totals at one portfolio snapshot date."""

    model_config = {"frozen": True}

    snapshot_date: date
    year: int
    reporting_currency: Currency = Currency.TWD
    realized_pnl_ytd: Decimal
    unrealized_pnl: Decimal
    dividend_income_ytd: Decimal
    financing_cost_ytd: Decimal = Field(default=Decimal("0"), ge=0)
    other_cost_ytd: Decimal = Field(default=Decimal("0"), ge=0)
    total_pnl_ytd: Decimal
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_totals(self) -> "AnnualPnlSnapshot":
        if self.year != self.snapshot_date.year:
            raise ValueError("annual P/L year must match snapshot date")
        expected = (
            self.realized_pnl_ytd
            + self.unrealized_pnl
            + self.dividend_income_ytd
            - self.financing_cost_ytd
            - self.other_cost_ytd
        )
        if self.total_pnl_ytd != expected:
            raise ValueError("annual P/L total does not match its components")
        return self
