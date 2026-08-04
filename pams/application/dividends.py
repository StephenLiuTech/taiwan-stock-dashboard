"""Application workflows for official normalized dividend events."""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from domain import DividendEvent, Market
from market_data import ProviderDataError
from market_data.dividend_payments import (
    OfficialDividendPaymentProvider,
    RawDividendPayment,
)
from market_data.dividends import OfficialDividendProvider, RawDividendEvent
from repositories import DividendEventRepository, HoldingRepository


class DividendEventError(ValueError):
    """A dividend command cannot complete with valid official data."""


@dataclass(frozen=True)
class DividendUpdateResult:
    fetched: int
    upserted: int
    payment_dates_enriched: int = 0
    payment_enrichment_available: bool = True


LOGGER = logging.getLogger(__name__)

_UNAVAILABLE_DIVIDEND_AMOUNTS = frozenset(
    {"", "-", "--", "N/A", "尚未公告", "尚未確定"}
)


def _roc_date(value: str) -> date:
    cleaned = (
        value.strip()
        .replace("/", "")
        .replace("年", "")
        .replace("月", "")
        .replace("日", "")
    )
    if len(cleaned) != 7 or not cleaned.isdigit():
        raise ProviderDataError(f"Malformed official dividend date: {value!r}")
    try:
        return date(int(cleaned[:3]) + 1911, int(cleaned[3:5]), int(cleaned[5:]))
    except ValueError as error:
        raise ProviderDataError(
            f"Malformed official dividend date: {value!r}"
        ) from error


def _optional_decimal(value: str) -> Decimal | None:
    cleaned = value.strip().replace(",", "")
    if cleaned in _UNAVAILABLE_DIVIDEND_AMOUNTS:
        return None
    try:
        result = Decimal(cleaned)
    except InvalidOperation as error:
        raise ProviderDataError(
            f"Malformed official dividend amount: {value!r}"
        ) from error
    if result < 0:
        raise ProviderDataError(f"Malformed official dividend amount: {value!r}")
    return result


class NormalizeDividendEventsUseCase:
    """Validate raw official rows and assign deterministic distribution keys."""

    def execute(self, rows: tuple[RawDividendEvent, ...]) -> tuple[DividendEvent, ...]:
        normalized: dict[str, DividendEvent] = {}
        fetched_at = datetime.now(UTC)
        for row in rows:
            symbol = row.symbol.strip().upper()
            if not symbol or not row.name.strip():
                raise ProviderDataError(
                    "Official dividend row is missing symbol or name"
                )
            ex_date = _roc_date(row.ex_dividend_date)
            cash = _optional_decimal(row.cash_dividend_per_share)
            stock = _optional_decimal(row.stock_dividend_per_share)
            event_type = (
                "ex-right-dividend"
                if cash and stock
                else ("ex-dividend" if cash else "ex-right")
            )
            identity = "|".join(
                (
                    row.market.value,
                    symbol,
                    ex_date.isoformat(),
                    event_type,
                )
            )
            event_id = sha256(identity.encode("utf-8")).hexdigest()
            normalized[event_id] = DividendEvent(
                source_event_id=event_id,
                symbol=symbol,
                market=row.market,
                name=row.name.strip(),
                dividend_year=ex_date.year,
                ex_dividend_date=ex_date,
                cash_dividend_per_share=cash,
                stock_dividend_per_share=stock,
                source=row.source,
                source_updated_at=row.source_updated_at,
                fetched_at=fetched_at,
            )
        return tuple(
            sorted(
                normalized.values(),
                key=lambda item: (
                    item.ex_dividend_date,
                    item.symbol,
                    item.source_event_id,
                ),
            )
        )


class DividendEventUseCase:
    """Fetch, persist, and inspect current-portfolio official events."""

    def __init__(
        self,
        provider: OfficialDividendProvider,
        repository: DividendEventRepository,
        holdings: HoldingRepository,
        payment_provider: OfficialDividendPaymentProvider | None = None,
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.holdings = holdings
        self.payment_provider = payment_provider
        self.normalizer = NormalizeDividendEventsUseCase()

    def update(
        self,
        *,
        market: Market | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> DividendUpdateResult:
        if start_date is not None and end_date is not None and start_date > end_date:
            raise DividendEventError("Dividend date range start must not exceed end")
        raw = self.provider.fetch(market, start_date, end_date)
        active = {
            (item.symbol, item.market)
            for item in self.holdings.list_all()
            if item.quantity > 0
        }
        events = [
            item
            for item in self.normalizer.execute(raw)
            if (item.symbol, item.market) in active
            and (start_date is None or item.ex_dividend_date >= start_date)
            and (end_date is None or item.ex_dividend_date <= end_date)
        ]
        existing = {
            item.source_event_id: item for item in self.repository.list_filtered()
        }
        events = [
            (
                item.model_copy(
                    update={
                        "payment_date": existing[item.source_event_id].payment_date,
                        "source": existing[item.source_event_id].source,
                    }
                )
                if item.source_event_id in existing
                and existing[item.source_event_id].payment_date is not None
                else item
            )
            for item in events
        ]
        enriched = 0
        payment_available = True
        if self.payment_provider is not None and events:
            period_start = start_date or date(
                min(item.dividend_year for item in events), 1, 1
            )
            period_end = end_date or date(
                max(item.dividend_year for item in events), 12, 31
            )
            symbols_by_market = {
                value: tuple(
                    sorted(
                        symbol
                        for symbol, market_value in active
                        if market_value is value
                    )
                )
                for value in Market
            }
            try:
                payments = self.payment_provider.fetch(
                    symbols_by_market, period_start, period_end
                )
                events, enriched = self._enrich_payment_dates(events, payments)
            except Exception as error:
                payment_available = False
                LOGGER.warning(
                    "official dividend payment-date enrichment unavailable",
                    extra={
                        "provider": "MOPS t108sb27",
                        "error_type": type(error).__name__,
                    },
                )
        written = self.repository.upsert_many(events)
        return DividendUpdateResult(len(raw), written, enriched, payment_available)

    @staticmethod
    def _enrich_payment_dates(
        events: list[DividendEvent], payments: tuple[RawDividendPayment, ...]
    ) -> tuple[list[DividendEvent], int]:
        by_key: dict[tuple[Market, str, date], list[int]] = {}
        for index, event in enumerate(events):
            key = (event.market, event.symbol, event.ex_dividend_date)
            by_key.setdefault(key, []).append(index)
        values = list(events)
        enriched = 0
        for payment in payments:
            symbol = payment.symbol.strip().upper()
            ex_date = _roc_date(payment.ex_dividend_date)
            payment_date = _roc_date(payment.payment_date)
            matches = by_key.get((payment.market, symbol, ex_date), [])
            if len(matches) != 1:
                continue
            index = matches[0]
            if values[index].cash_dividend_per_share is None:
                continue
            values[index] = values[index].model_copy(
                update={
                    "payment_date": payment_date,
                    "source": (
                        values[index].source
                        if payment.source in values[index].source
                        else f"{values[index].source} + {payment.source}"
                    ),
                }
            )
            enriched += 1
        return values, enriched

    def list(
        self,
        *,
        market: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[DividendEvent, ...]:
        if start_date is not None and end_date is not None and start_date > end_date:
            raise DividendEventError("Dividend date range start must not exceed end")
        return tuple(
            self.repository.list_filtered(
                market=market, start_date=start_date, end_date=end_date
            )
        )

    def show(
        self, symbol: str, *, year: int | None = None
    ) -> tuple[DividendEvent, ...]:
        values = self.repository.list_filtered(symbol=symbol.strip().upper(), year=year)
        if not values:
            raise DividendEventError(
                f"No persisted dividend events for {symbol.strip().upper()}"
            )
        return tuple(values)
