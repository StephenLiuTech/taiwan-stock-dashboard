"""Multi-market domain, provider, persistence, and valuation contracts."""

import sqlite3
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from config.settings import Settings
from domain import Currency, FxRate, Holding, Market, PriceQuote
from market_data.exceptions import ProviderDataError, ProviderRateLimitError
from market_data.global_engine import GlobalMarketDataEngine
from market_data.providers import (
    AlphaVantageFXRateProvider,
    AlphaVantageUSMarketDataProvider,
)
from pams.application.send_daily_report import DailyEmailPosition, DailyEmailReport
from pams.application.valuate_portfolio import ValuatePortfolioUseCase
from pams.delivery.rendering import DailyEmailReportRenderer
from repositories.sqlite import (
    SQLiteFxRateRepository,
    SQLiteHoldingRepository,
    SQLitePriceQuoteRepository,
)
from services.multi_currency_valuation import (
    MultiCurrencyValuationEngine,
    MultiCurrencyValuationError,
)


class DocumentTransport:
    def __init__(self, document: dict[str, object]) -> None:
        self.document = document
        self.urls: list[str] = []

    def get_document(self, url: str) -> dict[str, object]:
        self.urls.append(url)
        return self.document


def holding(symbol: str, market: Market, currency: Currency) -> Holding:
    return Holding(
        id=f"{market.value}-{symbol}",
        symbol=symbol,
        name=symbol,
        market=market,
        currency=currency,
        quantity=Decimal("10"),
        average_cost=Decimal("100"),
    )


def quote(
    symbol: str,
    market: Market,
    currency: Currency,
    close: str,
    previous: str,
    quote_date: date,
) -> PriceQuote:
    return PriceQuote(
        symbol=symbol,
        market=market,
        trade_date=quote_date,
        close_price=Decimal(close),
        previous_close=Decimal(previous),
        currency=currency,
        source="fixture",
    )


def test_same_symbol_can_exist_in_different_markets(
    connection: sqlite3.Connection,
) -> None:
    repository = SQLiteHoldingRepository(connection)
    repository.upsert(holding("ABC", Market.TWSE, Currency.TWD))
    repository.upsert(holding("ABC", Market.US, Currency.USD))
    assert {(item.symbol, item.market) for item in repository.list_all()} == {
        ("ABC", Market.TWSE),
        ("ABC", Market.US),
    }


def test_fx_repository_never_returns_future_rate(
    connection: sqlite3.Connection,
) -> None:
    repository = SQLiteFxRateRepository(connection)
    for rate_date, value in ((date(2026, 8, 4), "30"), (date(2026, 8, 6), "31")):
        repository.upsert(
            FxRate(
                base_currency=Currency.USD,
                quote_currency=Currency.TWD,
                rate_date=rate_date,
                rate=Decimal(value),
                source="fixture",
            )
        )
    selected = repository.get_latest_on_or_before("USD", "TWD", date(2026, 8, 5))
    assert selected is not None
    assert selected.rate_date == date(2026, 8, 4)
    assert selected.rate == Decimal("30")


@pytest.mark.parametrize("value", ["0", "-1"])
def test_fx_rate_rejects_non_positive_values(value: str) -> None:
    with pytest.raises(ValueError):
        FxRate(
            base_currency=Currency.USD,
            quote_currency=Currency.TWD,
            rate_date=date(2026, 8, 5),
            rate=Decimal(value),
            source="fixture",
        )


def test_constant_report_date_fx_translation_is_exact() -> None:
    report_date = date(2026, 8, 5)
    fx = FxRate(
        base_currency=Currency.USD,
        quote_currency=Currency.TWD,
        rate_date=report_date,
        rate=Decimal("31.42"),
        source="fixture",
    )
    result = MultiCurrencyValuationEngine().valuate(
        report_date,
        [
            holding("2330", Market.TWSE, Currency.TWD),
            holding("MU", Market.US, Currency.USD),
        ],
        [
            quote("2330", Market.TWSE, Currency.TWD, "110", "108", report_date),
            quote("MU", Market.US, Currency.USD, "158.30", "150", date(2026, 8, 4)),
        ],
        fx,
    )
    us = next(item for item in result.holdings if item.market is Market.US)
    assert us.market_value_twd == Decimal("49737.8600")
    assert us.daily_pnl_twd == Decimal("2607.8600")
    assert us.unrealized_pnl_twd == Decimal("18317.8600")
    assert us.quote_date == date(2026, 8, 4)
    assert us.fx_rate_date == report_date


def test_missing_fx_is_not_silently_one() -> None:
    with pytest.raises(MultiCurrencyValuationError, match="unavailable"):
        MultiCurrencyValuationEngine().valuate(
            date(2026, 8, 5),
            [holding("MU", Market.US, Currency.USD)],
            [quote("MU", Market.US, Currency.USD, "101", "100", date(2026, 8, 4))],
            None,
        )


def test_application_valuation_translates_us_holding_to_twd(
    connection: sqlite3.Connection,
) -> None:
    holdings = SQLiteHoldingRepository(connection)
    quotes = SQLitePriceQuoteRepository(connection)
    rates = SQLiteFxRateRepository(connection)
    holdings.upsert(holding("MU", Market.US, Currency.USD))
    quotes.upsert_many(
        [
            quote(
                "MU",
                Market.US,
                Currency.USD,
                "110",
                "108",
                date(2026, 8, 5),
            )
        ]
    )
    rates.upsert(
        FxRate(
            base_currency=Currency.USD,
            quote_currency=Currency.TWD,
            rate_date=date(2026, 8, 5),
            rate=Decimal("31.42"),
            source="fixture",
        )
    )
    result = ValuatePortfolioUseCase(holdings, quotes, fx_rates=rates).execute()
    assert result.total_market_value == Decimal("34562.00")
    assert result.total_cost == Decimal("31420.00")


class CompletenessQuoteRepository:
    def __init__(self, quotes: dict[str, PriceQuote]) -> None:
        self.quotes = quotes

    def get_latest_on_or_before(
        self, symbol: str, market: str, trade_date: date
    ) -> PriceQuote | None:
        del market
        value = self.quotes.get(symbol)
        return value if value and value.trade_date <= trade_date else None


class CompletenessFxRepository:
    def __init__(self, value: FxRate | None) -> None:
        self.value = value

    def get_latest_on_or_before(
        self, base: str, quote_currency: str, trade_date: date
    ) -> FxRate | None:
        del base, quote_currency
        return self.value if self.value and self.value.rate_date <= trade_date else None


def completeness_engine(
    holdings: list[Holding],
    quotes: dict[str, PriceQuote],
    fx: FxRate | None,
    covered_ids: set[str],
    *,
    providers: bool = True,
) -> GlobalMarketDataEngine:
    positions = [SimpleNamespace(holding_id=value) for value in covered_ids]
    unit_of_work = SimpleNamespace(
        price_quotes=CompletenessQuoteRepository(quotes),
        position_snapshots=SimpleNamespace(list_by_date=lambda _: positions),
    )
    return GlobalMarketDataEngine(
        SimpleNamespace(),
        SimpleNamespace(list_all=lambda: holdings),
        SimpleNamespace(),
        unit_of_work,
        CompletenessFxRepository(fx),
        SimpleNamespace() if providers else None,
        SimpleNamespace() if providers else None,
    )


def test_existing_taiwan_only_snapshot_remains_complete_no_op() -> None:
    tw = holding("2330", Market.TWSE, Currency.TWD)
    engine = completeness_engine([tw], {}, None, {tw.id})
    assert engine.requires_enrichment(date(2026, 8, 5)) is False


@pytest.mark.parametrize("missing_symbol", ["MU", "DRAM"])
def test_partial_or_missing_us_quote_requires_enrichment(missing_symbol: str) -> None:
    report_date = date(2026, 8, 5)
    holdings = [
        holding("MU", Market.US, Currency.USD),
        holding("DRAM", Market.US, Currency.USD),
    ]
    quotes = {
        item.symbol: quote(
            item.symbol, Market.US, Currency.USD, "100", "99", report_date
        )
        for item in holdings
        if item.symbol != missing_symbol
    }
    fx = FxRate(
        base_currency=Currency.USD,
        quote_currency=Currency.TWD,
        rate_date=report_date,
        rate=Decimal("31.42"),
        source="fixture",
    )
    engine = completeness_engine(holdings, quotes, fx, {item.id for item in holdings})
    assert engine.requires_enrichment(report_date) is True


def test_complete_global_snapshot_is_true_no_op() -> None:
    report_date = date(2026, 8, 5)
    us = holding("MU", Market.US, Currency.USD)
    fx = FxRate(
        base_currency=Currency.USD,
        quote_currency=Currency.TWD,
        rate_date=report_date,
        rate=Decimal("31.42"),
        source="fixture",
    )
    engine = completeness_engine(
        [us],
        {"MU": quote("MU", Market.US, Currency.USD, "100", "99", report_date)},
        fx,
        {us.id},
    )
    assert engine.requires_enrichment(report_date) is False


def test_missing_fx_or_position_coverage_requires_enrichment() -> None:
    report_date = date(2026, 8, 5)
    us = holding("MU", Market.US, Currency.USD)
    quotes = {"MU": quote("MU", Market.US, Currency.USD, "100", "99", report_date)}
    assert completeness_engine([us], quotes, None, {us.id}).requires_enrichment(
        report_date
    )
    fx = FxRate(
        base_currency=Currency.USD,
        quote_currency=Currency.TWD,
        rate_date=report_date,
        rate=Decimal("31.42"),
        source="fixture",
    )
    assert completeness_engine([us], quotes, fx, set()).requires_enrichment(report_date)


def test_us_provider_uses_latest_two_completed_sessions() -> None:
    transport = DocumentTransport(
        {
            "Time Series (Daily)": {
                "2026-08-06": {"4. close": "160"},
                "2026-08-04": {"4. close": "158.30"},
                "2026-08-03": {"4. close": "150"},
            }
        }
    )
    result = AlphaVantageUSMarketDataProvider("secret", transport).fetch(
        ("mu",), date(2026, 8, 5)
    )
    assert result[0].symbol == "MU"
    assert result[0].trade_date == date(2026, 8, 4)
    assert result[0].previous_close == Decimal("150")


def test_fx_provider_uses_latest_eligible_rate() -> None:
    transport = DocumentTransport(
        {
            "Time Series FX (Daily)": {
                "2026-08-06": {"4. close": "31.5"},
                "2026-08-04": {"4. close": "31.42"},
            }
        }
    )
    result = AlphaVantageFXRateProvider("secret", transport).fetch(
        Currency.USD, Currency.TWD, date(2026, 8, 5)
    )
    assert result.rate_date == date(2026, 8, 4)
    assert result.rate == Decimal("31.42")


def test_provider_semantic_error_is_typed() -> None:
    provider = AlphaVantageUSMarketDataProvider(
        "secret", DocumentTransport({"Information": "limit"})
    )
    with pytest.raises(ProviderRateLimitError):
        provider.fetch(("MU",), date(2026, 8, 5))


def test_us_provider_fetches_each_distinct_symbol_once() -> None:
    transport = DocumentTransport(
        {
            "Time Series (Daily)": {
                "2026-08-04": {"4. close": "100"},
                "2026-08-03": {"4. close": "99"},
            }
        }
    )
    AlphaVantageUSMarketDataProvider("secret", transport).fetch(
        ("MU", "mu", "DRAM"), date(2026, 8, 5)
    )
    assert len(transport.urls) == 2
    assert sum("symbol=MU" in url for url in transport.urls) == 1
    assert sum("symbol=DRAM" in url for url in transport.urls) == 1


def test_optional_providers_are_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PAMS_US_MARKET_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("PAMS_FX_PROVIDER", raising=False)
    settings = Settings(_env_file=None)
    assert settings.us_market_data_provider == "disabled"
    assert settings.fx_provider == "disabled"


def test_alpha_vantage_missing_key_is_clear_and_secret_is_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(ValueError, match="API key is required"):
        AlphaVantageUSMarketDataProvider("", DocumentTransport({}))
    secret = "never-log-this-key"
    provider = AlphaVantageUSMarketDataProvider(
        secret, DocumentTransport({"Information": "rate limit"})
    )
    with pytest.raises(ProviderDataError, match="Information"):
        provider.fetch(("MU",), date(2026, 8, 5))
    assert secret not in caplog.text


def test_combined_report_preserves_native_prices_and_twd_totals() -> None:
    report_date = date(2026, 8, 5)
    common = {
        "quantity": Decimal("10"),
        "daily_return": Decimal("0.01"),
        "market_value": Decimal("1100"),
        "unrealized_pnl": Decimal("100"),
        "unrealized_return": Decimal("0.1"),
        "portfolio_weight": Decimal("0.02"),
        "daily_profit_loss": Decimal("10"),
        "daily_profit_loss_percentage": Decimal("0.01"),
        "daily_profit_loss_share": Decimal("0.1"),
    }
    positions = (
        DailyEmailPosition(
            symbol="2330",
            name="TSMC",
            average_cost=Decimal("100"),
            close_price=Decimal("110"),
            market=Market.TWSE,
            native_currency=Currency.TWD,
            quote_date=report_date,
            **common,
        ),
        DailyEmailPosition(
            symbol="MU",
            name="Micron",
            average_cost=Decimal("150.25"),
            close_price=Decimal("158.30"),
            market=Market.US,
            native_currency=Currency.USD,
            quote_date=date(2026, 8, 4),
            fx_rate=Decimal("31.42"),
            fx_rate_date=report_date,
            **common,
        ),
    )
    report = DailyEmailReport(
        report_date,
        report_date,
        Decimal("2200"),
        Decimal("2000"),
        Decimal("200"),
        Decimal("0.1"),
        Decimal("0"),
        Decimal("2200"),
        Decimal("0"),
        Decimal("20"),
        Decimal("0.01"),
        (),
        positions,
    )
    rendered = DailyEmailReportRenderer().render(report)
    assert "TWSE" in rendered.html and "US" in rendered.html
    assert ">Currency</th>" in rendered.html
    assert ">Quote Date</th>" in rendered.html
    assert ">TWD</td>" in rendered.html
    assert ">USD</td>" in rendered.html
    assert "NT$100" in rendered.html
    assert "US$150.25" in rendered.html
    assert "US$158.3" in rendered.html
    assert ">31.42</td>" in rendered.html
    assert "2026-08-04" in rendered.html
    assert "US Holdings" in rendered.plain_text
    holdings_html = rendered.html.split(">Holdings</h2>", 1)[1].split("</table>", 1)[0]
    assert "white-space:nowrap" in holdings_html
