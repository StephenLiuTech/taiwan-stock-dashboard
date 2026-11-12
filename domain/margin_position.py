"""Per-symbol positions financed by one aggregate margin liability."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from domain.enums import Currency, Market
from domain.models import utc_now


class MarginFinancingPosition(BaseModel):
    """Current principal and financed quantity for one market/symbol."""

    model_config = {"frozen": True}

    id: str
    liability_id: str
    market: Market
    symbol: str = Field(min_length=1, max_length=32)
    currency: Currency = Currency.TWD
    principal: Decimal = Field(ge=0)
    financed_quantity: Decimal = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_position(self) -> "MarginFinancingPosition":
        if self.market not in (Market.TWSE, Market.TPEX):
            raise ValueError("margin positions require a Taiwan market")
        if self.currency is not Currency.TWD:
            raise ValueError("margin positions require TWD currency")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


def margin_position_id(liability_id: str, market: Market, symbol: str) -> str:
    """Return the stable human-auditable identity for one financed symbol."""
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("margin position symbol cannot be blank")
    return f"margin-position-{liability_id}-{market.value.lower()}-{normalized.lower()}"
