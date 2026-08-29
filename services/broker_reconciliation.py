"""Pure deterministic broker-to-ledger transaction reconciliation."""

from collections import defaultdict
from uuid import NAMESPACE_URL, uuid5

from domain import Transaction, TransactionType
from pams.brokerage.models import (
    BrokerRecordKind,
    NormalizedBrokerRecord,
    ReconciliationItem,
    ReconciliationPlan,
    ReconciliationStatus,
)


class BrokerReconciliationEngine:
    """Classify every normalized source row without persistence or fuzzy matching."""

    def reconcile(
        self,
        fingerprint: str,
        source_rows: int,
        records: tuple[NormalizedBrokerRecord, ...],
        transactions: list[Transaction],
    ) -> ReconciliationPlan:
        by_core: dict[tuple[object, ...], list[Transaction]] = defaultdict(list)
        by_loose: dict[tuple[object, ...], list[Transaction]] = defaultdict(list)
        for transaction in transactions:
            by_core[self._identity(transaction)].append(transaction)
            by_loose[self._loose_identity(transaction)].append(transaction)
        first_rows: dict[tuple[object, ...], int] = {}
        duplicates: list[tuple[int, int]] = []
        items: list[ReconciliationItem] = []
        for record in records:
            if record.kind is not BrokerRecordKind.TRADE:
                items.append(
                    ReconciliationItem(
                        record,
                        ReconciliationStatus.UNSUPPORTED,
                        warnings=((record.warning or "Unsupported broker row"),),
                    )
                )
                continue
            identity = record.economic_identity
            assert identity is not None
            duplicate_key = (*identity, record.fee, record.tax, record.source_reference)
            if duplicate_key in first_rows:
                duplicates.append((first_rows[duplicate_key], record.source_row))
                items.append(
                    ReconciliationItem(
                        record,
                        ReconciliationStatus.AMBIGUOUS,
                        warnings=(
                            f"Duplicate source row of CSV row {first_rows[duplicate_key]}",
                        ),
                    )
                )
                continue
            first_rows[duplicate_key] = record.source_row
            candidates = by_core.get(identity, [])
            referenced = [
                candidate
                for candidate in candidates
                if candidate.notes
                and record.source_reference.split(":", 1)[0] in candidate.notes
            ]
            if len(referenced) == 1:
                candidates = referenced
            elif referenced:
                candidates = referenced
            if len(candidates) > 1:
                accounting_matches = [
                    candidate
                    for candidate in candidates
                    if candidate.fees == record.fee and candidate.taxes == record.tax
                ]
                if len(accounting_matches) == 1:
                    candidates = accounting_matches
            if len(candidates) == 1:
                candidate = candidates[0]
                differences = self._differences(record, candidate)
                items.append(
                    ReconciliationItem(
                        record,
                        (
                            ReconciliationStatus.MATCHED
                            if not differences
                            else ReconciliationStatus.MISMATCH
                        ),
                        matched_transaction_id=candidate.id,
                        field_differences=differences,
                    )
                )
            elif len(candidates) > 1:
                items.append(
                    ReconciliationItem(
                        record,
                        ReconciliationStatus.AMBIGUOUS,
                        candidate_transaction_ids=tuple(
                            sorted(item.id for item in candidates)
                        ),
                    )
                )
            else:
                loose = by_loose.get(self._record_loose_identity(record), [])
                if loose:
                    items.append(
                        ReconciliationItem(
                            record,
                            (
                                ReconciliationStatus.MISMATCH
                                if len(loose) == 1
                                else ReconciliationStatus.AMBIGUOUS
                            ),
                            matched_transaction_id=(
                                loose[0].id if len(loose) == 1 else None
                            ),
                            candidate_transaction_ids=tuple(
                                sorted(item.id for item in loose)
                            ),
                            field_differences=(
                                self._differences(record, loose[0])
                                if len(loose) == 1
                                else ()
                            ),
                        )
                    )
                else:
                    items.append(
                        ReconciliationItem(
                            record,
                            ReconciliationStatus.NEW,
                            proposed_transaction=self._proposed(record, fingerprint),
                            dependencies=self._dependencies(record),
                        )
                    )
        return ReconciliationPlan(
            fingerprint, source_rows, len(records), tuple(items), tuple(duplicates)
        )

    @staticmethod
    def _identity(transaction: Transaction) -> tuple[object, ...]:
        return (
            transaction.symbol,
            transaction.market,
            transaction.trade_date,
            transaction.transaction_type,
            transaction.quantity,
            transaction.price,
        )

    @staticmethod
    def _loose_identity(transaction: Transaction) -> tuple[object, ...]:
        return (
            transaction.symbol,
            transaction.market,
            transaction.trade_date,
            transaction.transaction_type,
        )

    @staticmethod
    def _record_loose_identity(record: NormalizedBrokerRecord) -> tuple[object, ...]:
        return (
            record.symbol,
            record.market,
            record.trade_date,
            record.transaction_type,
        )

    @staticmethod
    def _differences(
        record: NormalizedBrokerRecord, transaction: Transaction
    ) -> tuple[tuple[str, str, str], ...]:
        pairs = {
            "quantity": (record.quantity, transaction.quantity),
            "price": (record.price, transaction.price),
            "fee": (record.fee, transaction.fees),
            "tax": (record.tax, transaction.taxes),
        }
        if (
            record.financing_type is not None
            and record.transaction_type is TransactionType.BUY
        ):
            pairs["financing_type"] = (
                record.financing_type,
                transaction.financing_type,
            )
        return tuple(
            (name, str(broker), str(existing))
            for name, (broker, existing) in pairs.items()
            if broker != existing
        )

    @staticmethod
    def _proposed(record: NormalizedBrokerRecord, fingerprint: str) -> Transaction:
        assert record.symbol and record.market and record.trade_date
        assert record.transaction_type and record.quantity is not None
        assert record.price is not None and record.currency
        identity = f"{fingerprint}|{record.source_row}|{record.source_reference}"
        return Transaction(
            id=f"broker-{uuid5(NAMESPACE_URL, identity)}",
            symbol=record.symbol,
            market=record.market,
            transaction_type=record.transaction_type,
            trade_date=record.trade_date,
            settlement_date=record.settlement_date or record.trade_date,
            quantity=record.quantity,
            price=record.price,
            fees=record.fee,
            taxes=record.tax,
            currency=record.currency,
            financing_type=(
                record.financing_type
                if record.transaction_type is TransactionType.BUY
                else None
            ),
        )

    @staticmethod
    def _dependencies(record: NormalizedBrokerRecord) -> tuple[str, ...]:
        if not record.financing_classification_known:
            return ("BLOCKED: explicit cash/margin classification is required",)
        if record.transaction_type is TransactionType.SELL:
            if record.financing_type is not None and (
                record.financing_principal_delta is None
                or record.financing_principal_delta >= 0
            ):
                return (
                    "BLOCKED: negative financing principal repayment evidence is required",
                )
            return ()
        if record.financing_type is not None and (
            record.financing_principal_delta is None
            or record.financing_principal_delta <= 0
        ):
            return ("BLOCKED: positive financing principal evidence is required",)
        return ()
