"""Application workflows for manually maintained watchlist entries."""

from decimal import Decimal

from domain import Market, WatchlistItem, WatchlistView
from repositories import PriceQuoteRepository, WatchlistRepository


class WatchlistError(ValueError):
    """Base error for watchlist operations."""


class DuplicateWatchlistItemError(WatchlistError):
    """The symbol and market already exist in the watchlist."""


class WatchlistItemNotFoundError(WatchlistError):
    """The requested watchlist entry does not exist."""


class WatchlistUseCase:
    """Coordinate watchlist persistence and optional quote enrichment."""

    def __init__(
        self, watchlist: WatchlistRepository, quotes: PriceQuoteRepository
    ) -> None:
        self.watchlist = watchlist
        self.quotes = quotes

    def add(
        self,
        symbol: str,
        market: str,
        *,
        display_name: str | None = None,
        target_price: Decimal | None = None,
        buy_below_price: Decimal | None = None,
        notes: str | None = None,
    ) -> WatchlistView:
        try:
            normalized_market = Market(market.strip().upper())
        except ValueError as error:
            raise WatchlistError(f"Unsupported market: {market}") from error
        item = WatchlistItem(
            symbol=symbol,
            market=normalized_market,
            display_name=display_name,
            target_price=target_price,
            buy_below_price=buy_below_price,
            notes=notes,
        )
        if self.watchlist.get(item.symbol):
            raise DuplicateWatchlistItemError(
                f"Watchlist entry already exists for {item.symbol} {item.market.value}"
            )
        self.watchlist.add(item)
        return self._view(item)

    def list(self) -> tuple[WatchlistView, ...]:
        return tuple(self._view(item) for item in self.watchlist.list_all())

    def show(self, symbol: str) -> WatchlistView:
        item = self.watchlist.get(symbol.strip().upper())
        if item is None:
            raise WatchlistItemNotFoundError(
                f"Watchlist entry not found for {symbol.strip().upper()}"
            )
        return self._view(item)

    def remove(self, symbol: str) -> None:
        if not self.watchlist.remove(symbol.strip().upper()):
            raise WatchlistItemNotFoundError(
                f"Watchlist entry not found for {symbol.strip().upper()}"
            )

    def _view(self, item: WatchlistItem) -> WatchlistView:
        quote = self.quotes.get_latest(item.symbol, item.market.value)
        return WatchlistView(
            item.symbol,
            item.market.value,
            item.display_name,
            item.target_price,
            item.buy_below_price,
            quote.close_price if quote else None,
            quote.trade_date if quote else None,
            item.notes,
        )
