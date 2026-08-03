"""Pure Decimal portfolio valuation calculations."""

from dataclasses import replace
from decimal import Decimal

from domain import Holding, HoldingValuation, PortfolioValuation, PriceQuote


class ValuationEngine:
    """Calculate current holding and portfolio values without data access."""

    @staticmethod
    def cost_basis(holding: Holding) -> Decimal:
        """Return the holding cost basis using the canonical valuation formula."""
        return holding.quantity * holding.average_cost

    def valuate(
        self, holdings: list[Holding], quotes: list[PriceQuote]
    ) -> PortfolioValuation:
        """Value holdings against matching latest quotes supplied by the caller."""
        quote_by_key = {(quote.symbol, quote.market): quote for quote in quotes}
        values = []
        for holding in holdings:
            quote = quote_by_key[(holding.symbol, holding.market)]
            cost_basis = self.cost_basis(holding)
            market_value = holding.quantity * quote.close_price
            unrealized = market_value - cost_basis
            values.append(
                HoldingValuation(
                    symbol=holding.symbol,
                    market=holding.market,
                    quantity=holding.quantity,
                    average_cost=holding.average_cost,
                    last_price=quote.close_price,
                    cost_basis=cost_basis,
                    market_value=market_value,
                    unrealized_pl=unrealized,
                    unrealized_return=(
                        unrealized / cost_basis if cost_basis else Decimal("0")
                    ),
                )
            )
        total_cost = sum((item.cost_basis for item in values), Decimal("0"))
        total_market_value = sum((item.market_value for item in values), Decimal("0"))
        total_unrealized = total_market_value - total_cost
        weighted_values = tuple(
            replace(
                item,
                portfolio_weight=(
                    item.market_value / total_market_value
                    if total_market_value
                    else Decimal("0")
                ),
            )
            for item in values
        )
        return PortfolioValuation(
            valuation_date=(
                max(quote.trade_date for quote in quotes) if quotes else None
            ),
            total_cost=total_cost,
            total_market_value=total_market_value,
            total_unrealized_pl=total_unrealized,
            total_return=(
                total_unrealized / total_cost if total_cost else Decimal("0")
            ),
            holdings=weighted_values,
        )
