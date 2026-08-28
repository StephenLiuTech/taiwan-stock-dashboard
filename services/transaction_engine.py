"""Framework-independent ordered transaction ledger calculations."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from domain import (
    CorporateAction,
    Currency,
    Holding,
    HoldingType,
    Market,
    PortfolioLedger,
    RealizedSale,
    Transaction,
    TransactionExpenseSummary,
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

    def __init__(self, corporate_actions: list[CorporateAction] | None = None) -> None:
        self.corporate_actions = tuple(corporate_actions or ())

    @staticmethod
    def summarize_expenses(
        transactions: list[Transaction],
    ) -> TransactionExpenseSummary:
        """Classify recorded fees and taxes without reconstructing positions."""
        return TransactionExpenseSummary(
            total_buy_fees=sum(
                (
                    item.fees
                    for item in transactions
                    if item.transaction_type is TransactionType.BUY
                ),
                Decimal("0"),
            ),
            total_sell_fees=sum(
                (
                    item.fees
                    for item in transactions
                    if item.transaction_type is TransactionType.SELL
                ),
                Decimal("0"),
            ),
            total_taxes=sum((item.taxes for item in transactions), Decimal("0")),
        )

    def build_ledger(
        self,
        transactions: list[Transaction],
        corporate_actions: list[CorporateAction] | None = None,
    ) -> PortfolioLedger:
        """Process transactions deterministically without mutating the input list."""
        states: dict[PositionKey, _PositionState] = {}
        total_realized_pnl = Decimal("0")
        total_buy_fees = Decimal("0")
        total_sell_fees = Decimal("0")
        total_taxes = Decimal("0")
        realized_sales: list[RealizedSale] = []
        events: list[tuple[object, int, str, Transaction | CorporateAction]] = [
            (
                item.trade_date,
                0 if item.transaction_type is TransactionType.BUY else 2,
                item.id,
                item,
            )
            for item in transactions
        ]
        events.extend(
            (item.effective_date, 1, item.id, item)
            for item in (
                list(self.corporate_actions)
                if corporate_actions is None
                else corporate_actions
            )
        )
        for _, _, _, event in sorted(events, key=lambda item: item[:3]):
            if isinstance(event, CorporateAction):
                self._apply_corporate_action(states, event)
                continue
            transaction = event
            self._validate_transaction(transaction)
            key = (transaction.symbol, transaction.market, transaction.currency)
            state = states.setdefault(key, _PositionState())
            if transaction.transaction_type is TransactionType.BUY:
                purchase_cost = transaction.quantity * transaction.price
                state.quantity += transaction.quantity
                state.cost_basis += purchase_cost
                state.average_cost = state.cost_basis / state.quantity
                total_buy_fees += transaction.fees
                total_taxes += transaction.taxes
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
            total_sell_fees += transaction.fees
            total_taxes += transaction.taxes
            realized = net_proceeds - allocated_cost
            realized_sales.append(
                RealizedSale(
                    transaction.id,
                    transaction.trade_date,
                    transaction.symbol,
                    transaction.market.value,
                    transaction.currency,
                    transaction.quantity,
                    state.average_cost,
                    allocated_cost,
                    transaction.quantity * transaction.price,
                    transaction.fees,
                    transaction.taxes,
                    net_proceeds,
                    realized,
                    realized / allocated_cost if allocated_cost else Decimal("0"),
                )
            )
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
        return PortfolioLedger(
            positions,
            total_realized_pnl,
            total_buy_fees,
            total_sell_fees,
            total_taxes,
            tuple(realized_sales),
        )

    @staticmethod
    def _apply_corporate_action(
        states: dict[PositionKey, _PositionState], action: CorporateAction
    ) -> None:
        matching = [
            (key, state)
            for key, state in states.items()
            if key[0] == action.symbol and key[1] is action.market
        ]
        if len(matching) != 1 or matching[0][1].quantity <= 0:
            raise InvalidTransactionHistoryError(
                f"Corporate action requires one active holding for {action.symbol} "
                f"in action {action.id}"
            )
        _, state = matching[0]
        state.quantity *= action.quantity_multiplier
        state.average_cost = state.cost_basis / state.quantity

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
        corporate_actions: list[CorporateAction] | None = None,
    ) -> tuple[Holding, ...]:
        """Overlay transaction-derived positions on holdings without ledger history."""
        if not transactions:
            return tuple(existing_holdings)
        projected = self.project_transaction_holdings(
            transactions, existing_holdings, corporate_actions
        )
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
        corporate_actions: list[CorporateAction] | None = None,
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
        ledger = self.build_ledger(transactions, corporate_actions)
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
