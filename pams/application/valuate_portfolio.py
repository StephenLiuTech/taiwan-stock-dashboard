"""Current portfolio valuation application workflow."""

from domain import Currency, Market, MultiCurrencyPortfolioValuation
from pams.application.dto import PortfolioValuation
from pams.application.exceptions import (
    MissingQuoteError,
    ValuationDataUnavailableError,
    ValuationRepositoryError,
)
from repositories.interfaces import (
    FxRateRepository,
    HoldingRepository,
    PriceQuoteRepository,
    TransactionRepository,
)
from services import MultiCurrencyValuationEngine, TransactionEngine, ValuationEngine


class ValuatePortfolioUseCase:
    """Load valuation inputs and return a presentation-neutral result."""

    def __init__(
        self,
        holdings: HoldingRepository,
        quotes: PriceQuoteRepository,
        engine: ValuationEngine | None = None,
        transactions: TransactionRepository | None = None,
        transaction_engine: TransactionEngine | None = None,
        fx_rates: FxRateRepository | None = None,
        multi_currency_engine: MultiCurrencyValuationEngine | None = None,
    ) -> None:
        self.holdings = holdings
        self.quotes = quotes
        self.engine = engine or ValuationEngine()
        self.transactions = transactions
        self.transaction_engine = transaction_engine or TransactionEngine()
        self.fx_rates = fx_rates
        self.multi_currency_engine = (
            multi_currency_engine or MultiCurrencyValuationEngine()
        )

    def execute(self) -> PortfolioValuation | MultiCurrencyPortfolioValuation:
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
        if any(holding.market is Market.US for holding in holdings):
            report_date = max(quote.trade_date for quote in quotes)
            fx_rate = (
                self.fx_rates.get_latest_on_or_before(
                    Currency.USD.value, Currency.TWD.value, report_date
                )
                if self.fx_rates
                else None
            )
            if fx_rate is None:
                raise MissingQuoteError(
                    "Missing eligible USD/TWD FX rate for multi-market valuation"
                )
            return self.multi_currency_engine.valuate(
                report_date, holdings, quotes, fx_rate
            )
        return self.engine.valuate(holdings, quotes)
