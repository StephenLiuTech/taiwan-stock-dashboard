"""Official latest-only TWSE and TPEx market-data providers."""

from collections.abc import Sequence

from domain import Market
from market_data.providers.current_tpex import CurrentTPExProvider
from market_data.transport import JSONRecord, JSONTransport, UrllibJSONTransport


class TWSEProvider:
    """Fetch the latest official TWSE all-securities daily report."""

    market = Market.TWSE
    source = "TWSE_STOCK_DAY_ALL"
    endpoint = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"

    def __init__(self, transport: JSONTransport | None = None) -> None:
        self.transport = transport or UrllibJSONTransport(
            provider_name=self.market.value
        )

    def fetch(self) -> Sequence[JSONRecord]:
        return self.transport.get_records(self.endpoint)


class TPExProvider(CurrentTPExProvider):
    """Backward-compatible name for the official current TPEx close provider."""
