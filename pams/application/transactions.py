"""Transaction entry and query application workflows."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from domain import (
    Currency,
    FinancingType,
    LiabilityPrincipalEvent,
    LiabilityPrincipalEventType,
    LiabilityType,
    Market,
    Transaction,
    TransactionType,
)
from pams.application.dto import (
    AddTransactionCommand,
    TransactionList,
    TransactionRecord,
)
from pams.application.exceptions import DuplicateTransactionError
from repositories.interfaces import (
    MarginTransactionUnitOfWork,
    TransactionHoldingUnitOfWork,
    TransactionRepository,
)
from services import (
    LiabilityPrincipalEngine,
    MarginFinancingResult,
    MarginFinancingService,
    TransactionEngine,
)


def _record(
    transaction: Transaction,
    financing: MarginFinancingResult | None = None,
    updated_holding_quantity: Decimal | None = None,
    *,
    principal_event_id: str | None = None,
    applied: bool = True,
) -> TransactionRecord:
    return TransactionRecord(
        id=transaction.id,
        symbol=transaction.symbol,
        market=transaction.market.value,
        transaction_type=transaction.transaction_type.value,
        trade_date=transaction.trade_date,
        settlement_date=transaction.settlement_date,
        quantity=transaction.quantity,
        price=transaction.price,
        fees=transaction.fees,
        taxes=transaction.taxes,
        currency=transaction.currency.value,
        notes=transaction.notes,
        broker_order=transaction.broker_order,
        financing_type=(
            transaction.financing_type.value
            if transaction.financing_type is not None
            else None
        ),
        gross_purchase_value=(financing.gross_purchase_value if financing else None),
        self_funded_amount=(financing.self_funded_amount if financing else None),
        financed_principal=(financing.financed_principal if financing else None),
        updated_holding_quantity=updated_holding_quantity,
        updated_margin_quantity=(
            financing.updated_margin_quantity if financing else None
        ),
        updated_margin_principal=(
            financing.updated_liability.principal if financing else None
        ),
        margin_position_id=(financing.updated_position.id if financing else None),
        principal_event_id=principal_event_id,
        applied=applied,
    )


class AddTransactionUseCase:
    """Validate and persist one new transaction through a repository protocol."""

    def __init__(
        self,
        transactions: TransactionRepository,
        margin_unit_of_work: MarginTransactionUnitOfWork | None = None,
        margin_self_funding_ratio: Decimal = Decimal("0.40"),
        transaction_engine: TransactionEngine | None = None,
        holding_unit_of_work: TransactionHoldingUnitOfWork | None = None,
    ) -> None:
        self.transactions = transactions
        self.margin_unit_of_work = margin_unit_of_work
        self.margin_service = MarginFinancingService(margin_self_funding_ratio)
        self.transaction_engine = transaction_engine or TransactionEngine()
        self.holding_unit_of_work = holding_unit_of_work

    def execute(
        self, command: AddTransactionCommand, *, apply: bool = True
    ) -> TransactionRecord:
        market = Market(command.market)
        currency = Currency(command.currency)
        expected_currency = Currency.USD if market is Market.US else Currency.TWD
        if currency is not expected_currency:
            raise ValueError(
                f"{market.value} transactions require {expected_currency.value} currency"
            )
        transaction_values = {
            "symbol": command.symbol,
            "market": market,
            "transaction_type": TransactionType(command.transaction_type),
            "trade_date": command.trade_date,
            "settlement_date": command.settlement_date or command.trade_date,
            "quantity": command.quantity,
            "price": command.price,
            "fees": command.fees,
            "taxes": command.taxes,
            "currency": currency,
            "financing_type": (
                FinancingType(command.financing_type)
                if command.financing_type is not None
                else None
            ),
            "broker_order": command.broker_order,
            "notes": command.notes,
        }
        if command.transaction_id is not None:
            transaction_values["id"] = command.transaction_id
        elif command.broker_order is not None:
            transaction_values["id"] = str(
                uuid5(
                    NAMESPACE_URL,
                    "broker-order|"
                    f"{market.value}|{command.symbol.strip().upper()}|"
                    f"{command.trade_date.isoformat()}|"
                    f"{command.broker_order.strip().upper()}",
                )
            )
        transaction = Transaction.model_validate(transaction_values)
        if (
            command.margin_principal is not None
            and transaction.financing_type is not FinancingType.MARGIN
        ):
            raise ValueError(
                "broker-confirmed margin principal requires margin financing"
            )
        if not apply:
            return self._preview(transaction, command.margin_principal)
        if transaction.financing_type is FinancingType.MARGIN:
            return self._execute_margin(transaction, command.margin_principal)
        if self.holding_unit_of_work is not None:
            return self._execute_with_holding(transaction)
        if self.transactions.exists(transaction.id):
            raise DuplicateTransactionError(
                f"Transaction ID already exists: {transaction.id}"
            )
        self.transactions.add(transaction)
        return _record(transaction)

    def _preview(
        self, transaction: Transaction, margin_principal: Decimal | None = None
    ) -> TransactionRecord:
        """Validate and calculate one transaction without persistence."""
        if transaction.financing_type is not FinancingType.MARGIN:
            if self.transactions.exists(transaction.id) or self._broker_order_exists(
                self.transactions.list_all(), transaction
            ):
                raise DuplicateTransactionError(
                    f"Transaction ID already exists: {transaction.id}"
                )
            return _record(transaction, applied=False)
        unit = self.margin_unit_of_work
        if unit is None:
            raise ValueError("margin transaction persistence is not configured")
        existing = unit.transactions.list_all()
        if (
            unit.transactions.exists(transaction.id)
            or self._broker_order_exists(existing, transaction)
            or any(self._same_margin_purchase(item, transaction) for item in existing)
        ):
            raise DuplicateTransactionError(
                "Margin transaction already exists for the same trade details"
            )
        liabilities = [
            item
            for item in unit.liabilities.list_all()
            if item.liability_type is LiabilityType.MARGIN_FINANCING
        ]
        if len(liabilities) != 1:
            raise ValueError(
                "exactly one aggregate margin-financing liability is required"
            )
        liability = liabilities[0]
        position = unit.margin_positions.get(
            liability.id, transaction.market.value, transaction.symbol
        )
        financing = self.margin_service.apply(
            transaction, liability, position, margin_principal
        )
        projected = self.transaction_engine.project_current_holdings(
            [*existing, transaction], unit.holdings.list_all()
        )
        holding = next(
            item
            for item in projected
            if item.symbol == transaction.symbol
            and item.market is transaction.market
            and item.currency is transaction.currency
        )
        event_id = self._principal_event_id(liability.id, transaction.id)
        return _record(
            transaction,
            financing,
            holding.quantity,
            principal_event_id=event_id,
            applied=False,
        )

    def _execute_with_holding(self, transaction: Transaction) -> TransactionRecord:
        unit = self.holding_unit_of_work
        if unit is None:  # pragma: no cover - guarded by execute
            raise RuntimeError("transaction holding persistence is not configured")
        with unit.transaction():
            existing = unit.transactions.list_all()
            if unit.transactions.exists(transaction.id):
                raise DuplicateTransactionError(
                    f"Transaction ID already exists: {transaction.id}"
                )
            unit.transactions.add(transaction)
            projected = self.transaction_engine.project_current_holdings(
                [*existing, transaction], unit.holdings.list_all()
            )
            holding = next(
                item
                for item in projected
                if item.symbol == transaction.symbol
                and item.market is transaction.market
                and item.currency is transaction.currency
            )
            unit.holdings.upsert(holding)
            return _record(transaction, updated_holding_quantity=holding.quantity)

    def _execute_margin(
        self, transaction: Transaction, margin_principal: Decimal | None = None
    ) -> TransactionRecord:
        if self.margin_unit_of_work is None:
            raise ValueError("margin transaction persistence is not configured")
        unit = self.margin_unit_of_work
        with unit.transaction():
            existing = unit.transactions.list_all()
            if (
                unit.transactions.exists(transaction.id)
                or self._broker_order_exists(existing, transaction)
                or any(
                    self._same_margin_purchase(item, transaction) for item in existing
                )
            ):
                raise DuplicateTransactionError(
                    "Margin transaction already exists for the same trade details"
                )
            liabilities = [
                item
                for item in unit.liabilities.list_all()
                if item.liability_type is LiabilityType.MARGIN_FINANCING
            ]
            if len(liabilities) != 1:
                raise ValueError(
                    "exactly one aggregate margin-financing liability is required"
                )
            position = unit.margin_positions.get(
                liabilities[0].id, transaction.market.value, transaction.symbol
            )
            financing = self.margin_service.apply(
                transaction, liabilities[0], position, margin_principal
            )
            history = unit.liability_principal_events.list_by_liability(
                liabilities[0].id
            )
            sequence = 1 + max(
                (
                    item.sequence
                    for item in history
                    if item.effective_date == transaction.trade_date
                ),
                default=-1,
            )
            principal_event = LiabilityPrincipalEvent(
                id=self._principal_event_id(liabilities[0].id, transaction.id),
                liability_id=liabilities[0].id,
                effective_date=transaction.trade_date,
                sequence=sequence,
                event_type=LiabilityPrincipalEventType.INCREASE,
                principal_delta=financing.financed_principal,
                resulting_principal=None,
                source="transaction entry",
                reference=transaction.id,
                margin_position_id=financing.updated_position.id,
                financed_quantity_delta=transaction.quantity,
                notes="Broker-rounded margin principal from margin BUY",
                created_at=datetime.combine(
                    transaction.trade_date, datetime.min.time(), UTC
                ),
            )
            principal_event, restated_events = self._restated_principal_events(
                history,
                principal_event,
                current_principal=liabilities[0].principal,
                updated_principal=financing.updated_liability.principal,
            )
            unit.transactions.add(transaction)
            persisted_holdings = unit.holdings.list_all()
            projected = self.transaction_engine.project_current_holdings(
                [*existing, transaction], persisted_holdings
            )
            holding = next(
                item
                for item in projected
                if item.symbol == transaction.symbol
                and item.market is transaction.market
                and item.currency is transaction.currency
            )
            unit.holdings.upsert(holding)
            unit.liabilities.upsert(financing.updated_liability)
            unit.margin_positions.upsert(financing.updated_position)
            inserted = unit.liability_principal_events.insert_many_if_absent(
                [principal_event]
            )
            if inserted != 1:
                raise DuplicateTransactionError(
                    "Margin principal event already exists for this transaction"
                )
            for event in restated_events:
                unit.liability_principal_events.replace(event)
            return _record(
                transaction,
                financing,
                holding.quantity,
                principal_event_id=principal_event.id,
            )

    @staticmethod
    def _principal_event_id(liability_id: str, transaction_id: str) -> str:
        return str(
            uuid5(
                NAMESPACE_URL,
                f"margin-principal|{liability_id}|{transaction_id}",
            )
        )

    @staticmethod
    def _restated_principal_events(
        history: list[LiabilityPrincipalEvent],
        principal_event: LiabilityPrincipalEvent,
        *,
        current_principal: Decimal,
        updated_principal: Decimal,
    ) -> tuple[LiabilityPrincipalEvent, tuple[LiabilityPrincipalEvent, ...]]:
        """Insert a backdated event without invalidating later replay checkpoints."""
        if not history:
            return (
                principal_event.model_copy(
                    update={"resulting_principal": updated_principal}
                ),
                (),
            )
        timeline = LiabilityPrincipalEngine().timeline(history)
        if not timeline or timeline[-1].principal != current_principal:
            raise ValueError(
                "margin liability master does not match its principal event ledger"
            )
        ordered = sorted(
            [*history, principal_event],
            key=lambda item: (item.effective_date, item.sequence, item.id),
        )
        running = Decimal("0")
        inserted = principal_event
        replacements: list[LiabilityPrincipalEvent] = []
        for event in ordered:
            running += event.principal_delta
            restated = event.model_copy(update={"resulting_principal": running})
            if event.id == principal_event.id:
                inserted = restated
            elif restated != event:
                replacements.append(restated)
        if running != updated_principal:
            raise ValueError(
                "updated margin liability does not match its principal event ledger"
            )
        return inserted, tuple(replacements)

    @staticmethod
    def _same_margin_purchase(existing: Transaction, candidate: Transaction) -> bool:
        return (
            existing.financing_type is FinancingType.MARGIN
            and existing.symbol == candidate.symbol
            and existing.market is candidate.market
            and existing.transaction_type is candidate.transaction_type
            and existing.trade_date == candidate.trade_date
            and existing.settlement_date == candidate.settlement_date
            and existing.quantity == candidate.quantity
            and existing.price == candidate.price
            and existing.fees == candidate.fees
            and existing.taxes == candidate.taxes
            and existing.currency is candidate.currency
        )

    @staticmethod
    def _broker_order_exists(
        existing: list[Transaction], candidate: Transaction
    ) -> bool:
        return candidate.broker_order is not None and any(
            item.market is candidate.market
            and item.trade_date == candidate.trade_date
            and item.broker_order == candidate.broker_order
            for item in existing
        )


class ListTransactionsUseCase:
    """Query ordered transactions through the repository protocol."""

    def __init__(self, transactions: TransactionRepository) -> None:
        self.transactions = transactions

    def execute(
        self,
        *,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> TransactionList:
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("from-date must not be later than to-date")
        return TransactionList(
            tuple(
                _record(item)
                for item in self.transactions.list_filtered(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                )
            )
        )
