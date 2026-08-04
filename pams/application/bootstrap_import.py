"""Read-only reconciliation for the one-time broker statement bootstrap import."""

import csv
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from domain import (
    Currency,
    Holding,
    Market,
    Transaction,
    TransactionPosition,
    TransactionType,
)
from repositories.interfaces import (
    BootstrapImportUnitOfWork,
    HoldingRepository,
    TransactionRepository,
)
from services import TransactionEngine

BROKER_AVERAGE_QUANTUM = Decimal("0.01")
BOOTSTRAP_DATE = date(2026, 1, 1)

SECURITIES: dict[str, tuple[str, Market]] = {
    "元大台灣50": ("0050", Market.TWSE),
    "主動統一台股增長": ("00981A", Market.TWSE),
    "大成鋼": ("2027", Market.TWSE),
    "台積電": ("2330", Market.TWSE),
    "華邦電": ("2344", Market.TWSE),
    "南亞科": ("2408", Market.TWSE),
    "鈊象": ("3293", Market.TPEX),
    "力積電": ("6770", Market.TWSE),
    "福懋科": ("8131", Market.TWSE),
    "群聯": ("8299", Market.TPEX),
}


class BootstrapImportError(ValueError):
    """Raised when a broker statement cannot reproduce current holdings."""


@dataclass(frozen=True)
class BootstrapTransactionPreview:
    symbol: str
    market: str
    trade_date: date
    transaction_type: str
    quantity: Decimal
    price: Decimal
    fees: Decimal
    taxes: Decimal
    synthetic: bool


@dataclass(frozen=True)
class BootstrapHoldingReconciliation:
    symbol: str
    expected_quantity: Decimal
    actual_quantity: Decimal
    expected_average_cost: Decimal
    actual_average_cost: Decimal
    expected_total_cost: Decimal
    accounting_cost_basis: Decimal
    passed: bool


@dataclass(frozen=True)
class BootstrapImportPreview:
    source: Path
    imported_transactions: tuple[BootstrapTransactionPreview, ...]
    bootstrap_transactions: tuple[BootstrapTransactionPreview, ...]
    excluded_symbols: tuple[str, ...]
    placeholder_transaction_ids: tuple[str, ...]
    replaced_transaction_ids: tuple[str, ...]
    reconciliations: tuple[BootstrapHoldingReconciliation, ...]
    total_buy_fees: Decimal
    total_sell_fees: Decimal
    total_taxes: Decimal
    total_trading_expenses: Decimal
    passed: bool
    applied: bool = False
    duplicate: bool = False
    database: str | None = None


class BootstrapImportUseCase:
    """Build a candidate ledger from a broker CSV without persistence."""

    def __init__(
        self,
        holdings: HoldingRepository,
        transactions: TransactionRepository,
        engine: TransactionEngine | None = None,
        unit_of_work: BootstrapImportUnitOfWork | None = None,
        database: str | None = None,
    ) -> None:
        self.holdings = holdings
        self.transactions = transactions
        self.engine = engine or TransactionEngine()
        self.unit_of_work = unit_of_work
        self.database = database

    def preview(self, source: Path, target_source: Path) -> BootstrapImportPreview:
        persisted_holdings = self.holdings.list_all()
        current_transactions = self.transactions.list_all()
        targets, target_costs = self._read_targets(target_source, persisted_holdings)
        if not targets:
            raise BootstrapImportError(
                "Current holdings are required for reconciliation"
            )
        actual, excluded = self._read_statement(source, targets)
        bootstraps = self._build_bootstraps(targets, target_costs, actual)
        candidate = [*bootstraps, *actual]
        ledger = self.engine.build_ledger(candidate)
        positions = {(item.symbol, item.market): item for item in ledger.positions}
        reconciliations = tuple(
            self._reconcile(target, target_costs[key], positions.get(key))
            for key, target in sorted(targets.items(), key=lambda item: item[0][0])
        )
        candidate_keys = {(item.symbol, item.market) for item in ledger.positions}
        passed = all(item.passed for item in reconciliations) and candidate_keys == set(
            targets
        )
        placeholders = tuple(
            item.id
            for item in current_transactions
            if item.trade_date == date(2026, 7, 24)
            and (item.symbol, item.market) in targets
            and not item.id.startswith(("broker-", "bootstrap-"))
        )
        managed_symbols = {item.symbol for item in actual} | excluded
        replaced = tuple(
            item.id
            for item in current_transactions
            if item.trade_date.year == 2026 and item.symbol in managed_symbols
        )
        return BootstrapImportPreview(
            source=source,
            imported_transactions=tuple(self._preview(item, False) for item in actual),
            bootstrap_transactions=tuple(
                self._preview(item, True) for item in bootstraps
            ),
            excluded_symbols=tuple(sorted(excluded)),
            placeholder_transaction_ids=placeholders,
            replaced_transaction_ids=replaced,
            reconciliations=reconciliations,
            total_buy_fees=ledger.total_buy_fees,
            total_sell_fees=ledger.total_sell_fees,
            total_taxes=ledger.total_taxes,
            total_trading_expenses=ledger.total_trading_expenses,
            passed=passed,
            database=self.database,
        )

    def apply(self, source: Path, target_source: Path) -> BootstrapImportPreview:
        """Atomically replace the 2026 ledger and rebuild broker-cost holdings."""
        preview = self.preview(source, target_source)
        if not preview.passed:
            raise BootstrapImportError("Pre-write bootstrap reconciliation failed")
        if self.unit_of_work is None:
            raise BootstrapImportError("Bootstrap import apply is not composed")
        persisted = self.holdings.list_all()
        targets, target_costs = self._read_targets(target_source, persisted)
        actual, excluded = self._read_statement(source, targets)
        bootstraps = self._build_bootstraps(targets, target_costs, actual)
        candidate = [*bootstraps, *actual]
        candidate_ids = {item.id for item in candidate}
        managed_symbols = {item.symbol for item in actual} | excluded
        current_2026 = {
            item.id
            for item in self.transactions.list_all()
            if item.trade_date.year == 2026 and item.symbol in managed_symbols
        }
        current_managed = [
            item
            for item in self.transactions.list_all()
            if item.trade_date.year == 2026 and item.symbol in managed_symbols
        ]
        equivalent_transactions = Counter(
            self._transaction_fingerprint(item) for item in current_managed
        ) == Counter(self._transaction_fingerprint(item) for item in candidate)
        persisted_by_key = {
            (item.symbol, item.market): item for item in persisted if item.quantity > 0
        }
        holdings_match = set(persisted_by_key) == set(targets) and all(
            persisted_by_key[key].quantity == target.quantity
            and persisted_by_key[key].average_cost.quantize(
                BROKER_AVERAGE_QUANTUM, rounding=ROUND_HALF_UP
            )
            == target.average_cost.quantize(
                BROKER_AVERAGE_QUANTUM, rounding=ROUND_HALF_UP
            )
            and persisted_by_key[key].quantity * persisted_by_key[key].average_cost
            == target_costs[key]
            for key, target in targets.items()
        )
        if (
            current_2026 == candidate_ids or equivalent_transactions
        ) and holdings_match:
            return replace(preview, duplicate=True)

        with self.unit_of_work.transaction():
            for transaction in self.unit_of_work.transactions.list_all():
                if (
                    transaction.trade_date.year == 2026
                    and transaction.symbol in managed_symbols
                ):
                    self.unit_of_work.transactions.delete(transaction.id)
            for transaction in candidate:
                self.unit_of_work.transactions.upsert(transaction)

            all_transactions = self.unit_of_work.transactions.list_all()
            ledger = self.engine.build_ledger(all_transactions)
            projected = self.engine.project_transaction_holdings(
                all_transactions, self.unit_of_work.holdings.list_all()
            )
            projected_keys = {
                (item.symbol, item.market, item.currency) for item in projected
            }
            for holding in projected:
                self.unit_of_work.holdings.upsert(holding)
            for holding in self.unit_of_work.holdings.list_all():
                key = (holding.symbol, holding.market, holding.currency)
                if key not in projected_keys and holding.quantity != 0:
                    self.unit_of_work.holdings.upsert(
                        holding.model_copy(
                            update={
                                "quantity": Decimal("0"),
                                "average_cost": Decimal("0"),
                            }
                        )
                    )

            positions = {(item.symbol, item.market): item for item in ledger.positions}
            post = tuple(
                self._reconcile(target, target_costs[key], positions.get(key))
                for key, target in sorted(targets.items(), key=lambda item: item[0][0])
            )
            persisted_after = {
                (item.symbol, item.market): item
                for item in self.unit_of_work.holdings.list_all()
                if item.quantity > 0
            }
            persisted_matches = set(persisted_after) == set(targets) and all(
                persisted_after[key].quantity == target.quantity
                and persisted_after[key].average_cost.quantize(
                    BROKER_AVERAGE_QUANTUM, rounding=ROUND_HALF_UP
                )
                == target.average_cost.quantize(
                    BROKER_AVERAGE_QUANTUM, rounding=ROUND_HALF_UP
                )
                and persisted_after[key].quantity * persisted_after[key].average_cost
                == target_costs[key]
                for key, target in targets.items()
            )
            if (
                not all(item.passed for item in post)
                or set(positions) != set(targets)
                or not persisted_matches
            ):
                raise BootstrapImportError(
                    "Post-write bootstrap reconciliation failed; transaction rolled back"
                )
        return replace(preview, reconciliations=post, applied=True)

    @staticmethod
    def _read_targets(
        source: Path, persisted: list[Holding]
    ) -> tuple[dict[tuple[str, Market], Holding], dict[tuple[str, Market], Decimal]]:
        metadata = {(item.symbol, item.market): item for item in persisted}
        targets: dict[tuple[str, Market], Holding] = {}
        costs: dict[tuple[str, Market], Decimal] = {}
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                key = (row["symbol"].strip().upper(), Market(row["market"]))
                holding = metadata.get(key)
                if holding is None:
                    raise BootstrapImportError(
                        f"Target holding metadata is missing for {key[0]}"
                    )
                targets[key] = holding.model_copy(
                    update={
                        "quantity": _decimal(row["quantity"]),
                        "average_cost": _decimal(row["average_cost"]),
                    }
                )
                costs[key] = _decimal(row["total_cost"])
        if not targets:
            raise BootstrapImportError("Broker target snapshot is empty")
        return targets, costs

    @staticmethod
    def _read_statement(
        source: Path, targets: dict[tuple[str, Market], Holding]
    ) -> tuple[list[Transaction], set[str]]:
        if not source.is_file():
            raise BootstrapImportError(f"Broker statement does not exist: {source}")
        imported: list[Transaction] = []
        excluded: set[str] = set()
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = csv.DictReader(stream)
            required = {
                "股名",
                "日期",
                "成交股數",
                "淨收付",
                "成交單價",
                "手續費",
                "交易稅",
                "稅款",
                "委託書號",
            }
            if not rows.fieldnames or not required.issubset(rows.fieldnames):
                raise BootstrapImportError("Broker statement columns are incomplete")
            for row_number, row in enumerate(rows, start=2):
                identity = SECURITIES.get(row["股名"].strip())
                if identity is None:
                    raise BootstrapImportError(
                        f"Unknown security name at CSV row {row_number}"
                    )
                symbol, market = identity
                if identity not in targets:
                    excluded.add(symbol)
                    continue
                net_cash = _decimal(row["淨收付"])
                transaction_type = (
                    TransactionType.BUY if net_cash < 0 else TransactionType.SELL
                )
                trade_date = date.fromisoformat(row["日期"].replace("/", "-"))
                quantity = _decimal(row["成交股數"])
                price = _decimal(row["成交單價"])
                fees = _decimal(row["手續費"])
                taxes = _decimal(row["交易稅"]) + _decimal(row["稅款"])
                if trade_date.year != 2026 or quantity <= 0 or price <= 0:
                    raise BootstrapImportError(
                        f"Invalid transaction at CSV row {row_number}"
                    )
                fingerprint = "|".join(
                    (
                        symbol,
                        market.value,
                        trade_date.isoformat(),
                        transaction_type.value,
                        str(quantity),
                        str(price),
                        str(fees),
                        str(taxes),
                        row["委託書號"].strip(),
                        str(row_number),
                    )
                )
                imported.append(
                    Transaction(
                        id=f"broker-{uuid5(NAMESPACE_URL, fingerprint)}",
                        symbol=symbol,
                        market=market,
                        transaction_type=transaction_type,
                        trade_date=trade_date,
                        settlement_date=trade_date,
                        quantity=quantity,
                        price=price,
                        fees=fees,
                        taxes=taxes,
                        currency=Currency.TWD,
                        notes=f"Broker statement order {row['委託書號'].strip()}",
                    )
                )
        return imported, excluded

    @staticmethod
    def _build_bootstraps(
        targets: dict[tuple[str, Market], Holding],
        target_costs: dict[tuple[str, Market], Decimal],
        actual: list[Transaction],
    ) -> list[Transaction]:
        bootstraps = []
        for (symbol, market), holding in sorted(
            targets.items(), key=lambda item: item[0][0]
        ):
            rows = [
                item
                for item in actual
                if (item.symbol, item.market) == (symbol, market)
            ]
            net_quantity = sum(
                (
                    (
                        item.quantity
                        if item.transaction_type is TransactionType.BUY
                        else -item.quantity
                    )
                    for item in rows
                ),
                Decimal("0"),
            )
            bootstrap_quantity = holding.quantity - net_quantity
            if bootstrap_quantity < 0:
                raise BootstrapImportError(
                    f"CSV quantity exceeds target holding for {symbol}"
                )
            if bootstrap_quantity == 0:
                continue
            if any(item.transaction_type is TransactionType.SELL for item in rows):
                raise BootstrapImportError(
                    f"Cannot derive opening cost across sales for {symbol}"
                )
            actual_cost = sum(
                (item.quantity * item.price for item in rows), Decimal("0")
            )
            bootstrap_price = (target_costs[(symbol, market)] - actual_cost) / (
                bootstrap_quantity
            )
            if bootstrap_price < 0:
                raise BootstrapImportError(
                    f"Target total cost cannot reconcile transactions for {symbol}"
                )
            bootstraps.append(
                Transaction(
                    id=f"bootstrap-{market.value.lower()}-{symbol}-20260101",
                    symbol=symbol,
                    market=market,
                    transaction_type=TransactionType.BUY,
                    trade_date=BOOTSTRAP_DATE,
                    settlement_date=BOOTSTRAP_DATE,
                    quantity=bootstrap_quantity,
                    price=bootstrap_price,
                    fees=Decimal("0"),
                    taxes=Decimal("0"),
                    currency=holding.currency,
                    notes="Already owned before PAMS began tracking.",
                )
            )
        return bootstraps

    @staticmethod
    def _reconcile(
        target: Holding,
        expected_total_cost: Decimal,
        position: TransactionPosition | None,
    ) -> BootstrapHoldingReconciliation:
        expected_average = target.average_cost.quantize(
            BROKER_AVERAGE_QUANTUM, rounding=ROUND_HALF_UP
        )
        if position is None:
            return BootstrapHoldingReconciliation(
                target.symbol,
                target.quantity,
                Decimal("0"),
                expected_average,
                Decimal("0"),
                expected_total_cost,
                Decimal("0"),
                False,
            )
        actual_average = position.average_cost.quantize(
            BROKER_AVERAGE_QUANTUM, rounding=ROUND_HALF_UP
        )
        return BootstrapHoldingReconciliation(
            target.symbol,
            target.quantity,
            position.quantity,
            expected_average,
            actual_average,
            expected_total_cost,
            position.cost_basis,
            position.quantity == target.quantity
            and actual_average == expected_average
            and position.cost_basis == expected_total_cost,
        )

    @staticmethod
    def _preview(
        transaction: Transaction, synthetic: bool
    ) -> BootstrapTransactionPreview:
        return BootstrapTransactionPreview(
            transaction.symbol,
            transaction.market.value,
            transaction.trade_date,
            transaction.transaction_type.value,
            transaction.quantity,
            transaction.price,
            transaction.fees,
            transaction.taxes,
            synthetic,
        )

    @staticmethod
    def _transaction_fingerprint(transaction: Transaction) -> tuple[object, ...]:
        return (
            transaction.symbol,
            transaction.market,
            transaction.transaction_type,
            transaction.trade_date,
            transaction.settlement_date,
            transaction.quantity,
            transaction.price,
            transaction.fees,
            transaction.taxes,
            transaction.currency,
        )


def _decimal(value: str) -> Decimal:
    return Decimal(value.strip().replace(",", ""))
