"""Safe broker-import planning and atomic application."""

from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from domain import (
    BrokerImportRecord,
    FinancingType,
    Liability,
    LiabilityPrincipalEvent,
    LiabilityPrincipalEventType,
    LiabilityType,
    Transaction,
    TransactionType,
)
from pams.application.reconcile_broker import ReconcileBrokerUseCase
from pams.brokerage import (
    BrokerApplyItem,
    BrokerApplyPlan,
    BrokerApplyResult,
    BrokerApplyStatus,
    NormalizedBrokerRecord,
    ReconciliationItem,
    ReconciliationStatus,
)
from repositories import BrokerImportUnitOfWork
from services import TransactionEngine


class BrokerImportBlockedError(ValueError):
    """The reconciled source contains a row that is unsafe to apply."""


class ImportBrokerUseCase:
    """Build one canonical plan and optionally apply it in one transaction."""

    def __init__(
        self,
        reconciliation: ReconcileBrokerUseCase,
        unit_of_work: BrokerImportUnitOfWork,
        transaction_engine: TransactionEngine | None = None,
    ) -> None:
        self.reconciliation = reconciliation
        self.unit = unit_of_work
        self.engine = transaction_engine or TransactionEngine()

    def plan(
        self,
        source: Path,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> BrokerApplyPlan:
        reconciliation = self.reconciliation.execute(
            source, start_date=start_date, end_date=end_date
        )
        items: list[BrokerApplyItem] = []
        for item in reconciliation.items:
            record = item.normalized_record
            if item.status is ReconciliationStatus.MATCHED:
                status = BrokerApplyStatus.NO_WRITE
                reasons: tuple[str, ...] = ()
            elif item.status is ReconciliationStatus.NEW and not item.dependencies:
                status = BrokerApplyStatus.ELIGIBLE
                reasons = ()
            elif item.status is ReconciliationStatus.NEW:
                status = BrokerApplyStatus.DEPENDENCY_BLOCKED
                reasons = item.dependencies
            else:
                status = BrokerApplyStatus.RECONCILIATION_BLOCKED
                reasons = (f"reconciliation status is {item.status.value}",)
            proposed = item.proposed_transaction
            items.append(
                BrokerApplyItem(
                    source_row=record.source_row,
                    source_reference=record.source_reference,
                    reconciliation_status=item.status,
                    apply_status=status,
                    domain_action="insert" if proposed is not None else None,
                    target_entity_type="transaction" if proposed is not None else None,
                    proposed_entity_id=proposed.id if proposed is not None else None,
                    dependencies=item.dependencies,
                    blocking_reasons=reasons,
                    warnings=item.warnings,
                )
            )
        return BrokerApplyPlan(reconciliation, tuple(items))

    def execute(
        self,
        source: Path,
        *,
        apply: bool,
        expected_fingerprint: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> BrokerApplyResult:
        plan = self.plan(source, start_date=start_date, end_date=end_date)
        if (
            expected_fingerprint is not None
            and plan.reconciliation.source_fingerprint != expected_fingerprint
        ):
            raise BrokerImportBlockedError(
                "broker source changed after the displayed apply plan"
            )
        if not apply:
            return BrokerApplyResult(plan, False, 0, 0, 0)
        if plan.blocked:
            raise BrokerImportBlockedError(
                "broker import is blocked by unsafe reconciliation rows or dependencies"
            )
        eligible = [
            (reconciled, planned)
            for reconciled, planned in zip(
                plan.reconciliation.items, plan.items, strict=True
            )
            if planned.apply_status is BrokerApplyStatus.ELIGIBLE
        ]
        if not eligible:
            return BrokerApplyResult(plan, True, 0, 0, 0)

        principal_events: list[LiabilityPrincipalEvent] = []
        provenance: list[BrokerImportRecord] = []
        with self.unit.transaction():
            existing = self.unit.transactions.list_all()
            liabilities = self.unit.liabilities.list_all()
            principal_history = self.unit.liability_principal_events.list_all()
            transactions = []
            for reconciled, _ in eligible:
                transaction = reconciled.proposed_transaction
                assert transaction is not None
                record = reconciled.normalized_record
                if self.unit.transactions.exists(transaction.id):
                    raise BrokerImportBlockedError(
                        f"deterministic transaction ID already exists: {transaction.id}"
                    )
                transactions.append(transaction)
                if record.financing_type is FinancingType.MARGIN:
                    event = self._principal_event(
                        record,
                        liabilities,
                        principal_history + principal_events,
                    )
                    principal_events.append(event)
                    liability = next(
                        value
                        for value in liabilities
                        if value.liability_type is LiabilityType.MARGIN_FINANCING
                    )
                    new_quantity = (liability.financed_quantity or Decimal("0")) + (
                        record.quantity
                        if record.transaction_type is TransactionType.BUY
                        else -record.quantity
                    )
                    new_principal = liability.principal + event.principal_delta
                    if new_quantity < 0 or new_principal < 0:
                        raise BrokerImportBlockedError(
                            "margin import would create a negative financed position"
                        )
                    updated = liability.model_copy(
                        update={
                            "principal": new_principal,
                            "financed_symbol": record.symbol,
                            "financed_quantity": new_quantity,
                        }
                    )
                    liabilities = [
                        updated if value.id == updated.id else value
                        for value in liabilities
                    ]
                    self.unit.liabilities.upsert(updated)
                provenance.append(self._provenance(plan, reconciled))

            for transaction in transactions:
                self.unit.transactions.add(transaction)
            if principal_events:
                inserted = self.unit.liability_principal_events.insert_many_if_absent(
                    principal_events
                )
                if inserted != len(principal_events):
                    raise BrokerImportBlockedError(
                        "principal event provenance is not uniquely insertable"
                    )
            self._rebuild_holdings([*existing, *transactions])
            for record in provenance:
                self.unit.broker_import_records.add(record)

        return BrokerApplyResult(
            plan,
            True,
            len(transactions),
            len(principal_events),
            len(provenance),
        )

    def _rebuild_holdings(self, transactions: list[Transaction]) -> None:
        existing = self.unit.holdings.list_all()
        projected = self.engine.project_transaction_holdings(transactions, existing)
        projected_by_key = {
            (item.symbol, item.market, item.currency): item for item in projected
        }
        transaction_keys = {
            (item.symbol, item.market, item.currency) for item in transactions
        }
        for holding in projected:
            self.unit.holdings.upsert(holding)
        for holding in existing:
            key = (holding.symbol, holding.market, holding.currency)
            if key in transaction_keys and key not in projected_by_key:
                self.unit.holdings.upsert(
                    holding.model_copy(
                        update={
                            "quantity": Decimal("0"),
                            "average_cost": Decimal("0"),
                        }
                    )
                )

    @staticmethod
    def _principal_event(
        record: NormalizedBrokerRecord,
        liabilities: list[Liability],
        history: list[LiabilityPrincipalEvent],
    ) -> LiabilityPrincipalEvent:
        margin = [
            value
            for value in liabilities
            if value.liability_type is LiabilityType.MARGIN_FINANCING
        ]
        if len(margin) != 1:
            raise BrokerImportBlockedError(
                "exactly one margin-financing liability is required"
            )
        delta = record.financing_principal_delta
        assert delta is not None and record.trade_date is not None
        if record.transaction_type is TransactionType.BUY and delta <= 0:
            raise BrokerImportBlockedError(
                "margin BUY principal delta must be positive"
            )
        if record.transaction_type is TransactionType.SELL and delta >= 0:
            raise BrokerImportBlockedError(
                "margin SELL principal delta must be negative"
            )
        latest_date = max(
            (
                event.effective_date
                for event in history
                if event.liability_id == margin[0].id
            ),
            default=record.trade_date,
        )
        if record.trade_date < latest_date:
            raise BrokerImportBlockedError(
                "margin principal event predates existing principal history"
            )
        sequence = 1 + max(
            (
                event.sequence
                for event in history
                if event.liability_id == margin[0].id
                and event.effective_date == record.trade_date
            ),
            default=-1,
        )
        event_id = str(
            uuid5(
                NAMESPACE_URL,
                f"broker-principal|{record.broker}|{record.source_reference}",
            )
        )
        return LiabilityPrincipalEvent(
            id=event_id,
            liability_id=margin[0].id,
            effective_date=record.trade_date,
            sequence=sequence,
            event_type=(
                LiabilityPrincipalEventType.INCREASE
                if delta > 0
                else LiabilityPrincipalEventType.REPAYMENT
            ),
            principal_delta=delta,
            resulting_principal=margin[0].principal + delta,
            source=record.broker,
            reference=record.source_reference,
            notes="Imported from reconciled broker source",
        )

    @staticmethod
    def _provenance(
        plan: BrokerApplyPlan, item: ReconciliationItem
    ) -> BrokerImportRecord:
        record = item.normalized_record
        transaction = item.proposed_transaction
        assert transaction is not None
        normalized = "|".join(str(value) for value in record.economic_identity or ())
        normalized_hash = sha256(normalized.encode("utf-8")).hexdigest()
        provenance_id = str(
            uuid5(
                NAMESPACE_URL,
                f"broker-import|{record.broker}|{plan.reconciliation.source_fingerprint}|"
                f"{record.source_reference}|transaction",
            )
        )
        return BrokerImportRecord(
            id=provenance_id,
            broker=record.broker,
            source_fingerprint=plan.reconciliation.source_fingerprint,
            source_row_reference=record.source_reference,
            record_type=record.kind.value,
            domain_entity_type="transaction",
            domain_entity_id=transaction.id,
            normalized_identity=normalized_hash,
        )
