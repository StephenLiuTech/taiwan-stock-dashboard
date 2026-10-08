"""Transaction-aware daily position performance calculations."""

from dataclasses import dataclass
from decimal import Decimal

from domain import Holding, PriceQuote, Transaction, TransactionType


class DailyPnlCalculationError(ValueError):
    """Daily transactions are inconsistent with the closing holding quantity."""


@dataclass(frozen=True)
class DailyPnlResult:
    """One position's price-only daily performance before fees and taxes."""

    profit_loss: Decimal
    return_percentage: Decimal | None


class DailyPnlCalculator:
    """Calculate daily P/L from overnight inventory and same-day trade cash flows."""

    @staticmethod
    def calculate(
        holding: Holding,
        quote: PriceQuote,
        transactions: list[Transaction],
        *,
        conversion_rate: Decimal = Decimal("1"),
    ) -> DailyPnlResult:
        relevant = [
            item
            for item in transactions
            if item.trade_date == quote.trade_date
            and item.symbol == holding.symbol
            and item.market is holding.market
            and item.currency is holding.currency
        ]
        bought_quantity = sum(
            (
                item.quantity
                for item in relevant
                if item.transaction_type is TransactionType.BUY
            ),
            Decimal("0"),
        )
        sold_quantity = sum(
            (
                item.quantity
                for item in relevant
                if item.transaction_type is TransactionType.SELL
            ),
            Decimal("0"),
        )
        opening_quantity = holding.quantity - bought_quantity + sold_quantity
        if opening_quantity < 0:
            raise DailyPnlCalculationError(
                f"Daily transactions imply negative opening quantity for "
                f"{holding.symbol} on {quote.trade_date}"
            )
        purchase_value = sum(
            (
                item.quantity * item.price
                for item in relevant
                if item.transaction_type is TransactionType.BUY
            ),
            Decimal("0"),
        )
        sale_value = sum(
            (
                item.quantity * item.price
                for item in relevant
                if item.transaction_type is TransactionType.SELL
            ),
            Decimal("0"),
        )
        if quote.previous_close is None and opening_quantity > 0:
            return DailyPnlResult(Decimal("0"), None)

        opening_value = opening_quantity * (quote.previous_close or Decimal("0"))
        closing_value = holding.quantity * quote.close_price
        profit_loss_native = closing_value - opening_value - purchase_value + sale_value
        capital_exposed_native = opening_value + purchase_value
        return DailyPnlResult(
            profit_loss=profit_loss_native * conversion_rate,
            return_percentage=(
                profit_loss_native / capital_exposed_native
                if capital_exposed_native
                else Decimal("0")
            ),
        )
