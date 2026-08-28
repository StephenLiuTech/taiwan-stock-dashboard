"""Transaction-derived current holdings query workflow."""

from collections import defaultdict
from datetime import date

from pams.application.dto import HoldingQueryItem, HoldingsQueryResult
from pams.application.exceptions import (
    AmbiguousHoldingSymbolError,
    HoldingNotFoundError,
    InvalidHoldingHistoryError,
    ValuationRepositoryError,
)
from repositories.interfaces import (
    CorporateActionRepository,
    HoldingRepository,
    PriceQuoteRepository,
    TransactionRepository,
)
from services import TransactionEngine, TransactionEngineError, ValuationEngine


class QueryHoldingsUseCase:
    """Project the transaction ledger and value its active holdings."""

    def __init__(
        self,
        transactions: TransactionRepository,
        holdings: HoldingRepository,
        quotes: PriceQuoteRepository,
        transaction_engine: TransactionEngine | None = None,
        valuation_engine: ValuationEngine | None = None,
        corporate_actions: CorporateActionRepository | None = None,
    ) -> None:
        self.transactions = transactions
        self.holdings = holdings
        self.quotes = quotes
        self.transaction_engine = transaction_engine or TransactionEngine()
        self.valuation_engine = valuation_engine or ValuationEngine()
        self.corporate_actions = corporate_actions

    def execute(
        self, symbol: str | None = None, as_of_date: date | None = None
    ) -> HoldingsQueryResult:
        """Return all or one holding projected through an inclusive trade date."""
        try:
            transactions = (
                self.transactions.list_filtered(end_date=as_of_date)
                if as_of_date is not None
                else self.transactions.list_all()
            )
            persisted_holdings = self.holdings.list_all()
            actions = (
                self.corporate_actions.list_filtered(end_date=as_of_date)
                if self.corporate_actions is not None and as_of_date is not None
                else (
                    self.corporate_actions.list_all()
                    if self.corporate_actions is not None
                    else None
                )
            )
        except Exception as error:
            raise ValuationRepositoryError(
                "Unable to load transaction-derived holdings"
            ) from error
        try:
            projected = self.transaction_engine.project_transaction_holdings(
                transactions, persisted_holdings, actions
            )
        except TransactionEngineError as error:
            raise InvalidHoldingHistoryError(str(error)) from error

        requested = symbol.strip().upper() if symbol is not None else None
        if requested is not None:
            projected = tuple(item for item in projected if item.symbol == requested)
            if not projected:
                raise HoldingNotFoundError(
                    f"No active transaction-derived holding found for {requested}"
                )
            if len(projected) > 1:
                markets = ", ".join(sorted(item.market.value for item in projected))
                raise AmbiguousHoldingSymbolError(
                    f"Holding symbol {requested} is active in multiple markets: {markets}"
                )
        if not projected:
            return HoldingsQueryResult(None, (), as_of_date)

        quotes = []
        for holding in projected:
            try:
                quote = (
                    self.quotes.get_latest_on_or_before(
                        holding.symbol, holding.market.value, as_of_date
                    )
                    if as_of_date is not None
                    else self.quotes.get_latest(holding.symbol, holding.market.value)
                )
            except Exception as error:
                raise ValuationRepositoryError(
                    "Unable to load portfolio price quotes"
                ) from error
            if quote is None:
                continue
            else:
                quotes.append(quote)

        quoted_keys = {(quote.symbol, quote.market) for quote in quotes}
        quote_dates = {
            (quote.symbol, quote.market): quote.trade_date for quote in quotes
        }
        quoted_holdings = [
            holding
            for holding in projected
            if (holding.symbol, holding.market) in quoted_keys
        ]
        valuation = self.valuation_engine.valuate(quoted_holdings, quotes)
        transaction_history = defaultdict(list)
        for transaction in transactions:
            transaction_history[(transaction.symbol, transaction.market)].append(
                transaction
            )
        values = {(item.symbol, item.market): item for item in valuation.holdings}
        items = []
        for holding in projected:
            value = values.get((holding.symbol, holding.market))
            history = transaction_history[(holding.symbol, holding.market)]
            items.append(
                HoldingQueryItem(
                    symbol=holding.symbol,
                    market=holding.market.value,
                    quantity=holding.quantity,
                    average_cost=holding.average_cost,
                    total_cost=self.valuation_engine.cost_basis(holding),
                    latest_price=value.last_price if value else None,
                    market_value=value.market_value if value else None,
                    unrealized_pl=value.unrealized_pl if value else None,
                    unrealized_return=value.unrealized_return if value else None,
                    transaction_count=len(history),
                    first_trade_date=min(item.trade_date for item in history),
                    latest_trade_date=max(item.trade_date for item in history),
                    quote_date=quote_dates.get((holding.symbol, holding.market)),
                )
            )
        return HoldingsQueryResult(valuation.valuation_date, tuple(items), as_of_date)
