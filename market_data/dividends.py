"""Official TWSE and TPEx dividend announcement adapters."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol
from urllib.parse import urlencode

from domain import Market
from market_data.exceptions import ProviderDataError
from market_data.transport import JSONDocumentTransport, JSONTransport

TWSE_DIVIDEND_ANNOUNCEMENTS_URL = (
    "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"
)
TPEX_DIVIDEND_ANNOUNCEMENTS_URL = (
    "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"
)
TWSE_DIVIDEND_RESULTS_URL = "https://www.twse.com.tw/exchangeReport/TWT49U"
TPEX_DIVIDEND_RESULTS_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ"


@dataclass(frozen=True)
class RawDividendEvent:
    """Provider-normalized strings awaiting application validation."""

    symbol: str
    market: Market
    name: str
    ex_dividend_date: str
    cash_dividend_per_share: str
    stock_dividend_per_share: str
    source: str
    event_type: str = "dividend"
    source_updated_at: datetime | None = None


class DividendSource(Protocol):
    def fetch(
        self, start_date: date | None = None, end_date: date | None = None
    ) -> tuple[RawDividendEvent, ...]: ...


class TWSEDividendProvider:
    """Read listed stock and ETF ex-dividend announcements from TWSE OpenAPI."""

    def __init__(self, transport: JSONTransport) -> None:
        self.transport = transport

    def fetch(
        self, start_date: date | None = None, end_date: date | None = None
    ) -> tuple[RawDividendEvent, ...]:
        del start_date, end_date
        return tuple(
            RawDividendEvent(
                str(row.get("Code", "")),
                Market.TWSE,
                str(row.get("Name", "")),
                str(row.get("Date", "")),
                str(row.get("CashDividend", "")),
                str(row.get("StockDividendRatio", "")),
                "TWSE TWT48U_ALL",
                str(row.get("Exdividend", "")),
            )
            for row in self.transport.get_records(TWSE_DIVIDEND_ANNOUNCEMENTS_URL)
        )


class TPExDividendProvider:
    """Read OTC stock and ETF ex-dividend announcements from TPEx OpenAPI."""

    def __init__(self, transport: JSONTransport) -> None:
        self.transport = transport

    def fetch(
        self, start_date: date | None = None, end_date: date | None = None
    ) -> tuple[RawDividendEvent, ...]:
        del start_date, end_date
        return tuple(
            RawDividendEvent(
                str(row.get("SecuritiesCompanyCode", "")),
                Market.TPEX,
                str(row.get("CompanyName", "")),
                str(row.get("ExRrightsExDividendDate", "")),
                str(row.get("CashDividend", "")),
                str(row.get("StockDividendRatio", "")),
                "TPEx tpex_exright_prepost",
                str(row.get("ExRrightsExDividend", "")),
            )
            for row in self.transport.get_records(TPEX_DIVIDEND_ANNOUNCEMENTS_URL)
        )


class OfficialDividendProvider:
    """Select configured official adapters without mixing parsing concerns."""

    def __init__(self, twse: DividendSource, tpex: DividendSource) -> None:
        self.providers = {Market.TWSE: twse, Market.TPEX: tpex}

    def fetch(
        self,
        market: Market | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[RawDividendEvent, ...]:
        providers = (
            (self.providers[market],)
            if market is not None
            else tuple(self.providers.values())
        )
        return tuple(
            item
            for provider in providers
            for item in provider.fetch(start_date, end_date)
        )


class CompositeDividendSource:
    """Combine announcement and historical-result feeds for one market."""

    def __init__(self, *sources: DividendSource) -> None:
        self.sources = sources

    def fetch(
        self, start_date: date | None = None, end_date: date | None = None
    ) -> tuple[RawDividendEvent, ...]:
        return tuple(
            item
            for source in self.sources
            for item in source.fetch(start_date, end_date)
        )


def _query_period(start_date: date | None, end_date: date | None) -> tuple[date, date]:
    year = date.today().year
    return start_date or date(year, 1, 1), end_date or date(year, 12, 31)


class TWSEHistoricalDividendProvider:
    """Read official listed-market ex-right/dividend calculation results."""

    def __init__(self, transport: JSONDocumentTransport) -> None:
        self.transport = transport

    def fetch(
        self, start_date: date | None = None, end_date: date | None = None
    ) -> tuple[RawDividendEvent, ...]:
        start, end = _query_period(start_date, end_date)
        url = (
            TWSE_DIVIDEND_RESULTS_URL
            + "?"
            + urlencode(
                {
                    "response": "json",
                    "startDate": start.strftime("%Y%m%d"),
                    "endDate": end.strftime("%Y%m%d"),
                }
            )
        )
        payload = self.transport.get_document(url)
        fields, data = payload.get("fields"), payload.get("data")
        if not isinstance(fields, list) or not isinstance(data, list):
            raise ProviderDataError("TWSE dividend results have an invalid schema")
        index = {str(field): position for position, field in enumerate(fields)}
        required = {"資料日期", "股票代號", "股票名稱", "權值+息值", "權/息"}
        if not required.issubset(index):
            raise ProviderDataError("TWSE dividend result fields are incomplete")
        return tuple(self._row(row, index) for row in data if isinstance(row, list))

    @staticmethod
    def _row(row: list[object], index: dict[str, int]) -> RawDividendEvent:
        event_type = str(row[index["權/息"]])
        value = str(row[index["權值+息值"]])
        return RawDividendEvent(
            str(row[index["股票代號"]]),
            Market.TWSE,
            str(row[index["股票名稱"]]),
            str(row[index["資料日期"]]),
            value if "息" in event_type else "",
            "",
            "TWSE TWT49U",
            event_type,
        )


class TPExHistoricalDividendProvider:
    """Read official OTC ex-right/dividend calculation results."""

    def __init__(self, transport: JSONDocumentTransport) -> None:
        self.transport = transport

    def fetch(
        self, start_date: date | None = None, end_date: date | None = None
    ) -> tuple[RawDividendEvent, ...]:
        start, end = _query_period(start_date, end_date)
        url = (
            TPEX_DIVIDEND_RESULTS_URL
            + "?"
            + urlencode(
                {
                    "startDate": start.strftime("%Y/%m/%d"),
                    "endDate": end.strftime("%Y/%m/%d"),
                }
            )
        )
        payload = self.transport.get_document(url)
        tables = payload.get("tables")
        if (
            not isinstance(tables, list)
            or not tables
            or not isinstance(tables[0], dict)
        ):
            raise ProviderDataError("TPEx dividend results have an invalid schema")
        fields, data = tables[0].get("fields"), tables[0].get("data")
        if not isinstance(fields, list) or not isinstance(data, list):
            raise ProviderDataError("TPEx dividend results have an invalid schema")
        index = {str(field): position for position, field in enumerate(fields)}
        required = {"除權息日期", "代號", "名稱", "權/息", "現金股利", "每仟股無償配股"}
        if not required.issubset(index):
            raise ProviderDataError("TPEx dividend result fields are incomplete")
        return tuple(
            RawDividendEvent(
                str(row[index["代號"]]),
                Market.TPEX,
                str(row[index["名稱"]]),
                str(row[index["除權息日期"]]),
                str(row[index["現金股利"]]),
                self._stock_per_share(row[index["每仟股無償配股"]]),
                "TPEx exDailyQ",
                str(row[index["權/息"]]),
            )
            for row in data
            if isinstance(row, list)
        )

    @staticmethod
    def _stock_per_share(value: object) -> str:
        try:
            return str(Decimal(str(value).replace(",", "")) / Decimal("1000"))
        except InvalidOperation as error:
            raise ProviderDataError(
                "TPEx dividend result contains an invalid stock distribution"
            ) from error
