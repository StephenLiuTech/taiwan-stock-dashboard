"""Application orchestration for liability-principal history."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from domain import LiabilityPrincipalEvent, LiabilityPrincipalPoint
from repositories import LiabilityPrincipalEventRepository, LiabilityRepository
from services import LiabilityPrincipalEngine


class LiabilityPrincipalError(RuntimeError):
    """Principal history cannot be persisted or reconciled safely."""


@dataclass(frozen=True)
class LiabilityPrincipalBackfillResult:
    """Outcome of an idempotent, reconciled principal-history backfill."""

    attempted: int
    inserted: int
    reconciled_principals: tuple[tuple[str, Decimal], ...]


class LiabilityPrincipalUseCase:
    """Query, replay, and controlled-backfill principal history."""

    def __init__(
        self,
        events: LiabilityPrincipalEventRepository,
        liabilities: LiabilityRepository,
        engine: LiabilityPrincipalEngine | None = None,
    ) -> None:
        self.events = events
        self.liabilities = liabilities
        self.engine = engine or LiabilityPrincipalEngine()

    def history(
        self, liability_id: str | None = None
    ) -> tuple[LiabilityPrincipalPoint, ...]:
        values = (
            self.events.list_by_liability(liability_id)
            if liability_id
            else self.events.list_all()
        )
        if liability_id:
            return self.engine.timeline(values)
        points: list[LiabilityPrincipalPoint] = []
        for item_id in sorted({event.liability_id for event in values}):
            points.extend(
                self.engine.timeline(
                    [event for event in values if event.liability_id == item_id]
                )
            )
        return tuple(points)

    def principal(self, liability_id: str, as_of: date) -> Decimal:
        return self.engine.principal_as_of(
            self.events.list_by_liability(liability_id), as_of
        )

    def backfill(
        self,
        events: list[LiabilityPrincipalEvent],
        *,
        as_of: date,
        expected_principals: dict[str, Decimal],
    ) -> LiabilityPrincipalBackfillResult:
        """Insert approved facts only after replay/current-balance reconciliation."""
        existing = self.events.list_all()
        existing_by_id = {event.id: event for event in existing}
        for event in events:
            prior = existing_by_id.get(event.id)
            if prior is not None and prior != event:
                raise LiabilityPrincipalError(
                    f"existing principal event conflicts with approved event {event.id}"
                )
        combined = dict(existing_by_id)
        combined.update({event.id: event for event in events})
        reconciled = []
        for liability_id, expected in sorted(expected_principals.items()):
            replayed = self.engine.principal_as_of(
                [
                    event
                    for event in combined.values()
                    if event.liability_id == liability_id
                ],
                as_of,
            )
            liability = self.liabilities.get_by_id(liability_id)
            if (
                liability is None
                or liability.principal != expected
                or replayed != expected
            ):
                raise LiabilityPrincipalError(
                    f"principal history does not reconcile for {liability_id}"
                )
            reconciled.append((liability_id, replayed))
        inserted = self.events.insert_many_if_absent(events)
        return LiabilityPrincipalBackfillResult(
            len(events), inserted, tuple(reconciled)
        )
