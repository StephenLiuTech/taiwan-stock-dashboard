"""Dry-run transaction-ledger holding projection workflow."""

from collections.abc import Mapping

from pams.application.dto import (
    LedgerPositionResult,
    ProjectedHoldingResult,
    RebuildHoldingsResult,
)
from repositories.interfaces import HoldingRepository, TransactionRepository
from services import HoldingProjectionMetadata, TransactionEngine
from services.transaction_engine import PositionKey


class RebuildHoldingsUseCase:
    """Build and project holdings from repositories without persisting changes."""

    def __init__(
        self,
        transactions: TransactionRepository,
        holdings: HoldingRepository,
        metadata: Mapping[PositionKey, HoldingProjectionMetadata],
        engine: TransactionEngine | None = None,
    ) -> None:
        self.transactions = transactions
        self.holdings = holdings
        self.metadata = metadata
        self.engine = engine or TransactionEngine()

    def execute(self) -> RebuildHoldingsResult:
        """Return an immutable dry-run projection; never write repositories."""
        transactions = self.transactions.list_all()
        ledger = self.engine.build_ledger(transactions)
        projected = self.engine.project_holdings(
            ledger, self.metadata, self.holdings.list_all()
        )
        return RebuildHoldingsResult(
            positions=tuple(
                LedgerPositionResult(
                    symbol=item.symbol,
                    market=item.market.value,
                    currency=item.currency.value,
                    quantity=item.quantity,
                    average_cost=item.average_cost,
                    cost_basis=item.cost_basis,
                    realized_pnl=item.realized_pnl,
                )
                for item in ledger.positions
            ),
            projected_holdings=tuple(
                ProjectedHoldingResult(
                    id=item.id,
                    symbol=item.symbol,
                    name=item.name,
                    market=item.market.value,
                    currency=item.currency.value,
                    quantity=item.quantity,
                    average_cost=item.average_cost,
                    holding_type=item.holding_type.value,
                )
                for item in projected
            ),
            total_realized_pnl=ledger.total_realized_pnl,
            total_buy_fees=ledger.total_buy_fees,
            total_sell_fees=ledger.total_sell_fees,
            total_taxes=ledger.total_taxes,
            total_trading_expenses=ledger.total_trading_expenses,
            transaction_count=len(transactions),
        )
