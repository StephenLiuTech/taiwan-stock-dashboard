"""Normalize exchange-specific records into PriceQuote domain objects."""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from domain import Currency, Market, PriceQuote
from market_data.exceptions import (
    ProviderDataError,
    SourceDateError,
    SuspendedSecurityError,
)

MISSING_VALUES = {"", "-", "--", "---", "－", "除權", "除息"}


class QuoteNormalizationError(ProviderDataError):
    """Raised when a requested exchange record cannot become a quote."""


def _first(record: Mapping[str, object], keys: Sequence[str]) -> object | None:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return None


def _text(value: object | None) -> str:
    return "" if value is None else str(value).strip()


def _price(value: object | None, field: str) -> Decimal:
    text = _text(value).replace(",", "")
    if text in MISSING_VALUES:
        if field == "close price":
            raise SuspendedSecurityError("Security has no closing trade")
        raise QuoteNormalizationError(f"Missing {field}")
    text = text.lstrip("+Xx")
    try:
        price = Decimal(text)
    except InvalidOperation as error:
        raise QuoteNormalizationError(f"Invalid {field}: {text}") from error
    if price < 0:
        raise QuoteNormalizationError(f"Negative {field}: {text}")
    return price


def _signed_decimal(value: object | None) -> Decimal | None:
    text = _text(value).replace(",", "")
    if text in MISSING_VALUES:
        return None
    text = text.lstrip("+Xx")
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise QuoteNormalizationError(f"Invalid price change: {text}") from error


class QuoteNormalizer:
    """Translate official exchange rows to stable domain quotes."""

    SYMBOL_KEYS = (
        "Code",
        "SecuritiesCompanyCode",
        "SecuritiesCode",
        "代號",
        "證券代號",
    )
    CLOSE_KEYS = ("ClosingPrice", "Close", "ClosePrice", "收盤價", "收盤")
    PREVIOUS_CLOSE_KEYS = (
        "PreviousClose",
        "PreviousClosePrice",
        "LastClose",
        "昨收",
    )
    CHANGE_KEYS = ("Change", "PriceChange", "漲跌價差", "漲跌")
    DATE_KEYS = ("Date", "TradeDate", "日期")

    def extract_symbol(self, record: Mapping[str, object]) -> str:
        """Extract a normalized symbol without parsing unrelated fields."""
        return _text(_first(record, self.SYMBOL_KEYS)).upper()

    def extract_trade_date(self, record: Mapping[str, object]) -> date:
        """Parse the official ROC calendar date carried by both exchanges."""
        raw = _text(_first(record, self.DATE_KEYS))
        compact = raw.replace("/", "").replace("-", "")
        if len(compact) != 7 or not compact.isdigit():
            raise SourceDateError(f"Missing or invalid official source date: {raw!r}")
        roc_year = int(compact[:3])
        try:
            return date(roc_year + 1911, int(compact[3:5]), int(compact[5:7]))
        except ValueError as error:
            raise SourceDateError(f"Invalid official source date: {raw!r}") from error

    def normalize(
        self,
        record: Mapping[str, object],
        market: Market,
        trade_date: date,
        source: str,
        fetched_at: datetime,
    ) -> PriceQuote:
        """Normalize one requested record or raise a precise data error."""
        symbol = self.extract_symbol(record)
        if not symbol:
            raise QuoteNormalizationError("Missing symbol")
        close_price = _price(_first(record, self.CLOSE_KEYS), "close price")
        previous_raw = _first(record, self.PREVIOUS_CLOSE_KEYS)
        previous_close = None
        if _text(previous_raw) not in MISSING_VALUES:
            previous_close = _price(previous_raw, "previous close")
        else:
            change = _signed_decimal(_first(record, self.CHANGE_KEYS))
            if change is not None:
                previous_close = close_price - change
                if previous_close < 0:
                    raise QuoteNormalizationError("Derived previous close is negative")
        return PriceQuote(
            symbol=symbol,
            market=market,
            trade_date=trade_date,
            close_price=close_price,
            previous_close=previous_close,
            currency=Currency.TWD,
            source=source,
            fetched_at=fetched_at,
        )
