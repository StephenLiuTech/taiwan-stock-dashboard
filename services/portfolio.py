"""Portfolio valuation application service."""

from datetime import date
from decimal import Decimal

from domain import Holding, Liability, PortfolioSummary, PositionValuation, PriceQuote


class MissingPriceQuoteError(ValueError):
    """Raised when a holding has no matching quote."""


class PortfolioService:
    """Calculate portfolio positions and aggregate totals."""

    def value_portfolio(
        self,
        holdings: list[Holding],
        quotes: list[PriceQuote],
        liabilities: list[Liability],
        valuation_date: date,
    ) -> PortfolioSummary:
        """Build a summary from holdings, quotes, and liabilities."""
        currencies = {holding.currency for holding in holdings} | {
            liability.currency for liability in liabilities
        }
        if len(currencies) > 1:
            raise ValueError("Portfolio valuation requires a single currency")

        quote_by_key = {(quote.symbol, quote.market): quote for quote in quotes}
        inputs: list[tuple[Holding, PriceQuote]] = []
        for holding in holdings:
            quote = quote_by_key.get((holding.symbol, holding.market))
            if quote is None:
                raise MissingPriceQuoteError(f"Missing quote for {holding.symbol}")
            if quote.currency != holding.currency:
                raise ValueError(f"Currency mismatch for {holding.symbol}")
            inputs.append((holding, quote))

        total_market_value = sum(
            (holding.quantity * quote.close_price for holding, quote in inputs),
            Decimal("0"),
        )
        positions = [
            self._value_position(holding, quote, total_market_value)
            for holding, quote in inputs
        ]
        total_cost_basis = sum(
            (position.cost_basis for position in positions), Decimal("0")
        )
        total_liabilities = sum(
            (liability.principal for liability in liabilities), Decimal("0")
        )
        net_asset_value = total_market_value - total_liabilities
        leverage_ratio = (
            total_liabilities / total_market_value
            if total_market_value
            else Decimal("0")
        )
        return PortfolioSummary(
            valuation_date=valuation_date,
            positions=positions,
            total_market_value=total_market_value,
            total_cost_basis=total_cost_basis,
            total_unrealized_pnl=total_market_value - total_cost_basis,
            total_liabilities=total_liabilities,
            net_asset_value=net_asset_value,
            leverage_ratio=leverage_ratio,
        )

    @staticmethod
    def _value_position(
        holding: Holding, quote: PriceQuote, total_market_value: Decimal
    ) -> PositionValuation:
        cost_basis = holding.quantity * holding.average_cost
        market_value = holding.quantity * quote.close_price
        unrealized_pnl = market_value - cost_basis
        unrealized_return = unrealized_pnl / cost_basis if cost_basis else Decimal("0")
        previous_value = (
            holding.quantity * quote.previous_close
            if quote.previous_close is not None
            else market_value
        )
        return PositionValuation(
            holding_id=holding.id,
            symbol=holding.symbol,
            quantity=holding.quantity,
            average_cost=holding.average_cost,
            close_price=quote.close_price,
            cost_basis=cost_basis,
            market_value=market_value,
            unrealized_pnl=unrealized_pnl,
            unrealized_return=unrealized_return,
            portfolio_weight=(
                market_value / total_market_value
                if total_market_value
                else Decimal("0")
            ),
            daily_value_change=market_value - previous_value,
        )
