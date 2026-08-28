"""Application workflow for auditable non-cash quantity conversions."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from domain import CorporateAction, Market
from repositories.interfaces import CorporateActionRepository, TransactionRepository
from services import TransactionEngine, TransactionEngineError


class CorporateActionError(ValueError):
    """Raised when a corporate action cannot be recorded safely."""


@dataclass(frozen=True)
class AddCorporateActionCommand:
    symbol: str
    market: Market
    effective_date: date
    quantity_multiplier: Decimal
    source: str
    reference: str | None = None
    notes: str | None = None


class AddCorporateActionUseCase:
    """Validate a corporate action through replay before persisting it."""

    def __init__(
        self,
        actions: CorporateActionRepository,
        transactions: TransactionRepository,
        engine: TransactionEngine | None = None,
    ) -> None:
        self.actions = actions
        self.transactions = transactions
        self.engine = engine or TransactionEngine()

    def execute(self, command: AddCorporateActionCommand) -> CorporateAction:
        symbol = command.symbol.strip().upper()
        fingerprint = "|".join(
            (
                symbol,
                command.market.value,
                command.effective_date.isoformat(),
                str(command.quantity_multiplier),
                command.source.strip(),
                (command.reference or "").strip(),
            )
        )
        action = CorporateAction(
            id=f"corporate-action-{uuid5(NAMESPACE_URL, fingerprint)}",
            symbol=symbol,
            market=command.market,
            effective_date=command.effective_date,
            quantity_multiplier=command.quantity_multiplier,
            source=command.source.strip(),
            reference=(command.reference or None),
            notes=command.notes,
        )
        existing = self.actions.get_by_id(action.id)
        if existing is not None:
            return existing
        transactions = self.transactions.list_filtered(end_date=command.effective_date)
        actions = self.actions.list_filtered(end_date=command.effective_date)
        try:
            self.engine.build_ledger(transactions, [*actions, action])
            self.actions.add(action)
        except (TransactionEngineError, ValueError) as error:
            raise CorporateActionError(str(error)) from error
        return action
