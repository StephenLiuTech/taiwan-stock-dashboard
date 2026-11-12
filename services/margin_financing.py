"""Pure margin-financing rules independent of persistence and presentation."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal

from domain import (
    Currency,
    Liability,
    LiabilityType,
    MarginFinancingPosition,
    Market,
    Transaction,
    TransactionType,
    margin_position_id,
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
    updated_position: MarginFinancingPosition


class MarginFinancingService:
    """Apply configured broker margin ratios to one Taiwan BUY transaction."""

    PRINCIPAL_INCREMENT = Decimal("1000")

    def __init__(self, self_funding_ratio: Decimal) -> None:
        if self_funding_ratio <= 0 or self_funding_ratio >= 1:
            raise MarginFinancingError(
                "margin self-funding ratio must be greater than 0 and less than 1"
            )
        self.self_funding_ratio = self_funding_ratio

    def apply(
        self,
        transaction: Transaction,
        liability: Liability,
        position: MarginFinancingPosition | None = None,
        financed_principal: Decimal | None = None,
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
        if position is not None and (
            position.liability_id != liability.id
            or position.market is not transaction.market
            or position.symbol != transaction.symbol
        ):
            raise MarginFinancingError("margin position does not match transaction")
        legacy_symbol, legacy_quantity = self._position(liability)
        if position is None:
            existing_quantity = (
                legacy_quantity if legacy_symbol == transaction.symbol else Decimal("0")
            )
            existing_principal = (
                liability.principal
                if legacy_symbol == transaction.symbol
                else Decimal("0")
            )
            timestamp = datetime.combine(
                transaction.trade_date, datetime.min.time(), UTC
            )
            position = MarginFinancingPosition(
                id=margin_position_id(
                    liability.id, transaction.market, transaction.symbol
                ),
                liability_id=liability.id,
                market=transaction.market,
                symbol=transaction.symbol,
                currency=transaction.currency,
                principal=existing_principal,
                financed_quantity=existing_quantity,
                created_at=timestamp,
                updated_at=timestamp,
            )
        else:
            existing_quantity = position.financed_quantity
            existing_principal = position.principal
        gross = transaction.quantity * transaction.price
        if financed_principal is not None:
            if financed_principal <= 0 or financed_principal > gross:
                raise MarginFinancingError(
                    "broker-confirmed margin principal must be positive and not "
                    "exceed gross purchase value"
                )
            financed = financed_principal
        else:
            raw_financed = gross * (Decimal("1") - self.self_funding_ratio)
            financed = (raw_financed / self.PRINCIPAL_INCREMENT).to_integral_value(
                rounding=ROUND_FLOOR
            ) * self.PRINCIPAL_INCREMENT
        self_funded = gross - financed
        updated_quantity = existing_quantity + transaction.quantity
        liability_updates: dict[str, object] = {
            "principal": liability.principal + financed,
        }
        if legacy_symbol in (None, transaction.symbol):
            liability_updates.update(
                {
                    "financed_symbol": transaction.symbol,
                    "financed_quantity": updated_quantity,
                    "collateral_description": (
                        f"{transaction.symbol} — {updated_quantity:,.0f} shares"
                    ),
                }
            )
        updated = liability.model_copy(update=liability_updates)
        updated_position = position.model_copy(
            update={
                "principal": existing_principal + financed,
                "financed_quantity": updated_quantity,
                "updated_at": datetime.combine(
                    transaction.trade_date, datetime.min.time(), UTC
                ),
            }
        )
        return MarginFinancingResult(
            gross,
            self_funded,
            financed,
            updated_quantity,
            updated,
            updated_position,
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
