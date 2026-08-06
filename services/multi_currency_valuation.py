"""Pure constant-report-date FX portfolio valuation."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from domain import (
    Currency,
    FxRate,
    Holding,
    Market,
    MultiCurrencyPortfolioValuation,
    PriceQuote,
    TranslatedHoldingValuation,
)


class MultiCurrencyValuationError(ValueError):
    """Inputs cannot produce a trustworthy reporting-currency valuation."""


class MultiCurrencyValuationEngine:
    """Translate native holding values at one report-date FX rate."""

    reporting_currency = Currency.TWD

    def valuate(
        self,
        report_date: date,
        holdings: list[Holding],
        quotes: list[PriceQuote],
        fx_rate: FxRate | None,
    ) -> MultiCurrencyPortfolioValuation:
        quote_by_key = {(item.symbol, item.market): item for item in quotes}
        values: list[TranslatedHoldingValuation] = []
        for holding in holdings:
            quote = quote_by_key.get((holding.symbol, holding.market))
            if quote is None:
                raise MultiCurrencyValuationError(
                    f"Missing quote for {holding.symbol} ({holding.market.value})"
                )
            if quote.trade_date > report_date:
                raise MultiCurrencyValuationError(
                    f"Future quote for {holding.symbol}: {quote.trade_date}"
                )
            if quote.currency != holding.currency:
                raise MultiCurrencyValuationError(
                    f"Currency mismatch for {holding.symbol}"
                )
            conversion, fx_date = self._conversion(holding, fx_rate, report_date)
            native_cost = holding.quantity * holding.average_cost
            native_market = holding.quantity * quote.close_price
            cost_twd = native_cost * conversion
            market_twd = native_market * conversion
            unrealized_twd = (
                (quote.close_price - holding.average_cost)
                * holding.quantity
                * conversion
            )
            daily_twd = (
                (quote.close_price - quote.previous_close)
                * holding.quantity
                * conversion
                if quote.previous_close is not None
                else Decimal("0")
            )
            values.append(
                TranslatedHoldingValuation(
                    holding.market,
                    holding.symbol,
                    holding.name,
                    holding.currency,
                    holding.quantity,
                    holding.average_cost,
                    quote.close_price,
                    quote.trade_date,
                    conversion,
                    fx_date,
                    self.reporting_currency,
                    cost_twd,
                    market_twd,
                    daily_twd,
                    (
                        (quote.close_price - quote.previous_close)
                        / quote.previous_close
                        if quote.previous_close
                        else Decimal("0")
                    ),
                    unrealized_twd,
                    unrealized_twd / cost_twd if cost_twd else Decimal("0"),
                )
            )
        total_cost = sum((item.cost_basis_twd for item in values), Decimal("0"))
        total_market = sum((item.market_value_twd for item in values), Decimal("0"))
        total_unrealized = sum(
            (item.unrealized_pnl_twd for item in values), Decimal("0")
        )
        weighted = tuple(
            replace(
                item,
                portfolio_weight=(
                    item.market_value_twd / total_market
                    if total_market
                    else Decimal("0")
                ),
            )
            for item in values
        )
        return MultiCurrencyPortfolioValuation(
            report_date,
            self.reporting_currency,
            weighted,
            total_cost,
            total_market,
            total_unrealized,
            total_unrealized / total_cost if total_cost else Decimal("0"),
            sum(
                (
                    item.market_value_twd
                    for item in weighted
                    if item.market is not Market.US
                ),
                Decimal("0"),
            ),
            sum(
                (
                    item.market_value_twd
                    for item in weighted
                    if item.market is Market.US
                ),
                Decimal("0"),
            ),
        )

    @staticmethod
    def _conversion(
        holding: Holding, fx_rate: FxRate | None, report_date: date
    ) -> tuple[Decimal, date | None]:
        if holding.currency is Currency.TWD:
            return Decimal("1"), None
        if holding.market is not Market.US or holding.currency is not Currency.USD:
            raise MultiCurrencyValuationError(
                f"Unsupported market/currency pair: {holding.market.value}/{holding.currency.value}"
            )
        if fx_rate is None:
            raise MultiCurrencyValuationError("USD/TWD FX rate is unavailable")
        if (
            fx_rate.base_currency is not Currency.USD
            or fx_rate.quote_currency is not Currency.TWD
            or fx_rate.rate_date > report_date
        ):
            raise MultiCurrencyValuationError("Invalid USD/TWD FX rate provenance")
        return fx_rate.rate, fx_rate.rate_date
