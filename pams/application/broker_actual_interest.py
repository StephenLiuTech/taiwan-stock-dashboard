"""Controlled recording of broker-reported financing interest settlements."""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from domain import (
    Currency,
    InvestmentCostEvent,
    InvestmentCostType,
    LiabilityType,
    Market,
)
from pams.application.financing_interest import financing_interest_event_id
from repositories import InvestmentCostEventRepository, LiabilityRepository

ACTUAL_INTEREST_PREFIX = "broker-financing-interest:"
REVERSAL_INTEREST_PREFIX = "broker-financing-interest-reversal:"
_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class BrokerActualInterestError(RuntimeError):
    """A broker actual-interest event cannot be recorded safely."""


@dataclass(frozen=True)
class RecordBrokerActualInterestCommand:
    """One externally evidenced financing-interest settlement."""

    event_date: date
    amount: Decimal
    liability_id: str
    market: Market
    symbol: str
    sell_order: str
    source: str


@dataclass(frozen=True)
class BrokerActualInterestPlan:
    """Auditable dry-run/apply result for one settlement."""

    event: InvestmentCostEvent
    replacement_event_id: str
    replaced_accrual_amount: Decimal
    reversal_event: InvestmentCostEvent | None
    events_to_insert: tuple[InvestmentCostEvent, ...]
    inserted: int
    already_recorded: bool
    annual_financing_cost_delta: Decimal
    applied: bool


def broker_actual_interest_event_id(command: RecordBrokerActualInterestCommand) -> str:
    """Return a stable identity tied to the broker SELL reference."""
    return (
        f"{ACTUAL_INTEREST_PREFIX}{command.liability_id}:"
        f"{command.event_date.isoformat()}:{command.market.value}:"
        f"{command.symbol}:{command.sell_order}"
    )


def broker_actual_interest_reversal_id(
    command: RecordBrokerActualInterestCommand,
) -> str:
    """Return the stable audit identity for a same-day accrual reversal."""
    return (
        f"{REVERSAL_INTEREST_PREFIX}{command.liability_id}:"
        f"{command.event_date.isoformat()}:{command.market.value}:"
        f"{command.symbol}:{command.sell_order}"
    )


def is_broker_actual_interest(event: InvestmentCostEvent) -> bool:
    """Return whether an event is an actual broker settlement, not a reversal."""
    return event.id.startswith(ACTUAL_INTEREST_PREFIX)


def is_broker_actual_interest_reversal(event: InvestmentCostEvent) -> bool:
    """Return whether an event reverses a superseded automatic accrual."""
    return event.id.startswith(REVERSAL_INTEREST_PREFIX)


def replaced_automatic_interest_id(event: InvestmentCostEvent) -> str | None:
    """Return the automatic daily event superseded by an actual settlement."""
    if not is_broker_actual_interest(event):
        return None
    parts = event.id.removeprefix(ACTUAL_INTEREST_PREFIX).rsplit(":", 4)
    if len(parts) != 5:
        return None
    liability_id, event_date, _, _, _ = parts
    try:
        parsed_date = date.fromisoformat(event_date)
    except ValueError:
        return None
    return financing_interest_event_id(liability_id, parsed_date)


class RecordBrokerActualInterestUseCase:
    """Record actual interest while preserving and reversing superseded accruals."""

    def __init__(
        self,
        costs: InvestmentCostEventRepository,
        liabilities: LiabilityRepository,
    ) -> None:
        self._costs = costs
        self._liabilities = liabilities

    def execute(
        self, command: RecordBrokerActualInterestCommand, *, apply: bool
    ) -> BrokerActualInterestPlan:
        """Preview or atomically insert the settlement and required reversal."""
        self._validate(command)
        liability = next(
            (
                item
                for item in self._liabilities.list_all()
                if item.id == command.liability_id
            ),
            None,
        )
        if liability is None:
            raise BrokerActualInterestError(
                f"liability does not exist: {command.liability_id}"
            )
        if liability.liability_type is not LiabilityType.MARGIN_FINANCING:
            raise BrokerActualInterestError(
                f"liability is not margin financing: {command.liability_id}"
            )

        created_at = datetime.combine(command.event_date, datetime.min.time(), UTC)
        replacement_id = financing_interest_event_id(
            command.liability_id, command.event_date
        )
        description = (
            "broker actual margin interest; "
            f"liability_id={command.liability_id}; market={command.market.value}; "
            f"symbol={command.symbol}; sell_order={command.sell_order}; "
            f"replaces={replacement_id}"
        )
        actual = InvestmentCostEvent(
            id=broker_actual_interest_event_id(command),
            event_date=command.event_date,
            cost_type=InvestmentCostType.FINANCING,
            amount=command.amount,
            currency=Currency.TWD,
            description=description,
            source=command.source,
            created_at=created_at,
        )
        existing = {
            item.id: item
            for item in self._costs.list_between_dates(
                command.event_date, command.event_date
            )
        }
        existing_actual = existing.get(actual.id)
        if existing_actual is not None:
            self._assert_same(existing_actual, actual)

        automatic = existing.get(replacement_id)
        reversal = None
        if automatic is not None:
            reversal = InvestmentCostEvent(
                id=broker_actual_interest_reversal_id(command),
                event_date=command.event_date,
                cost_type=InvestmentCostType.FINANCING,
                amount=automatic.amount,
                currency=automatic.currency,
                description=(
                    "reversal of superseded automatic financing accrual; "
                    f"replaced_event_id={replacement_id}; actual_event_id={actual.id}"
                ),
                source=command.source,
                created_at=created_at,
            )
            existing_reversal = existing.get(reversal.id)
            if existing_reversal is not None:
                self._assert_same(existing_reversal, reversal)

        requested = tuple(
            event
            for event in (actual, reversal)
            if event is not None and event.id not in existing
        )
        already_recorded = existing_actual is not None and not requested
        inserted = self._costs.insert_many_if_absent(list(requested)) if apply else 0
        if apply and inserted != len(requested):
            raise BrokerActualInterestError(
                "actual-interest insert conflicted with concurrent data; transaction rolled back"
            )
        replaced = automatic.amount if automatic is not None else Decimal("0")
        return BrokerActualInterestPlan(
            actual,
            replacement_id,
            replaced,
            reversal,
            requested,
            inserted,
            already_recorded,
            command.amount - replaced if not already_recorded else Decimal("0"),
            apply,
        )

    @staticmethod
    def _validate(command: RecordBrokerActualInterestCommand) -> None:
        if command.amount <= 0:
            raise BrokerActualInterestError("actual interest amount must be positive")
        if not command.source.strip():
            raise BrokerActualInterestError("actual interest source is required")
        for label, value in (
            ("symbol", command.symbol),
            ("sell order", command.sell_order),
        ):
            if not value or not _REFERENCE_PATTERN.fullmatch(value):
                raise BrokerActualInterestError(f"invalid {label}: {value!r}")

    @staticmethod
    def _assert_same(
        existing: InvestmentCostEvent, expected: InvestmentCostEvent
    ) -> None:
        comparable_existing = existing.model_dump(exclude={"created_at"})
        comparable_expected = expected.model_dump(exclude={"created_at"})
        if comparable_existing != comparable_expected:
            raise BrokerActualInterestError(
                f"event identity exists with different fields: {expected.id}"
            )
