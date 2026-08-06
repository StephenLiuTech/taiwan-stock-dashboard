"""Transaction entry and query application workflows."""

from datetime import date

from domain import Currency, Market, Transaction, TransactionType
from pams.application.dto import (
    AddTransactionCommand,
    TransactionList,
    TransactionRecord,
)
from pams.application.exceptions import DuplicateTransactionError
from repositories.interfaces import TransactionRepository


def _record(transaction: Transaction) -> TransactionRecord:
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
    )


class AddTransactionUseCase:
    """Validate and persist one new transaction through a repository protocol."""

    def __init__(self, transactions: TransactionRepository) -> None:
        self.transactions = transactions

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
            "notes": command.notes,
        }
        if command.transaction_id is not None:
            transaction_values["id"] = command.transaction_id
        transaction = Transaction.model_validate(transaction_values)
        if self.transactions.exists(transaction.id):
            raise DuplicateTransactionError(
                f"Transaction ID already exists: {transaction.id}"
            )
        self.transactions.add(transaction)
        return _record(transaction)


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
