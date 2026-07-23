"""Current portfolio valuation application workflow."""

from dataclasses import replace
from decimal import Decimal

from pams.application.dto import PortfolioValuation
from pams.application.exceptions import MissingQuoteError
from repositories.interfaces import HoldingRepository, PriceQuoteRepository
from services import ValuationEngine


class ValuatePortfolioUseCase:
    """Load valuation inputs and return a presentation-neutral result."""

    def __init__(
        self,
        holdings: HoldingRepository,
        quotes: PriceQuoteRepository,
        engine: ValuationEngine | None = None,
    ) -> None:
        self.holdings = holdings
        self.quotes = quotes
        self.engine = engine or ValuationEngine()

    def execute(self) -> PortfolioValuation:
        """Value every persisted holding against its latest matching quote."""
        holdings = self.holdings.list_all()
        quotes = []
        missing = []
        for holding in holdings:
            quote = self.quotes.get_latest(holding.symbol, holding.market.value)
            if quote is None:
                missing.append(f"{holding.symbol} ({holding.market.value})")
            else:
                quotes.append(quote)
        if missing:
            raise MissingQuoteError("Missing latest quote for: " + ", ".join(missing))
        valuation = self.engine.valuate(holdings, quotes)
        total = valuation.total_market_value
        valued_holdings = tuple(
            replace(
                item,
                portfolio_weight=(item.market_value / total if total else Decimal("0")),
            )
            for item in valuation.holdings
        )
        return replace(valuation, holdings=valued_holdings)
