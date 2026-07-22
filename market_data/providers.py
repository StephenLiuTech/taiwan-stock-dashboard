"""Official TWSE and TPEx market-data providers."""

from collections.abc import Sequence
from typing import Protocol

from domain import Market
from market_data.transport import JSONRecord, JSONTransport, UrllibJSONTransport


class MarketDataProvider(Protocol):
    """Fetch raw end-of-day records for one market."""

    market: Market
    source: str

    def fetch(self) -> Sequence[JSONRecord]: ...


class TWSEProvider:
    """Fetch the latest official TWSE all-securities daily report."""

    market = Market.TWSE
    source = "TWSE_STOCK_DAY_ALL"
    endpoint = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"

    def __init__(self, transport: JSONTransport | None = None) -> None:
        self.transport = transport or UrllibJSONTransport()

    def fetch(self) -> Sequence[JSONRecord]:
        return self.transport.get_records(self.endpoint)


class TPExProvider:
    """Fetch the latest official TPEx mainboard daily close report."""

    market = Market.TPEX
    source = "TPEX_MAINBOARD_DAILY_CLOSE"
    endpoint = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"

    def __init__(self, transport: JSONTransport | None = None) -> None:
        self.transport = transport or UrllibJSONTransport()

    def fetch(self) -> Sequence[JSONRecord]:
        return self.transport.get_records(self.endpoint)
