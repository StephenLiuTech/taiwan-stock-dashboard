"""Official dividend normalization, persistence, and rendering tests."""

# ruff: noqa: ANN001, ANN003, ANN201, ANN202, ANN204

from datetime import date
from decimal import Decimal

import pytest

from database import initialize_database
from database.schema import initialize_schema
from domain import (
    Currency,
    DailyReportSections,
    DividendEvent,
    Holding,
    HoldingType,
    Market,
    Transaction,
    TransactionType,
)
from market_data import ProviderDataError
from market_data.dividend_payments import (
    MOPSDividendPaymentProvider,
    RawDividendPayment,
)
from market_data.dividends import (
    RawDividendEvent,
    TPExDividendProvider,
    TPExHistoricalDividendProvider,
    TWSEDividendProvider,
    TWSEHistoricalDividendProvider,
)
from pams.application.dividends import (
    DividendEventUseCase,
    NormalizeDividendEventsUseCase,
)
from pams.application.report_sections import BuildReportSectionsUseCase
from pams.cli import _format_dividends, main
from pams.delivery.sections import DailyReportSectionRenderer
from repositories import SQLiteDividendEventRepository
from services import ReportSectionService


class Records:
    def __init__(self, rows):
        self.rows = rows

    def get_records(self, url):
        del url
        return self.rows


class Document:
    def __init__(self, payload):
        self.payload = payload
        self.url = ""

    def get_document(self, url):
        self.url = url
        return self.payload


def raw(symbol="2330", market=Market.TWSE, date_value="1150727", cash="5", stock=""):
    return RawDividendEvent(symbol, market, "Name", date_value, cash, stock, "official")


def test_twse_tpex_and_etf_provider_shapes() -> None:
    twse = TWSEDividendProvider(
        Records(
            [
                {
                    "Date": "1150727",
                    "Code": "0050",
                    "Name": "ETF",
                    "CashDividend": "0.36",
                    "StockDividendRatio": "",
                }
            ]
        )
    ).fetch()[0]
    tpex = TPExDividendProvider(
        Records(
            [
                {
                    "ExRrightsExDividendDate": "1150728",
                    "SecuritiesCompanyCode": "6488",
                    "CompanyName": "Company",
                    "CashDividend": "2.5",
                    "StockDividendRatio": "0",
                }
            ]
        )
    ).fetch()[0]
    assert (twse.symbol, twse.market, twse.cash_dividend_per_share) == (
        "0050",
        Market.TWSE,
        "0.36",
    )
    assert (tpex.symbol, tpex.market) == ("6488", Market.TPEX)


def test_historical_providers_preserve_leading_zero_and_market_mapping() -> None:
    twse_document = Document(
        {
            "fields": ["資料日期", "股票代號", "股票名稱", "權值+息值", "權/息"],
            "data": [["115年07月21日", "0050", "元大台灣50", "0.600000", "息"]],
        }
    )
    tpex_document = Document(
        {
            "tables": [
                {
                    "fields": [
                        "除權息日期",
                        "代號",
                        "名稱",
                        "權/息",
                        "現金股利",
                        "每仟股無償配股",
                    ],
                    "data": [["115/07/08", "3293", "鈊象  ", "除息", "36", "0"]],
                }
            ]
        }
    )
    twse = TWSEHistoricalDividendProvider(twse_document).fetch()[0]
    tpex = TPExHistoricalDividendProvider(tpex_document).fetch()[0]
    assert (twse.symbol, twse.market, twse.ex_dividend_date) == (
        "0050",
        Market.TWSE,
        "115年07月21日",
    )
    assert (tpex.symbol, tpex.market, tpex.cash_dividend_per_share) == (
        "3293",
        Market.TPEX,
        "36",
    )
    assert "startDate=" in twse_document.url
    assert "startDate=" in tpex_document.url


def test_update_filters_by_normalized_symbol_and_returns_repository_write_count() -> (
    None
):
    class Provider:
        def fetch(self, market=None, start_date=None, end_date=None):
            del market, start_date, end_date
            return (
                raw(symbol=" 0050 ", market=Market.TWSE),
                raw(symbol="3293", market=Market.TPEX),
                raw(symbol="2330", market=Market.TPEX),
            )

    class Holdings:
        def list_all(self):
            return [
                Holding(
                    id="0050-TWSE",
                    symbol="0050",
                    name="ETF",
                    market=Market.TWSE,
                    currency=Currency.TWD,
                    quantity=Decimal("1"),
                    average_cost=Decimal("1"),
                    holding_type=HoldingType.ETF,
                ),
                Holding(
                    id="3293-TPEX",
                    symbol="3293",
                    name="OTC",
                    market=Market.TPEX,
                    currency=Currency.TWD,
                    quantity=Decimal("1"),
                    average_cost=Decimal("1"),
                    holding_type=HoldingType.STOCK,
                ),
            ]

    class Repository:
        def __init__(self):
            self.events = []

        def upsert_many(self, events):
            self.events = events
            return len(events)

        def list_filtered(self, **kwargs):
            del kwargs
            return []

    repository = Repository()
    result = DividendEventUseCase(Provider(), repository, Holdings()).update()
    assert result.fetched == 3
    assert result.upserted == 2
    assert {(item.symbol, item.market) for item in repository.events} == {
        ("0050", Market.TWSE),
        ("3293", Market.TPEX),
    }


def test_normalization_missing_payment_duplicate_and_multiple_distributions() -> None:
    values = NormalizeDividendEventsUseCase().execute(
        (raw(), raw(), raw(date_value="1150927", cash="3"))
    )
    assert len(values) == 2
    assert values[0].payment_date is None
    assert values[0].cash_dividend_per_share == Decimal("5")
    assert values[0].dividend_year == 2026


@pytest.mark.parametrize("bad", ["bad", "1151332"])
def test_malformed_date_is_rejected(bad: str) -> None:
    with pytest.raises(ProviderDataError, match="date"):
        NormalizeDividendEventsUseCase().execute((raw(date_value=bad),))


def test_malformed_amount_is_rejected() -> None:
    with pytest.raises(ProviderDataError, match="amount"):
        NormalizeDividendEventsUseCase().execute((raw(cash="oops"),))


@pytest.mark.parametrize(
    "unavailable_value",
    ["尚未公告", "尚未確定", "-", "--", "", "N/A"],
)
def test_known_unavailable_dividend_amount_is_normalized_to_none(
    unavailable_value: str,
) -> None:
    values = NormalizeDividendEventsUseCase().execute(
        (
            raw(symbol="2330", cash=unavailable_value),
            raw(symbol="0050", cash="3.25"),
        )
    )

    by_symbol = {item.symbol: item for item in values}
    assert by_symbol["2330"].cash_dividend_per_share is None
    assert by_symbol["0050"].cash_dividend_per_share == Decimal("3.25")


def event(payment=None) -> DividendEvent:
    return (
        NormalizeDividendEventsUseCase()
        .execute((raw(),))[0]
        .model_copy(update={"payment_date": payment})
    )


def test_sqlite_upsert_uniqueness_and_later_payment_date(connection) -> None:
    initialize_schema(connection)
    repository = SQLiteDividendEventRepository(connection)
    repository.upsert_many([event()])
    repository.upsert_many([event(date(2026, 8, 10))])
    values = repository.list_filtered(symbol="2330")
    assert len(values) == 1
    assert values[0].payment_date == date(2026, 8, 10)


def test_dividend_estimate_decimal_and_renderer() -> None:
    section = ReportSectionService.dividends(
        date(2026, 7, 1),
        [event()],
        {("2330", date(2026, 7, 27)): Decimal("10.5")},
    )
    assert section.items[0].estimated_cash_dividend == Decimal("52.5")
    rendered = DailyReportSectionRenderer()
    html = rendered.dividend_calendar_html(
        DailyReportSections(dividend_calendar=section)
    )
    text = rendered.dividend_calendar_text(
        DailyReportSections(dividend_calendar=section)
    )
    assert "NT$52" in html
    assert "N/A" in html
    assert "Ex-Date" in html
    assert "Actual Cash Received" in html
    assert "Estimated Annual Dividend" in html
    assert "Upcoming Ex-Date" in html
    assert "Dividend Calendar" in text
    assert "Dividend Estimate Summary" in text
    assert "Actual Cash Received means" in text
    assert "Estimated Annual Dividend | NT$52" in text


def test_mops_payment_provider_parses_official_two_row_header() -> None:
    html = """
    <table><tr><th>公司代號</th><th>公司名稱</th></tr>
    <tr><th>盈餘分配之股東現金股利(元/股)</th>
    <th>法定盈餘公積、資本公積發放之現金(元/股)</th>
    <th>除息交易日</th><th>現金股利發放日</th></tr>
    <tr><td>2330</td><td>台積電</td><td>114年第4季</td><td>115/06/17</td>
    <td></td><td></td><td></td><td>6.00003573</td><td></td><td></td>
    <td>115/06/11</td><td>115/07/09</td></tr></table>
    """

    class Forms:
        def post_form(self, url, fields):
            assert "ajax_t108sb27" in url
            assert fields["TYPEK"] == "sii"
            return html

    values = MOPSDividendPaymentProvider(Market.TWSE, Forms()).fetch(
        ("2330",), date(2026, 1, 1), date(2026, 12, 31)
    )
    assert values == (
        RawDividendPayment(
            "2330", Market.TWSE, "台積電", "115/06/11", "115/07/09", "6.00003573"
        ),
    )


def test_mops_payment_provider_missing_date_is_not_guessed() -> None:
    html = """
    <table><tr><th>公司代號</th><th>公司名稱</th></tr>
    <tr><th>盈餘分配之股東現金股利(元/股)</th>
    <th>法定盈餘公積、資本公積發放之現金(元/股)</th>
    <th>除息交易日</th><th>現金股利發放日</th></tr>
    <tr><td>2330</td><td>台積電</td><td>period</td><td>record</td>
    <td></td><td></td><td></td><td>6</td><td></td><td></td>
    <td>115/06/11</td><td></td></tr></table>
    """

    class Forms:
        def post_form(self, url, fields):
            del url, fields
            return html

    assert (
        MOPSDividendPaymentProvider(Market.TWSE, Forms()).fetch(
            ("2330",), date(2026, 1, 1), date(2026, 12, 31)
        )
        == ()
    )


def test_malformed_complete_payment_dataset_is_rejected() -> None:
    class Forms:
        def post_form(self, url, fields):
            del url, fields
            return "<table><tr><th>公司代號</th><th>公司名稱</th></tr><tr><th>wrong</th></tr></table>"

    with pytest.raises(ProviderDataError, match="fields"):
        MOPSDividendPaymentProvider(Market.TWSE, Forms()).fetch(
            ("2330",), date(2026, 1, 1), date(2026, 12, 31)
        )


def test_payment_matching_is_exact_and_conservative() -> None:
    events = [
        event().model_copy(update={"ex_dividend_date": date(2026, 3, 17)}),
        event().model_copy(
            update={
                "source_event_id": "second",
                "ex_dividend_date": date(2026, 6, 11),
            }
        ),
    ]
    payments = (
        RawDividendPayment(
            "2330", Market.TWSE, "台積電", "115/06/11", "115/07/09", "6"
        ),
        RawDividendPayment(
            "2330", Market.TPEX, "台積電", "115/03/17", "115/04/09", "6"
        ),
    )
    enriched, count = DividendEventUseCase._enrich_payment_dates(events, payments)
    assert enriched[0].payment_date is None
    assert enriched[1].payment_date == date(2026, 7, 9)
    assert count == 1


@pytest.mark.parametrize(
    ("report_date", "payment", "expected", "actual"),
    [
        (date(2026, 7, 1), date(2026, 8, 10), "Upcoming Ex-Date", Decimal("0")),
        (date(2026, 7, 27), date(2026, 8, 10), "Waiting for Payment", Decimal("0")),
        (date(2026, 8, 10), date(2026, 8, 10), "Paid", Decimal("50")),
        (date(2026, 7, 27), None, "Unknown Payment Date", Decimal("0")),
    ],
)
def test_dividend_status_boundaries(report_date, payment, expected, actual) -> None:
    value = event(payment)
    section = ReportSectionService.dividends(
        report_date,
        [value],
        {("2330", value.ex_dividend_date): Decimal("10")},
    )
    assert section.items[0].status == expected
    assert section.items[0].actual_cash_received == actual
    assert section.estimated_annual_dividend == Decimal("50")


def test_unavailable_estimate_makes_actual_received_unavailable() -> None:
    value = event(date(2026, 8, 1)).model_copy(update={"cash_dividend_per_share": None})
    section = ReportSectionService.dividends(
        date(2026, 8, 3),
        [value],
        {("2330", value.ex_dividend_date): Decimal("10")},
    )
    assert section.items[0].estimated_cash_dividend is None
    assert section.items[0].actual_cash_received is None


def test_repository_does_not_erase_known_payment_date(connection) -> None:
    initialize_schema(connection)
    repository = SQLiteDividendEventRepository(connection)
    repository.upsert_many([event(date(2026, 8, 10))])
    repository.upsert_many([event()])
    assert repository.list_filtered(symbol="2330")[0].payment_date == date(2026, 8, 10)


def test_payment_provider_failure_preserves_known_date_and_ex_update(caplog) -> None:
    value = event(date(2026, 8, 10))

    class Provider:
        def fetch(self, market=None, start_date=None, end_date=None):
            del market, start_date, end_date
            return (raw(),)

    class Payments:
        def fetch(self, symbols, start_date, end_date):
            del symbols, start_date, end_date
            raise TimeoutError("temporary")

    class Holdings:
        def list_all(self):
            return [
                Holding(
                    id="h",
                    symbol="2330",
                    name="Name",
                    market=Market.TWSE,
                    currency=Currency.TWD,
                    quantity=Decimal("10"),
                    average_cost=Decimal("1"),
                    holding_type=HoldingType.STOCK,
                )
            ]

    class Repository:
        def __init__(self):
            self.values = [value]

        def list_filtered(self, **filters):
            del filters
            return self.values

        def upsert_many(self, events):
            self.values = events
            return len(events)

    repository = Repository()
    result = DividendEventUseCase(
        Provider(), repository, Holdings(), Payments()
    ).update()
    assert result.payment_enrichment_available is False
    assert result.upserted == 1
    assert repository.values[0].payment_date == date(2026, 8, 10)
    assert "payment-date enrichment unavailable" in caplog.text


def test_dividend_summary_partitions_available_estimates() -> None:
    base = event()
    values = [
        base.model_copy(
            update={
                "source_event_id": "paid",
                "ex_dividend_date": date(2026, 1, 10),
                "payment_date": date(2026, 2, 10),
            }
        ),
        base.model_copy(
            update={
                "source_event_id": "waiting",
                "ex_dividend_date": date(2026, 7, 1),
                "payment_date": date(2026, 9, 1),
            }
        ),
        base.model_copy(
            update={
                "source_event_id": "unknown",
                "ex_dividend_date": date(2026, 7, 2),
                "payment_date": None,
            }
        ),
        base.model_copy(
            update={
                "source_event_id": "upcoming",
                "ex_dividend_date": date(2026, 12, 1),
                "payment_date": date(2026, 12, 20),
            }
        ),
        base.model_copy(
            update={
                "source_event_id": "prior-year",
                "ex_dividend_date": date(2025, 7, 1),
                "payment_date": date(2025, 8, 1),
            }
        ),
    ]
    eligible = {(item.symbol, item.ex_dividend_date): Decimal("10") for item in values}
    result = ReportSectionService.dividends(date(2026, 8, 3), values, eligible)
    assert result.estimated_annual_dividend == Decimal("200")
    assert result.already_received == Decimal("50")
    assert result.waiting_for_payment == Decimal("50")
    assert result.unknown_payment_date == Decimal("50")
    assert result.upcoming_ex_date == Decimal("50")
    assert len(result.items) == 5


def test_cli_dividend_decimal_format_has_no_exponent_or_trailing_zeroes() -> None:
    rendered = _format_dividends(
        (
            event().model_copy(
                update={
                    "cash_dividend_per_share": Decimal("36.00000000"),
                    "stock_dividend_per_share": Decimal("0E-8"),
                }
            ),
        )
    )
    assert "| 36 | 0 |" in rendered
    assert "0E-8" not in rendered


def test_empty_dividend_section_is_hidden() -> None:
    from domain import DividendCalendarSection

    sections = DailyReportSections(dividend_calendar=DividendCalendarSection())
    renderer = DailyReportSectionRenderer()
    assert renderer.dividend_calendar_html(sections) == ""
    assert renderer.dividend_calendar_text(sections) == ""


def test_persisted_dividend_cli_list(tmp_path, capsys) -> None:
    database = tmp_path / "dividends.db"
    connection = initialize_database(f"sqlite:///{database.as_posix()}")
    initialize_schema(connection)
    SQLiteDividendEventRepository(connection).upsert_many([event()])
    connection.close()
    assert main(["dividend", "list", "--database", str(database)]) == 0
    output = capsys.readouterr().out
    assert "2330 | TWSE | Name | 5" in output


def test_eligible_quantity_uses_trade_date_before_ex_date_and_partial_sells() -> None:
    holding = Holding(
        id="h1",
        symbol="2330",
        name="Name",
        market=Market.TWSE,
        currency=Currency.TWD,
        quantity=Decimal("0"),
        average_cost=Decimal("0"),
        holding_type=HoldingType.STOCK,
    )

    def transaction(identifier, side, trade_date, quantity, settlement_date):
        return Transaction(
            id=identifier,
            symbol="2330",
            market=Market.TWSE,
            transaction_type=side,
            trade_date=trade_date,
            settlement_date=settlement_date,
            quantity=Decimal(quantity),
            price=Decimal("100"),
            currency=Currency.TWD,
        )

    transactions = [
        transaction(
            "buy", TransactionType.BUY, date(2026, 7, 20), "10", date(2026, 7, 28)
        ),
        transaction(
            "sell", TransactionType.SELL, date(2026, 7, 24), "3", date(2026, 7, 24)
        ),
        transaction(
            "same-day", TransactionType.BUY, date(2026, 7, 27), "100", date(2026, 7, 27)
        ),
        transaction(
            "after", TransactionType.BUY, date(2026, 7, 28), "50", date(2026, 7, 28)
        ),
    ]
    use_case = object.__new__(BuildReportSectionsUseCase)
    quantities = use_case._eligible_quantities([event()], transactions, [holding])
    assert quantities[("2330", date(2026, 7, 27))] == Decimal("7")
