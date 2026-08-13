"""Opt-in US quote and FX provider boundaries and Alpha Vantage adapters."""

import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol
from urllib.parse import urlencode

from domain import Currency, FxRate, Market, PriceQuote
from market_data.exceptions import (
    ProviderAuthenticationError,
    ProviderDataError,
    ProviderInformationError,
    ProviderRateLimitError,
    ProviderSymbolError,
)
from market_data.transport import JSONDocumentTransport

_LOGGER = logging.getLogger(__name__)


class USMarketDataProvider(Protocol):
    """Fetch completed US daily closes at or before a report date."""

    source: str

    def fetch(
        self, symbols: tuple[str, ...], report_date: date
    ) -> tuple[PriceQuote, ...]: ...


class FXRateProvider(Protocol):
    """Fetch a completed currency rate at or before a report date."""

    source: str

    def fetch(self, base: Currency, quote: Currency, report_date: date) -> FxRate: ...


class AlphaVantageRequestPacer:
    """Apply one bounded interval between calls sharing an Alpha Vantage key."""

    def __init__(
        self,
        interval_seconds: float = 12,
        *,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if interval_seconds < 0:
            raise ValueError("Request interval must not be negative")
        self.interval_seconds = interval_seconds
        self._sleeper = sleeper
        self._requested = False

    def wait(self) -> None:
        if self._requested and self.interval_seconds:
            self._sleeper(self.interval_seconds)
        self._requested = True


def _decimal(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as error:
        raise ProviderDataError(f"Alpha Vantage returned invalid {label}") from error
    if parsed <= 0:
        raise ProviderDataError(f"Alpha Vantage returned non-positive {label}")
    return parsed


def _daily_series(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    series = document.get(key)
    if isinstance(series, Mapping):
        return series
    error_message = document.get("Error Message")
    if error_message:
        message = str(error_message).lower()
        if "api key" in message and ("invalid" in message or "missing" in message):
            raise ProviderAuthenticationError(
                "Alpha Vantage authentication failed: Error Message"
            )
        raise ProviderSymbolError("Alpha Vantage rejected the symbol or API call")
    for message_key in ("Note", "Information"):
        raw_message = document.get(message_key)
        if not raw_message:
            continue
        message = str(raw_message).lower()
        if any(
            phrase in message
            for phrase in ("rate limit", "call frequency", "requests per", "quota")
        ):
            raise ProviderRateLimitError(
                f"Alpha Vantage request quota unavailable: {message_key}"
            )
        if "api key" in message and any(
            phrase in message for phrase in ("invalid", "missing", "not valid")
        ):
            raise ProviderAuthenticationError(
                f"Alpha Vantage authentication failed: {message_key}"
            )
        raise ProviderInformationError(
            f"Alpha Vantage returned provider information: {message_key}"
        )
    raise ProviderDataError(f"Alpha Vantage response is missing {key}")


def _eligible_dates(series: Mapping[str, object], report_date: date) -> list[date]:
    dates: list[date] = []
    for raw_date in series:
        try:
            parsed = date.fromisoformat(str(raw_date))
        except ValueError as error:
            raise ProviderDataError("Alpha Vantage returned an invalid date") from error
        if parsed <= report_date:
            dates.append(parsed)
    return sorted(dates, reverse=True)


class AlphaVantageUSMarketDataProvider:
    """Alpha Vantage daily adjusted-close adapter for US instruments."""

    source = "Alpha Vantage TIME_SERIES_DAILY"
    endpoint = "https://www.alphavantage.co/query"

    def __init__(
        self,
        api_key: str,
        transport: JSONDocumentTransport,
        *,
        information_attempts: int = 2,
        request_interval_seconds: float = 12,
        sleeper: Callable[[float], None] = time.sleep,
        pacer: AlphaVantageRequestPacer | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Alpha Vantage API key is required")
        if information_attempts < 1:
            raise ValueError("Information attempts must be positive")
        if request_interval_seconds < 0:
            raise ValueError("Request interval must not be negative")
        self._api_key = api_key
        self._transport = transport
        self._information_attempts = information_attempts
        self._sleeper = sleeper
        self._pacer = pacer or AlphaVantageRequestPacer(
            request_interval_seconds, sleeper=sleeper
        )

    def fetch(
        self, symbols: tuple[str, ...], report_date: date
    ) -> tuple[PriceQuote, ...]:
        quotes: list[PriceQuote] = []
        distinct_symbols = sorted({item.strip().upper() for item in symbols})
        for symbol in distinct_symbols:
            try:
                series = self._fetch_series(symbol)
                quotes.append(self._quote(symbol, series, report_date))
            except ProviderDataError as error:
                _LOGGER.warning(
                    "US quote unavailable: provider=%s symbol=%s category=%s",
                    self.source,
                    symbol,
                    type(error).__name__,
                )
        return tuple(quotes)

    def _fetch_series(self, symbol: str) -> Mapping[str, object]:
        url = f"{self.endpoint}?{urlencode({'function': 'TIME_SERIES_DAILY', 'symbol': symbol, 'outputsize': 'compact', 'apikey': self._api_key})}"
        for attempt in range(1, self._information_attempts + 1):
            self._pacer.wait()
            document = self._transport.get_document(url)
            try:
                return _daily_series(document, "Time Series (Daily)")
            except ProviderInformationError:
                if attempt == self._information_attempts:
                    raise
                _LOGGER.warning(
                    "Transient US provider information response: provider=%s "
                    "symbol=%s attempt=%d/%d category=ProviderInformationError",
                    self.source,
                    symbol,
                    attempt,
                    self._information_attempts,
                )
        raise AssertionError("unreachable")

    def _quote(
        self, symbol: str, series: Mapping[str, object], report_date: date
    ) -> PriceQuote:
        eligible = _eligible_dates(series, report_date)
        if len(eligible) < 2:
            raise ProviderDataError(
                f"No completed current and previous US close for {symbol}"
            )
        current_date, previous_date = eligible[:2]
        current = series[current_date.isoformat()]
        previous = series[previous_date.isoformat()]
        if not isinstance(current, Mapping) or not isinstance(previous, Mapping):
            raise ProviderDataError("Alpha Vantage daily row is malformed")
        return PriceQuote(
            symbol=symbol,
            market=Market.US,
            trade_date=current_date,
            close_price=_decimal(current.get("4. close"), "US close"),
            previous_close=_decimal(previous.get("4. close"), "previous US close"),
            currency=Currency.USD,
            source=self.source,
            fetched_at=datetime.now(UTC),
        )


class AlphaVantageFXRateProvider:
    """Alpha Vantage daily FX adapter using the latest eligible close."""

    source = "Alpha Vantage FX_DAILY"
    endpoint = "https://www.alphavantage.co/query"

    def __init__(
        self,
        api_key: str,
        transport: JSONDocumentTransport,
        *,
        information_attempts: int = 2,
        request_interval_seconds: float = 12,
        sleeper: Callable[[float], None] = time.sleep,
        pacer: AlphaVantageRequestPacer | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Alpha Vantage API key is required")
        if information_attempts < 1:
            raise ValueError("Information attempts must be positive")
        self._api_key = api_key
        self._transport = transport
        self._information_attempts = information_attempts
        self._pacer = pacer or AlphaVantageRequestPacer(
            request_interval_seconds, sleeper=sleeper
        )

    def fetch(self, base: Currency, quote: Currency, report_date: date) -> FxRate:
        url = f"{self.endpoint}?{urlencode({'function': 'FX_DAILY', 'from_symbol': base.value, 'to_symbol': quote.value, 'outputsize': 'compact', 'apikey': self._api_key})}"
        for attempt in range(1, self._information_attempts + 1):
            self._pacer.wait()
            document = self._transport.get_document(url)
            try:
                series = _daily_series(document, "Time Series FX (Daily)")
                break
            except ProviderInformationError as error:
                if attempt == self._information_attempts:
                    raise
                _LOGGER.warning(
                    "Retryable FX provider response: provider=%s attempt=%d/%d "
                    "category=%s",
                    self.source,
                    attempt,
                    self._information_attempts,
                    type(error).__name__,
                )
        else:
            raise AssertionError("unreachable")
        eligible = _eligible_dates(series, report_date)
        if not eligible:
            raise ProviderDataError("No eligible Alpha Vantage FX rate")
        rate_date = eligible[0]
        row = series[rate_date.isoformat()]
        if not isinstance(row, Mapping):
            raise ProviderDataError("Alpha Vantage FX row is malformed")
        return FxRate(
            base_currency=base,
            quote_currency=quote,
            rate_date=rate_date,
            rate=_decimal(row.get("4. close"), "FX close"),
            source=self.source,
            fetched_at=datetime.now(UTC),
        )
