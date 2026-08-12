"""Official TPEx current-publication close provider and date-aware selector."""

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import date

from domain import Market
from market_data.exceptions import (
    MarketDateUnavailableError,
    ProviderDataError,
    SourceDateError,
    SourceDateMismatchError,
)
from market_data.normalizer import QuoteNormalizer
from market_data.providers.historical_tpex import HistoricalTPExProvider
from market_data.transport import JSONRecord, JSONTransport, UrllibJSONTransport

_LOGGER = logging.getLogger(__name__)


class CurrentTPExProvider:
    """Fetch the latest completed official TPEx main-board close publication."""

    market = Market.TPEX
    source = "TPEX_MAINBOARD_DAILY_CLOSE_CURRENT"
    endpoint = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    required_fields = frozenset(
        {"Date", "SecuritiesCompanyCode", "CompanyName", "Close", "Change"}
    )

    def __init__(
        self,
        transport: JSONTransport | None = None,
        *,
        expected_date: date | None = None,
        required_symbols: frozenset[str] = frozenset(),
        normalizer: QuoteNormalizer | None = None,
    ) -> None:
        self.transport = transport or UrllibJSONTransport(
            provider_name="TPEx current closes"
        )
        self.expected_date = expected_date
        self.required_symbols = frozenset(
            symbol.strip().upper() for symbol in required_symbols
        )
        self.normalizer = normalizer or QuoteNormalizer()

    def fetch(self) -> Sequence[JSONRecord]:
        """Return only a non-empty, single-date, structurally complete dataset."""
        records = list(self.transport.get_records(self.endpoint))
        if not records:
            raise MarketDateUnavailableError("TPEx current close publication is empty")
        for record in records:
            if not isinstance(record, Mapping) or not self.required_fields.issubset(
                record
            ):
                raise ProviderDataError(
                    "TPEx current close publication is missing required fields"
                )
        source_dates = {
            self.normalizer.extract_trade_date(record) for record in records
        }
        if len(source_dates) != 1:
            raise SourceDateError(
                "TPEx current close publication contains mixed source dates"
            )
        source_date = source_dates.pop()
        if self.expected_date is not None and source_date != self.expected_date:
            raise SourceDateMismatchError(Market.TPEX, self.expected_date, source_date)
        symbols = {
            str(record["SecuritiesCompanyCode"]).strip().upper() for record in records
        }
        missing = self.required_symbols - symbols
        if missing:
            raise ProviderDataError(
                "TPEx current close publication is incomplete for required symbols: "
                + ", ".join(sorted(missing))
            )
        return records


class DateAwareTPExProvider:
    """Prefer current publication for today and historical data otherwise."""

    market = Market.TPEX
    source = "TPEX_DATE_AWARE_CLOSE"

    def __init__(
        self,
        trade_date: date,
        current_provider: CurrentTPExProvider,
        historical_provider: HistoricalTPExProvider,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.trade_date = trade_date
        self.current_provider = current_provider
        self.historical_provider = historical_provider
        self.today = today

    def fetch(self) -> Sequence[JSONRecord]:
        """Use current closes only for today, with exact-date historical fallback."""
        if self.trade_date < self.today():
            return self.historical_provider.fetch()
        if self.trade_date > self.today():
            raise MarketDateUnavailableError("TPEx future close data is unavailable")
        try:
            return self.current_provider.fetch()
        except ProviderDataError as error:
            _LOGGER.warning(
                "TPEx current close unavailable; trying exact-date historical "
                "fallback: date=%s error=%s",
                self.trade_date,
                type(error).__name__,
            )
            return self.historical_provider.fetch()
