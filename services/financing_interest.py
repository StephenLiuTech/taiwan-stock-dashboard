"""Pure daily financing-interest calculation."""

from dataclasses import dataclass
from decimal import Decimal

DAYS_PER_YEAR = Decimal("365")


@dataclass(frozen=True)
class DailyFinancingInterest:
    """One liability's interest for one calendar day."""

    principal: Decimal
    annual_rate: Decimal
    amount: Decimal


class FinancingInterestEngine:
    """Calculate Actual/365 interest without persistence or clocks."""

    def calculate(
        self, principal: Decimal, annual_rate: Decimal
    ) -> DailyFinancingInterest:
        if principal < 0:
            raise ValueError("financing principal cannot be negative")
        if annual_rate < 0:
            raise ValueError("financing annual rate cannot be negative")
        return DailyFinancingInterest(
            principal,
            annual_rate,
            principal * annual_rate / DAYS_PER_YEAR,
        )
