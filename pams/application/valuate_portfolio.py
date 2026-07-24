"""Current portfolio valuation application workflow."""

from pams.application.dto import PortfolioValuation
from pams.application.exceptions import (
    MissingQuoteError,
    ValuationDataUnavailableError,
    ValuationRepositoryError,
)
from repositories.interfaces import (
    HoldingRepository,
    PriceQuoteRepository,
    TransactionRepository,
)
from services import TransactionEngine, ValuationEngine


class ValuatePortfolioUseCase:
    """Load valuation inputs and return a presentation-neutral result."""

    def __init__(
        self,
        holdings: HoldingRepository,
        quotes: PriceQuoteRepository,
        engine: ValuationEngine | None = None,
        transactions: TransactionRepository | None = None,
        transaction_engine: TransactionEngine | None = None,
    ) -> None:
        self.holdings = holdings
        self.quotes = quotes
        self.engine = engine or ValuationEngine()
        self.transactions = transactions
        self.transaction_engine = transaction_engine or TransactionEngine()

    def execute(self) -> PortfolioValuation:
        """Value every persisted holding against its latest matching quote."""
        try:
            holdings = self.holdings.list_all()
            if self.transactions is not None:
                holdings = list(
                    self.transaction_engine.project_current_holdings(
                        self.transactions.list_all(), holdings
                    )
                )
        except Exception as error:
            raise ValuationRepositoryError(
                "Unable to load portfolio holdings"
            ) from error

        if not holdings:
            raise ValuationDataUnavailableError(
                "No portfolio holdings are available for valuation"
            )

        quotes = []
        missing = []
        for holding in holdings:
            try:
                quote = self.quotes.get_latest(holding.symbol, holding.market.value)
            except Exception as error:
                raise ValuationRepositoryError(
                    "Unable to load portfolio price quotes"
                ) from error
            if quote is None:
                missing.append(f"{holding.symbol} ({holding.market.value})")
            else:
                quotes.append(quote)
        if missing:
            raise MissingQuoteError("Missing latest quote for: " + ", ".join(missing))
        return self.engine.valuate(holdings, quotes)
