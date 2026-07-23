"""Portfolio valuation application service."""

from datetime import date
from decimal import Decimal

from domain import (
    Holding,
    HoldingValuation,
    Liability,
    PortfolioSummary,
    PositionValuation,
    PriceQuote,
)
from services.valuation_engine import ValuationEngine


class MissingPriceQuoteError(ValueError):
    """Raised when a holding has no matching quote."""


class PortfolioService:
    """Calculate portfolio positions and aggregate totals."""

    def __init__(self, valuation_engine: ValuationEngine | None = None) -> None:
        self.valuation_engine = valuation_engine or ValuationEngine()

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

        valuation = self.valuation_engine.valuate(holdings, quotes)
        valuation_by_key = {
            (item.symbol, item.market): item for item in valuation.holdings
        }
        total_market_value = valuation.total_market_value
        positions = [
            self._value_position(
                holding,
                quote,
                valuation_by_key[(holding.symbol, holding.market)],
            )
            for holding, quote in inputs
        ]
        total_cost_basis = valuation.total_cost
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
            total_unrealized_pnl=valuation.total_unrealized_pl,
            total_liabilities=total_liabilities,
            net_asset_value=net_asset_value,
            leverage_ratio=leverage_ratio,
        )

    @staticmethod
    def _value_position(
        holding: Holding,
        quote: PriceQuote,
        valuation: HoldingValuation,
    ) -> PositionValuation:
        cost_basis = valuation.cost_basis
        market_value = valuation.market_value
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
            unrealized_pnl=valuation.unrealized_pl,
            unrealized_return=valuation.unrealized_return,
            portfolio_weight=valuation.portfolio_weight,
            daily_value_change=market_value - previous_value,
            daily_return=(
                (quote.close_price - quote.previous_close) / quote.previous_close
                if quote.previous_close
                else None
            ),
        )
