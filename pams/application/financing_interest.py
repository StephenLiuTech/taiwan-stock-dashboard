"""Application orchestration for deterministic financing-interest accrual."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType

from domain import Currency, InvestmentCostEvent, InvestmentCostType, LiabilityType
from repositories import (
    InvestmentCostEventRepository,
    LiabilityPrincipalEventRepository,
    LiabilityRepository,
)
from services import FinancingInterestEngine, LiabilityPrincipalEngine

AUTOMATIC_FINANCING_ACCRUAL_START = date(2026, 8, 29)
AUTOMATIC_FINANCING_SOURCE = "automatic financing accrual"


class FinancingInterestError(RuntimeError):
    """Daily financing interest cannot be derived safely."""


@dataclass(frozen=True)
class FinancingInterestItem:
    """One calculated liability/day observation and persistence status."""

    liability_id: str
    accrual_date: date
    principal: Decimal
    annual_rate: Decimal
    daily_interest: Decimal
    event_id: str
    persisted: bool


@dataclass(frozen=True)
class FinancingInterestResult:
    """Immutable outcome of ensuring an inclusive accrual range."""

    start_date: date
    end_date: date
    items: tuple[FinancingInterestItem, ...]
    inserted: int


def financing_interest_event_id(liability_id: str, accrual_date: date) -> str:
    """Return the stable semantic identity for one liability/day expense."""
    return f"financing-interest:{liability_id}:{accrual_date.isoformat()}"


class FinancingInterestUseCase:
    """Replay principal and ensure missing calendar-day interest events."""

    def __init__(
        self,
        liabilities: LiabilityRepository,
        principal_events: LiabilityPrincipalEventRepository,
        costs: InvestmentCostEventRepository,
        annual_rates: Mapping[LiabilityType, Decimal],
        *,
        interest_engine: FinancingInterestEngine | None = None,
        principal_engine: LiabilityPrincipalEngine | None = None,
    ) -> None:
        self._liabilities = liabilities
        self._principal_events = principal_events
        self._costs = costs
        self._annual_rates = MappingProxyType(dict(annual_rates))
        self._interest_engine = interest_engine or FinancingInterestEngine()
        self._principal_engine = principal_engine or LiabilityPrincipalEngine()

    def inspect(self, accrual_date: date) -> tuple[FinancingInterestItem, ...]:
        """Calculate one date without writing or filling earlier dates."""
        existing = {
            item.id
            for item in self._costs.list_between_dates(accrual_date, accrual_date)
        }
        return self._items_for_date(accrual_date, existing)

    def ensure_through(
        self, end_date: date, *, persist: bool = True
    ) -> FinancingInterestResult:
        """Fill every missing calendar date from the structural boundary."""
        start = AUTOMATIC_FINANCING_ACCRUAL_START
        if end_date < start:
            return FinancingInterestResult(start, end_date, (), 0)
        existing = {
            item.id
            for item in self._costs.list_between_dates(start, end_date)
            if item.cost_type is InvestmentCostType.FINANCING
        }
        items: list[FinancingInterestItem] = []
        events: list[InvestmentCostEvent] = []
        current = start
        while current <= end_date:
            calculated = self._items_for_date(current, existing)
            items.extend(calculated)
            for item in calculated:
                if item.daily_interest == 0 or item.persisted:
                    continue
                events.append(
                    InvestmentCostEvent(
                        id=item.event_id,
                        event_date=current,
                        cost_type=InvestmentCostType.FINANCING,
                        amount=item.daily_interest,
                        currency=Currency.TWD,
                        description=(
                            f"{item.liability_id}; {current.isoformat()}; "
                            f"principal={item.principal}; rate={item.annual_rate}; "
                            "Actual/365; v11 principal replay"
                        ),
                        source=AUTOMATIC_FINANCING_SOURCE,
                        created_at=datetime.combine(current, datetime.min.time(), UTC),
                    )
                )
            current += timedelta(days=1)
        inserted = self._costs.insert_many_if_absent(events) if persist else 0
        return FinancingInterestResult(start, end_date, tuple(items), inserted)

    def _items_for_date(
        self, accrual_date: date, existing: set[str]
    ) -> tuple[FinancingInterestItem, ...]:
        if accrual_date < AUTOMATIC_FINANCING_ACCRUAL_START:
            return ()
        values = self._principal_events.list_all()
        result = []
        for liability in sorted(self._liabilities.list_all(), key=lambda item: item.id):
            rate = self._annual_rates.get(liability.liability_type)
            if rate is None:
                continue
            principal = self._principal_engine.principal_as_of(
                [item for item in values if item.liability_id == liability.id],
                accrual_date,
            )
            calculated = self._interest_engine.calculate(principal, rate)
            event_id = financing_interest_event_id(liability.id, accrual_date)
            result.append(
                FinancingInterestItem(
                    liability.id,
                    accrual_date,
                    principal,
                    rate,
                    calculated.amount,
                    event_id,
                    event_id in existing,
                )
            )
        return tuple(result)
