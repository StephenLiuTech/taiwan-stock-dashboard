"""Framework-independent ordered transaction ledger calculations."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from domain import (
    Currency,
    Holding,
    HoldingType,
    Market,
    PortfolioLedger,
    Transaction,
    TransactionPosition,
    TransactionType,
)

PositionKey = tuple[str, Market, Currency]


class TransactionEngineError(ValueError):
    """Base error for invalid transaction-ledger input."""


class OversellError(TransactionEngineError):
    """Raised when a sale exceeds the currently held quantity."""


class InvalidTransactionHistoryError(TransactionEngineError):
    """Raised when ordered transaction history violates ledger invariants."""


class UnsupportedTransactionTypeError(TransactionEngineError):
    """Raised when a transaction direction is not supported."""


@dataclass(frozen=True)
class HoldingProjectionMetadata:
    """Non-financial metadata required to construct a Holding."""

    name: str
    holding_type: HoldingType


@dataclass
class _PositionState:
    quantity: Decimal = Decimal("0")
    cost_basis: Decimal = Decimal("0")
    average_cost: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")


class TransactionEngine:
    """Build moving-weighted-average positions from immutable transaction input."""

    def build_ledger(self, transactions: list[Transaction]) -> PortfolioLedger:
        """Process transactions deterministically without mutating the input list."""
        states: dict[PositionKey, _PositionState] = {}
        total_realized_pnl = Decimal("0")
        ordered = sorted(
            transactions,
            key=lambda item: (
                item.trade_date,
                0 if item.transaction_type is TransactionType.BUY else 1,
                item.id,
            ),
        )
        for transaction in ordered:
            self._validate_transaction(transaction)
            key = (transaction.symbol, transaction.market, transaction.currency)
            state = states.setdefault(key, _PositionState())
            if transaction.transaction_type is TransactionType.BUY:
                purchase_cost = (
                    transaction.quantity * transaction.price
                    + transaction.fees
                    + transaction.taxes
                )
                state.quantity += transaction.quantity
                state.cost_basis += purchase_cost
                state.average_cost = state.cost_basis / state.quantity
                continue
            if state.quantity == 0:
                raise InvalidTransactionHistoryError(
                    f"SELL before BUY for {transaction.symbol} "
                    f"in transaction {transaction.id}"
                )
            if transaction.quantity > state.quantity:
                raise OversellError(
                    f"SELL exceeds held quantity for {transaction.symbol} "
                    f"in transaction {transaction.id}"
                )
            allocated_cost = state.average_cost * transaction.quantity
            net_proceeds = (
                transaction.quantity * transaction.price
                - transaction.fees
                - transaction.taxes
            )
            realized = net_proceeds - allocated_cost
            state.realized_pnl += realized
            total_realized_pnl += realized
            state.quantity -= transaction.quantity
            state.cost_basis -= allocated_cost
            if state.quantity == 0:
                state.cost_basis = Decimal("0")
                state.average_cost = Decimal("0")

        positions = tuple(
            TransactionPosition(
                symbol=symbol,
                market=market,
                currency=currency,
                quantity=state.quantity,
                average_cost=state.average_cost,
                cost_basis=state.cost_basis,
                realized_pnl=state.realized_pnl,
            )
            for (symbol, market, currency), state in sorted(
                states.items(),
                key=lambda item: (
                    item[0][0],
                    item[0][1].value,
                    item[0][2].value,
                ),
            )
            if state.quantity != 0
        )
        return PortfolioLedger(positions, total_realized_pnl)

    def project_holdings(
        self,
        ledger: PortfolioLedger,
        metadata: Mapping[PositionKey, HoldingProjectionMetadata],
        existing_holdings: list[Holding] | None = None,
    ) -> tuple[Holding, ...]:
        """Project active ledger positions without writing a repository."""
        existing_by_key = {
            (holding.symbol, holding.market, holding.currency): holding
            for holding in (existing_holdings or [])
        }
        projected = []
        for position in ledger.positions:
            key = (position.symbol, position.market, position.currency)
            details = metadata.get(key)
            if details is None:
                raise InvalidTransactionHistoryError(
                    f"Missing holding metadata for {position.symbol}"
                )
            existing = existing_by_key.get(key)
            if existing is not None:
                projected.append(
                    existing.model_copy(
                        update={
                            "name": details.name,
                            "holding_type": details.holding_type,
                            "quantity": position.quantity,
                            "average_cost": position.average_cost,
                        }
                    )
                )
            else:
                projected.append(
                    Holding(
                        id="holding-ledger-"
                        f"{position.market.value.lower()}-"
                        f"{position.currency.value.lower()}-{position.symbol.lower()}",
                        symbol=position.symbol,
                        name=details.name,
                        market=position.market,
                        currency=position.currency,
                        quantity=position.quantity,
                        average_cost=position.average_cost,
                        holding_type=details.holding_type,
                        is_pledged=False,
                        notes=None,
                    )
                )
        return tuple(projected)

    def project_current_holdings(
        self,
        transactions: list[Transaction],
        existing_holdings: list[Holding],
    ) -> tuple[Holding, ...]:
        """Overlay transaction-derived positions on holdings without ledger history."""
        if not transactions:
            return tuple(existing_holdings)
        projected = self.project_transaction_holdings(transactions, existing_holdings)
        existing_by_key = {
            (holding.symbol, holding.market, holding.currency): holding
            for holding in existing_holdings
        }
        transaction_keys = {
            (transaction.symbol, transaction.market, transaction.currency)
            for transaction in transactions
        }
        unchanged = (
            holding
            for key, holding in existing_by_key.items()
            if key not in transaction_keys
        )
        return tuple(
            sorted(
                (*projected, *unchanged),
                key=lambda holding: (
                    holding.symbol,
                    holding.market.value,
                    holding.currency.value,
                ),
            )
        )

    def project_transaction_holdings(
        self,
        transactions: list[Transaction],
        existing_holdings: list[Holding],
    ) -> tuple[Holding, ...]:
        """Project only active positions represented by transaction history."""
        if not transactions:
            return ()
        existing_by_key = {
            (holding.symbol, holding.market, holding.currency): holding
            for holding in existing_holdings
        }
        metadata = {
            key: HoldingProjectionMetadata(holding.name, holding.holding_type)
            for key, holding in existing_by_key.items()
        }
        for transaction in transactions:
            key = (transaction.symbol, transaction.market, transaction.currency)
            metadata.setdefault(
                key,
                HoldingProjectionMetadata(transaction.symbol, HoldingType.STOCK),
            )
        ledger = self.build_ledger(transactions)
        return self.project_holdings(ledger, metadata, existing_holdings)

    @staticmethod
    def _validate_transaction(transaction: Transaction) -> None:
        if transaction.quantity <= 0:
            raise InvalidTransactionHistoryError(
                f"Transaction quantity must be positive for {transaction.symbol} "
                f"in transaction {transaction.id}"
            )
        if not isinstance(transaction.market, Market) or not isinstance(
            transaction.currency, Currency
        ):
            raise InvalidTransactionHistoryError(
                f"Inconsistent market or currency for {transaction.symbol} "
                f"in transaction {transaction.id}"
            )
        if transaction.transaction_type not in (
            TransactionType.BUY,
            TransactionType.SELL,
        ):
            raise UnsupportedTransactionTypeError(
                f"Unsupported transaction type for {transaction.symbol} "
                f"in transaction {transaction.id}"
            )
