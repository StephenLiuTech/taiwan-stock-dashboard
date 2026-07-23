"""Immutable portfolio valuation calculation results."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from domain.enums import Market


@dataclass(frozen=True)
class HoldingValuation:
    """Current price valuation for one holding."""

    symbol: str
    market: Market
    quantity: Decimal
    average_cost: Decimal
    last_price: Decimal
    cost_basis: Decimal
    market_value: Decimal
    unrealized_pl: Decimal
    unrealized_return: Decimal


@dataclass(frozen=True)
class PortfolioValuation:
    """Current portfolio totals and holding-level valuations."""

    valuation_date: date | None
    total_cost: Decimal
    total_market_value: Decimal
    total_unrealized_pl: Decimal
    total_return: Decimal
    holdings: tuple[HoldingValuation, ...]
