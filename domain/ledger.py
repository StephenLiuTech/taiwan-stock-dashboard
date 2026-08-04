"""Immutable transaction-ledger calculation results."""

from dataclasses import dataclass
from decimal import Decimal

from domain.enums import Currency, Market


@dataclass(frozen=True)
class TransactionPosition:
    """Current position and cumulative realized result for one ledger key."""

    symbol: str
    market: Market
    currency: Currency
    quantity: Decimal
    average_cost: Decimal
    cost_basis: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True)
class PortfolioLedger:
    """Positions, realized result, and expenses from the transaction ledger."""

    positions: tuple[TransactionPosition, ...]
    total_realized_pnl: Decimal
    total_buy_fees: Decimal = Decimal("0")
    total_sell_fees: Decimal = Decimal("0")
    total_taxes: Decimal = Decimal("0")

    @property
    def total_trading_expenses(self) -> Decimal:
        """Return all fees and taxes without mixing them into holding cost."""
        return self.total_buy_fees + self.total_sell_fees + self.total_taxes


@dataclass(frozen=True)
class TransactionExpenseSummary:
    """Trading expenses for an arbitrary transaction period."""

    total_buy_fees: Decimal
    total_sell_fees: Decimal
    total_taxes: Decimal

    @property
    def total_trading_expenses(self) -> Decimal:
        return self.total_buy_fees + self.total_sell_fees + self.total_taxes
