"""Persisted historical Stock Net Equity facts and provenance."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from domain.models import utc_now


class StockNetEquityQuality(StrEnum):
    VERIFIED = "VERIFIED"
    ESTIMATED_LIABILITY = "ESTIMATED_LIABILITY"
    UNKNOWN = "UNKNOWN"


class HistoricalStockNetEquity(BaseModel):
    """One imported daily value kept separate from accounting snapshots."""

    model_config = {"frozen": True}

    snapshot_date: date
    total_market_value: Decimal | None = Field(default=None, ge=0)
    pledge_debt: Decimal | None = Field(default=None, ge=0)
    margin_debt: Decimal | None = Field(default=None, ge=0)
    total_liabilities: Decimal | None = Field(default=None, ge=0)
    stock_net_equity: Decimal | None = None
    quality_status: StockNetEquityQuality
    source: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    imported_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_quality(self) -> "HistoricalStockNetEquity":
        values = (
            self.total_market_value,
            self.pledge_debt,
            self.margin_debt,
            self.total_liabilities,
            self.stock_net_equity,
        )
        if self.quality_status is not StockNetEquityQuality.UNKNOWN and any(
            value is None for value in values
        ):
            raise ValueError("known Stock Net Equity rows require all monetary fields")
        return self
