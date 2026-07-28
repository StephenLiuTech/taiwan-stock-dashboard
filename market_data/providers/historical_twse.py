"""Official date-query TWSE historical market-data provider."""

import re
from collections.abc import Mapping, Sequence
from datetime import date
from urllib.parse import urlencode

from domain import Market
from market_data.exceptions import MarketDateUnavailableError, ProviderDataError
from market_data.transport import (
    JSONDocumentTransport,
    JSONRecord,
    UrllibJSONDocumentTransport,
)


def _table_rows(
    document: JSONRecord, required_field: str
) -> tuple[list[str], list[list[object]]]:
    tables = document.get("tables")
    if not isinstance(tables, list):
        raise ProviderDataError("TWSE historical response has no tables")
    for table in tables:
        if not isinstance(table, Mapping):
            continue
        fields = table.get("fields")
        data = table.get("data")
        if (
            isinstance(fields, list)
            and required_field in fields
            and isinstance(data, list)
        ):
            return [str(field) for field in fields], [
                list(row) for row in data if isinstance(row, list)
            ]
    raise ProviderDataError("TWSE historical response has no security quote table")


def _signed_change(sign_value: object, change_value: object) -> str:
    sign = re.sub(r"<[^>]+>", "", str(sign_value)).strip()
    change = str(change_value).strip()
    if sign == "+":
        return f"+{change}"
    if sign == "-":
        return f"-{change}"
    if sign.upper() == "X":
        return "-"
    return change


class HistoricalTWSEProvider:
    """Fetch all TWSE security closes for one explicit Gregorian date."""

    market = Market.TWSE
    source = "TWSE_MI_INDEX_HISTORICAL"
    endpoint = "https://www.twse.com.tw/exchangeReport/MI_INDEX"

    def __init__(
        self,
        trade_date: date,
        transport: JSONDocumentTransport | None = None,
    ) -> None:
        self.trade_date = trade_date
        self.transport = transport or UrllibJSONDocumentTransport(
            provider_name=self.market.value
        )

    @property
    def url(self) -> str:
        return f"{self.endpoint}?{urlencode({'response': 'json', 'date': self.trade_date.strftime('%Y%m%d'), 'type': 'ALLBUT0999'})}"

    def fetch(self) -> Sequence[JSONRecord]:
        try:
            document = self.transport.get_document(self.url)
            if document.get("stat") != "OK":
                raise MarketDateUnavailableError(
                    "TWSE has no historical data for requested date"
                )
            source_date = str(document.get("date", "")).strip()
            fields, rows = _table_rows(document, "證券代號")
            indexes = {field: fields.index(field) for field in fields}
            return [
                {
                    "Date": source_date,
                    "Code": row[indexes["證券代號"]],
                    "Name": row[indexes["證券名稱"]],
                    "ClosingPrice": row[indexes["收盤價"]],
                    "Change": _signed_change(
                        row[indexes["漲跌(+/-)"]], row[indexes["漲跌價差"]]
                    ),
                }
                for row in rows
            ]
        except ProviderDataError:
            raise
        except Exception as error:
            raise ProviderDataError(
                "TWSE historical provider response is invalid"
            ) from error
