"""Immutable, replayable liability-principal facts."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from domain.enums import LiabilityPrincipalEventType
from domain.models import utc_now


class LiabilityPrincipalEvent(BaseModel):
    """One dated change to a liability account's principal."""

    model_config = {"frozen": True}

    id: str
    liability_id: str
    effective_date: date
    sequence: int = Field(ge=0)
    event_type: LiabilityPrincipalEventType
    principal_delta: Decimal
    resulting_principal: Decimal | None = Field(default=None, ge=0)
    source: str
    reference: str | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


@dataclass(frozen=True)
class LiabilityPrincipalPoint:
    """Principal immediately after one deterministically ordered event."""

    event: LiabilityPrincipalEvent
    principal: Decimal
