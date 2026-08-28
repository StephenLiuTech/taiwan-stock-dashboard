"""Application orchestration for bounded historical FX persistence."""

from dataclasses import dataclass
from datetime import date

from domain import Currency, FxRate
from market_data.providers import FXRateProvider
from repositories import FxRateRepository, MarketDataUnitOfWork


@dataclass(frozen=True)
class FxBackfillResult:
    """Immutable preview or apply result for one bounded FX range."""

    base_currency: Currency
    quote_currency: Currency
    start_date: date
    end_date: date
    observations: tuple[FxRate, ...]
    existing_dates: tuple[date, ...]
    missing_dates: tuple[date, ...]
    inserted: int
    applied: bool
    database: str


class BackfillFxRatesUseCase:
    """Fetch authentic observations and insert only previously absent dates."""

    def __init__(
        self,
        provider: FXRateProvider,
        repository: FxRateRepository,
        unit_of_work: MarketDataUnitOfWork,
        database: str,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._unit_of_work = unit_of_work
        self._database = database

    def execute(
        self,
        base_currency: Currency,
        quote_currency: Currency,
        start_date: date,
        end_date: date,
        *,
        apply: bool,
    ) -> FxBackfillResult:
        if start_date > end_date:
            raise ValueError("FX backfill start date follows end date")
        if base_currency is quote_currency:
            raise ValueError("FX backfill requires two different currencies")

        observations = self._provider.fetch_between(
            base_currency, quote_currency, start_date, end_date
        )
        existing = self._repository.list_between(
            base_currency.value,
            quote_currency.value,
            start_date,
            end_date,
        )
        existing_dates = {item.rate_date for item in existing}
        missing = tuple(
            item for item in observations if item.rate_date not in existing_dates
        )
        inserted = 0
        if apply:
            with self._unit_of_work.transaction():
                current_dates = {
                    item.rate_date
                    for item in self._unit_of_work.fx_rates.list_between(
                        base_currency.value,
                        quote_currency.value,
                        start_date,
                        end_date,
                    )
                }
                for rate in missing:
                    if rate.rate_date in current_dates:
                        continue
                    if self._unit_of_work.fx_rates.insert_if_absent(rate):
                        inserted += 1
                        current_dates.add(rate.rate_date)

        return FxBackfillResult(
            base_currency,
            quote_currency,
            start_date,
            end_date,
            observations,
            tuple(sorted(existing_dates)),
            tuple(item.rate_date for item in missing),
            inserted,
            apply,
            self._database,
        )
