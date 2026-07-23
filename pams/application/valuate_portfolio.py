"""Current portfolio valuation application workflow."""

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
        return self.engine.valuate(holdings, quotes)
