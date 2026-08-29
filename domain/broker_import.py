"""Structured provenance for one applied brokerage source row."""

from datetime import datetime

from pydantic import BaseModel, Field

from domain.models import utc_now


class BrokerImportRecord(BaseModel):
    """Immutable link between source evidence and one persisted domain entity."""

    model_config = {"frozen": True}

    id: str
    broker: str = Field(min_length=1)
    source_fingerprint: str = Field(min_length=64, max_length=64)
    source_row_reference: str = Field(min_length=1)
    record_type: str = Field(min_length=1)
    domain_entity_type: str = Field(min_length=1)
    domain_entity_id: str = Field(min_length=1)
    normalized_identity: str | None = None
    imported_at: datetime = Field(default_factory=utc_now)
    notes: str | None = None
