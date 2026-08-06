"""Opt-in US quote and FX provider boundaries and Alpha Vantage adapters."""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol
from urllib.parse import urlencode

from domain import Currency, FxRate, Market, PriceQuote
from market_data.exceptions import ProviderDataError, ProviderRateLimitError
from market_data.transport import JSONDocumentTransport


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


def _decimal(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as error:
        raise ProviderDataError(f"Alpha Vantage returned invalid {label}") from error
    if parsed <= 0:
        raise ProviderDataError(f"Alpha Vantage returned non-positive {label}")
    return parsed


def _daily_series(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    for error_key in ("Note", "Information"):
        if document.get(error_key):
            raise ProviderRateLimitError(
                f"Alpha Vantage request quota unavailable: {error_key}"
            )
    if document.get("Error Message"):
        raise ProviderDataError("Alpha Vantage request failed: Error Message")
    series = document.get(key)
    if not isinstance(series, Mapping):
        raise ProviderDataError(f"Alpha Vantage response is missing {key}")
    return series


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

    def __init__(self, api_key: str, transport: JSONDocumentTransport) -> None:
        if not api_key.strip():
            raise ValueError("Alpha Vantage API key is required")
        self._api_key = api_key
        self._transport = transport

    def fetch(
        self, symbols: tuple[str, ...], report_date: date
    ) -> tuple[PriceQuote, ...]:
        quotes: list[PriceQuote] = []
        for symbol in sorted({item.strip().upper() for item in symbols}):
            url = f"{self.endpoint}?{urlencode({'function': 'TIME_SERIES_DAILY', 'symbol': symbol, 'outputsize': 'compact', 'apikey': self._api_key})}"
            document = self._transport.get_document(url)
            series = _daily_series(document, "Time Series (Daily)")
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
            quotes.append(
                PriceQuote(
                    symbol=symbol,
                    market=Market.US,
                    trade_date=current_date,
                    close_price=_decimal(current.get("4. close"), "US close"),
                    previous_close=_decimal(
                        previous.get("4. close"), "previous US close"
                    ),
                    currency=Currency.USD,
                    source=self.source,
                    fetched_at=datetime.now(UTC),
                )
            )
        return tuple(quotes)


class AlphaVantageFXRateProvider:
    """Alpha Vantage daily FX adapter using the latest eligible close."""

    source = "Alpha Vantage FX_DAILY"
    endpoint = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str, transport: JSONDocumentTransport) -> None:
        if not api_key.strip():
            raise ValueError("Alpha Vantage API key is required")
        self._api_key = api_key
        self._transport = transport

    def fetch(self, base: Currency, quote: Currency, report_date: date) -> FxRate:
        url = f"{self.endpoint}?{urlencode({'function': 'FX_DAILY', 'from_symbol': base.value, 'to_symbol': quote.value, 'outputsize': 'compact', 'apikey': self._api_key})}"
        document = self._transport.get_document(url)
        series = _daily_series(document, "Time Series FX (Daily)")
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
