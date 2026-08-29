"""Pure deterministic replay of liability principal events."""

from datetime import date
from decimal import Decimal

from domain import LiabilityPrincipalEvent, LiabilityPrincipalPoint


class LiabilityPrincipalReplayError(ValueError):
    """A principal ledger violates an accounting invariant."""


class LiabilityPrincipalEngine:
    """Replay principal from zero in effective-date and sequence order."""

    @staticmethod
    def timeline(
        events: list[LiabilityPrincipalEvent],
    ) -> tuple[LiabilityPrincipalPoint, ...]:
        ordered = sorted(
            events,
            key=lambda item: (item.effective_date, item.sequence, item.id),
        )
        principal = Decimal("0")
        points: list[LiabilityPrincipalPoint] = []
        seen_orders: set[tuple[str, date, int]] = set()
        for event in ordered:
            order = (event.liability_id, event.effective_date, event.sequence)
            if order in seen_orders:
                raise LiabilityPrincipalReplayError(
                    "duplicate liability principal event sequence"
                )
            seen_orders.add(order)
            principal += event.principal_delta
            if principal < 0:
                raise LiabilityPrincipalReplayError(
                    f"liability principal became negative at {event.effective_date}"
                )
            if (
                event.resulting_principal is not None
                and event.resulting_principal != principal
            ):
                raise LiabilityPrincipalReplayError(
                    f"resulting principal mismatch for event {event.id}"
                )
            points.append(LiabilityPrincipalPoint(event, principal))
        return tuple(points)

    def principal_as_of(
        self, events: list[LiabilityPrincipalEvent], as_of: date
    ) -> Decimal:
        points = self.timeline(
            [event for event in events if event.effective_date <= as_of]
        )
        return points[-1].principal if points else Decimal("0")
