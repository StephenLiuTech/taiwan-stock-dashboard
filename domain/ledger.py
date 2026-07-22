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
    """Active transaction-derived positions and total realized profit or loss."""

    positions: tuple[TransactionPosition, ...]
    total_realized_pnl: Decimal
