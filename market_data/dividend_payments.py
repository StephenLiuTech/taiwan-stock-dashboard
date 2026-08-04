"""Official MOPS cash-dividend payment-date adapter."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

from domain import Market
from market_data.exceptions import ProviderDataError
from market_data.transport import TextFormTransport

MOPS_DIVIDEND_ANNOUNCEMENTS_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t108sb27"


@dataclass(frozen=True)
class RawDividendPayment:
    symbol: str
    market: Market
    name: str
    ex_dividend_date: str
    payment_date: str
    cash_dividend_per_share: str
    source: str = "MOPS t108sb27"


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip().replace("\xa0", ""))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


class MOPSDividendPaymentProvider:
    """Read official payment dates for a market's current portfolio symbols."""

    _HEADERS = {
        "公司代號",
        "公司名稱",
        "除息交易日",
        "現金股利發放日",
        "盈餘分配之股東現金股利(元/股)",
        "法定盈餘公積、資本公積發放之現金(元/股)",
    }

    def __init__(self, market: Market, transport: TextFormTransport) -> None:
        self.market = market
        self.transport = transport

    def fetch(
        self, symbols: tuple[str, ...], start_date: date, end_date: date
    ) -> tuple[RawDividendPayment, ...]:
        years = range(start_date.year, end_date.year + 1)
        values: list[RawDividendPayment] = []
        for symbol in sorted(set(symbols)):
            for year in years:
                html = self.transport.post_form(
                    MOPS_DIVIDEND_ANNOUNCEMENTS_URL,
                    {
                        "step": "1",
                        "firstin": "true",
                        "off": "1",
                        "TYPEK": "sii" if self.market is Market.TWSE else "otc",
                        "co_id_1": symbol,
                        "co_id_2": symbol,
                        "year": str(year - 1911),
                        "month": "",
                        "b_date": "",
                        "e_date": "",
                        "type": "3",
                    },
                )
                values.extend(self._parse(html))
        return tuple(values)

    def _parse(self, html: str) -> list[RawDividendPayment]:
        parser = _TableParser()
        parser.feed(html)
        first_header = next(
            (
                index
                for index, row in enumerate(parser.rows)
                if {"公司代號", "公司名稱"}.issubset(set(row))
            ),
            None,
        )
        if first_header is None or first_header + 1 >= len(parser.rows):
            if "查無資料" in html or not parser.rows:
                return []
            raise ProviderDataError("MOPS dividend payment fields are incomplete")
        second_header = first_header + 1
        headers = parser.rows[first_header] + parser.rows[second_header]
        if not self._HEADERS.issubset(set(headers)):
            raise ProviderDataError("MOPS dividend payment fields are incomplete")
        results: list[RawDividendPayment] = []
        rows = parser.rows[second_header + 1 :]
        for row in rows:
            # MOPS renders two header rows but flattens each data record in this
            # documented display order: identity, distribution fields, then
            # announcement metadata. The first twelve cells are stable fields.
            if len(row) < 12 or not row[0]:
                continue
            ex_date = row[10]
            payment = row[11]
            if not ex_date or not payment:
                continue
            cash_values = (row[7], row[8])
            results.append(
                RawDividendPayment(
                    row[0],
                    self.market,
                    row[1],
                    ex_date,
                    payment,
                    self._cash_total(cash_values),
                )
            )
        return results

    @staticmethod
    def _cash_total(values: tuple[str, str]) -> str:
        try:
            return str(
                sum(
                    (Decimal(value.replace(",", "")) for value in values if value),
                    Decimal("0"),
                )
            )
        except InvalidOperation as error:
            raise ProviderDataError(
                "MOPS dividend payment contains an invalid cash amount"
            ) from error


class OfficialDividendPaymentProvider:
    """Combine official MOPS payment-date adapters without changing their grain."""

    def __init__(
        self, twse: MOPSDividendPaymentProvider, tpex: MOPSDividendPaymentProvider
    ) -> None:
        self.providers = {Market.TWSE: twse, Market.TPEX: tpex}

    def fetch(
        self,
        symbols_by_market: dict[Market, tuple[str, ...]],
        start_date: date,
        end_date: date,
    ) -> tuple[RawDividendPayment, ...]:
        return tuple(
            item
            for market, provider in self.providers.items()
            for item in provider.fetch(
                symbols_by_market.get(market, ()), start_date, end_date
            )
        )
