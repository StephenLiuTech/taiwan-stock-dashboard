"""Auditable acquisition-lot matches for an explicitly matched sale."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from domain.models import utc_now


class LotAllocation(BaseModel):
    """One sale's quantity and price-only cost drawn from a specific BUY."""

    model_config = {"frozen": True}

    sell_transaction_id: str
    buy_transaction_id: str
    matched_quantity: Decimal = Field(gt=0)
    matched_trade_cost: Decimal = Field(ge=0)
    allocated_buy_fee: Decimal = Field(ge=0)
    source: str
    source_reference: str
    created_at: datetime = Field(default_factory=utc_now)
