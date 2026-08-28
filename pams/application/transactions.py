"""Transaction entry and query application workflows."""

from datetime import date
from decimal import Decimal

from domain import (
    Currency,
    FinancingType,
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
from repositories.interfaces import MarginTransactionUnitOfWork, TransactionRepository
from services import MarginFinancingResult, MarginFinancingService, TransactionEngine


def _record(
    transaction: Transaction,
    financing: MarginFinancingResult | None = None,
    updated_holding_quantity: Decimal | None = None,
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
    )


class AddTransactionUseCase:
    """Validate and persist one new transaction through a repository protocol."""

    def __init__(
        self,
        transactions: TransactionRepository,
        margin_unit_of_work: MarginTransactionUnitOfWork | None = None,
        margin_self_funding_ratio: Decimal = Decimal("0.40"),
        transaction_engine: TransactionEngine | None = None,
    ) -> None:
        self.transactions = transactions
        self.margin_unit_of_work = margin_unit_of_work
        self.margin_service = MarginFinancingService(margin_self_funding_ratio)
        self.transaction_engine = transaction_engine or TransactionEngine()

    def execute(self, command: AddTransactionCommand) -> TransactionRecord:
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
            "notes": command.notes,
        }
        if command.transaction_id is not None:
            transaction_values["id"] = command.transaction_id
        transaction = Transaction.model_validate(transaction_values)
        if transaction.financing_type is FinancingType.MARGIN:
            return self._execute_margin(transaction)
        if self.transactions.exists(transaction.id):
            raise DuplicateTransactionError(
                f"Transaction ID already exists: {transaction.id}"
            )
        self.transactions.add(transaction)
        return _record(transaction)

    def _execute_margin(self, transaction: Transaction) -> TransactionRecord:
        if self.margin_unit_of_work is None:
            raise ValueError("margin transaction persistence is not configured")
        unit = self.margin_unit_of_work
        with unit.transaction():
            existing = unit.transactions.list_all()
            if unit.transactions.exists(transaction.id) or any(
                self._same_margin_purchase(item, transaction) for item in existing
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
                raise ValueError("exactly one margin-financing liability is required")
            financing = self.margin_service.apply(transaction, liabilities[0])
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
            return _record(transaction, financing, holding.quantity)

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
