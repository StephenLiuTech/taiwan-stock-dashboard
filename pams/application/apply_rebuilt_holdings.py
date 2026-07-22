"""Explicit holding change-plan preview and persistence workflow."""

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal

from domain import Holding
from pams.application.dto import (
    HoldingChangeAction,
    HoldingChangeItem,
    HoldingChangePlan,
)
from pams.application.exceptions import (
    EmptyTransactionHistoryError,
    UnmatchedHoldingsError,
)
from repositories.interfaces import HoldingRebuildUnitOfWork
from services import HoldingProjectionMetadata, TransactionEngine
from services.transaction_engine import PositionKey


def _by_symbol(item: HoldingChangeItem) -> str:
    return item.symbol


class ApplyRebuiltHoldingsUseCase:
    """Preview by default and atomically apply only after explicit confirmation."""

    def __init__(
        self,
        unit_of_work: HoldingRebuildUnitOfWork,
        metadata: Mapping[PositionKey, HoldingProjectionMetadata],
        engine: TransactionEngine | None = None,
    ) -> None:
        self.unit_of_work = unit_of_work
        self.metadata = metadata
        self.engine = engine or TransactionEngine()

    def execute(
        self,
        *,
        apply: bool = False,
        allow_unmatched_holdings: bool = False,
    ) -> HoldingChangePlan:
        """Calculate a plan and optionally apply all changes atomically."""
        if not apply:
            plan, _ = self._build_plan()
            return plan
        with self.unit_of_work.transaction():
            plan, writes = self._build_plan()
            if plan.transaction_count == 0:
                raise EmptyTransactionHistoryError(
                    "Cannot apply holding rebuild: transaction history is empty"
                )
            if plan.warnings and not allow_unmatched_holdings:
                raise UnmatchedHoldingsError(
                    "Cannot apply holding rebuild with unresolved unmatched holdings"
                )
            for holding in writes:
                self.unit_of_work.holdings.upsert(holding)
            return replace(plan, applied=True)

    def _build_plan(self) -> tuple[HoldingChangePlan, tuple[Holding, ...]]:
        transactions = self.unit_of_work.transactions.list_all()
        persisted = self.unit_of_work.holdings.list_all()
        ledger = self.engine.build_ledger(transactions)
        projected = self.engine.project_holdings(ledger, self.metadata, persisted)
        persisted_by_key = {
            (item.symbol, item.market, item.currency): item for item in persisted
        }
        projected_by_key = {
            (item.symbol, item.market, item.currency): item for item in projected
        }
        ledger_by_key = {
            (item.symbol, item.market, item.currency): item for item in ledger.positions
        }
        transaction_keys = {
            (item.symbol, item.market, item.currency) for item in transactions
        }
        created: list[HoldingChangeItem] = []
        updated: list[HoldingChangeItem] = []
        unchanged: list[HoldingChangeItem] = []
        closed: list[HoldingChangeItem] = []
        writes: list[Holding] = []

        for key, new in projected_by_key.items():
            old = persisted_by_key.get(key)
            ledger_position = ledger_by_key[key]
            if old is None:
                action = HoldingChangeAction.CREATE
                target = created
                writes.append(new)
            elif old.quantity != new.quantity or old.average_cost != new.average_cost:
                action = HoldingChangeAction.UPDATE
                target = updated
                writes.append(new)
            else:
                action = HoldingChangeAction.UNCHANGED
                target = unchanged
            target.append(
                self._item(
                    new.symbol,
                    action,
                    old,
                    new.quantity,
                    new.average_cost,
                    ledger_position.cost_basis,
                )
            )

        for key, old in persisted_by_key.items():
            if key in projected_by_key:
                continue
            closed.append(
                self._item(
                    old.symbol,
                    HoldingChangeAction.CLOSE,
                    old,
                    Decimal("0"),
                    Decimal("0"),
                    Decimal("0"),
                )
            )
            if old.quantity != 0 or old.average_cost != 0:
                writes.append(
                    old.model_copy(
                        update={
                            "quantity": Decimal("0"),
                            "average_cost": Decimal("0"),
                        }
                    )
                )

        warnings = tuple(
            f"Active persisted holding {item.symbol} has no transaction history"
            for item in persisted
            if item.quantity > 0
            and (item.symbol, item.market, item.currency) not in transaction_keys
        )
        if not transactions:
            warnings += ("Transaction history is empty; apply is prohibited",)
        plan = HoldingChangePlan(
            created_holdings=tuple(sorted(created, key=_by_symbol)),
            updated_holdings=tuple(sorted(updated, key=_by_symbol)),
            unchanged_holdings=tuple(sorted(unchanged, key=_by_symbol)),
            closed_holdings=tuple(sorted(closed, key=_by_symbol)),
            warnings=warnings,
            transaction_count=len(transactions),
            projected_total_cost_basis=sum(
                (item.cost_basis for item in ledger.positions), Decimal("0")
            ),
        )
        return plan, tuple(writes)

    @staticmethod
    def _item(
        symbol: str,
        action: HoldingChangeAction,
        old: Holding | None,
        new_quantity: Decimal,
        new_average_cost: Decimal,
        new_cost_basis: Decimal,
    ) -> HoldingChangeItem:
        return HoldingChangeItem(
            symbol=symbol,
            action=action,
            old_quantity=old.quantity if old else None,
            new_quantity=new_quantity,
            old_average_cost=old.average_cost if old else None,
            new_average_cost=new_average_cost,
            old_cost_basis=(old.quantity * old.average_cost if old else None),
            new_cost_basis=new_cost_basis,
        )
