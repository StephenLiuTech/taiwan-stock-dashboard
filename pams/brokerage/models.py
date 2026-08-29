"""Immutable broker-neutral import and reconciliation records."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from domain import Currency, FinancingType, Market, Transaction, TransactionType


class BrokerRecordKind(StrEnum):
    TRADE = "trade"
    DIVIDEND = "dividend"
    CORPORATE_ACTION = "corporate_action"
    FINANCING_SETTLEMENT = "financing_settlement"
    UNSUPPORTED = "unsupported"


class ReconciliationStatus(StrEnum):
    MATCHED = "MATCHED"
    NEW = "NEW"
    MISMATCH = "MISMATCH"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class NormalizedBrokerRecord:
    broker: str
    source_reference: str
    source_row: int
    kind: BrokerRecordKind
    trade_date: date | None
    settlement_date: date | None
    symbol: str | None
    market: Market | None
    transaction_type: TransactionType | None
    financing_type: FinancingType | None
    quantity: Decimal | None
    price: Decimal | None
    gross_amount: Decimal | None
    fee: Decimal
    tax: Decimal
    net_amount: Decimal | None
    currency: Currency | None
    warning: str | None = None

    @property
    def economic_identity(self) -> tuple[object, ...] | None:
        if self.kind is not BrokerRecordKind.TRADE:
            return None
        return (
            self.symbol,
            self.market,
            self.trade_date,
            self.transaction_type,
            self.quantity,
            self.price,
        )


@dataclass(frozen=True)
class ReconciliationItem:
    normalized_record: NormalizedBrokerRecord
    status: ReconciliationStatus
    matched_transaction_id: str | None = None
    candidate_transaction_ids: tuple[str, ...] = ()
    field_differences: tuple[tuple[str, str, str], ...] = ()
    proposed_transaction: Transaction | None = None
    dependencies: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReconciliationPlan:
    source_fingerprint: str
    source_rows: int
    parsed_rows: int
    items: tuple[ReconciliationItem, ...]
    duplicate_source_rows: tuple[tuple[int, int], ...] = ()

    def count(self, status: ReconciliationStatus) -> int:
        return sum(item.status is status for item in self.items)

    @property
    def apply_blocked(self) -> bool:
        return any(
            item.status
            in {
                ReconciliationStatus.MISMATCH,
                ReconciliationStatus.AMBIGUOUS,
                ReconciliationStatus.UNSUPPORTED,
            }
            for item in self.items
        )
