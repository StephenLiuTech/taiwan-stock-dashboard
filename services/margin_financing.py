"""Pure margin-financing rules independent of persistence and presentation."""

import re
from dataclasses import dataclass
from decimal import Decimal

from domain import (
    Currency,
    Liability,
    LiabilityType,
    Market,
    Transaction,
    TransactionType,
)


class MarginFinancingError(ValueError):
    """Raised when a requested margin operation violates domain rules."""


@dataclass(frozen=True)
class MarginFinancingResult:
    """Exact amounts and updated liability produced by one margin purchase."""

    gross_purchase_value: Decimal
    self_funded_amount: Decimal
    financed_principal: Decimal
    updated_margin_quantity: Decimal
    updated_liability: Liability


class MarginFinancingService:
    """Apply configured broker margin ratios to one Taiwan BUY transaction."""

    def __init__(self, self_funding_ratio: Decimal) -> None:
        if self_funding_ratio <= 0 or self_funding_ratio >= 1:
            raise MarginFinancingError(
                "margin self-funding ratio must be greater than 0 and less than 1"
            )
        self.self_funding_ratio = self_funding_ratio

    def apply(
        self, transaction: Transaction, liability: Liability
    ) -> MarginFinancingResult:
        """Return the exact liability change without mutating either input."""
        if transaction.transaction_type is not TransactionType.BUY:
            raise MarginFinancingError(
                "margin financing is supported only for BUY transactions"
            )
        if transaction.market not in (Market.TWSE, Market.TPEX):
            raise MarginFinancingError(
                "margin financing is supported only for Taiwan markets"
            )
        if transaction.currency is not Currency.TWD:
            raise MarginFinancingError("margin financing requires TWD currency")
        if transaction.quantity <= 0 or transaction.price <= 0:
            raise MarginFinancingError(
                "margin financing requires positive quantity and price"
            )
        if liability.liability_type is not LiabilityType.MARGIN_FINANCING:
            raise MarginFinancingError("margin-financing liability is required")
        symbol, existing_quantity = self._position(liability)
        if symbol is not None and symbol != transaction.symbol and existing_quantity:
            raise MarginFinancingError(
                f"margin liability already tracks a different symbol: {symbol}"
            )
        gross = transaction.quantity * transaction.price
        self_funded = gross * self.self_funding_ratio
        financed = gross - self_funded
        updated_quantity = existing_quantity + transaction.quantity
        updated = liability.model_copy(
            update={
                "principal": liability.principal + financed,
                "financed_symbol": transaction.symbol,
                "financed_quantity": updated_quantity,
                "collateral_description": (
                    f"{transaction.symbol} — {updated_quantity:,.0f} shares"
                ),
            }
        )
        return MarginFinancingResult(
            gross,
            self_funded,
            financed,
            updated_quantity,
            updated,
        )

    @staticmethod
    def _position(liability: Liability) -> tuple[str | None, Decimal]:
        if (
            liability.financed_symbol is not None
            and liability.financed_quantity is not None
        ):
            return liability.financed_symbol, liability.financed_quantity
        description = liability.collateral_description or ""
        match = re.search(
            r"([0-9A-Z]+)(?:\s+margin quantity:|\s*[—-])\s*([\d,]+)\s+shares",
            description,
        )
        if match is None:
            return None, Decimal("0")
        return match.group(1), Decimal(match.group(2).replace(",", ""))
