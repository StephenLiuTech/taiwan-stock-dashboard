"""Official date-query TPEx historical market-data provider."""

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


class HistoricalTPExProvider:
    """Fetch all TPEx mainboard closes for one explicit Gregorian date."""

    market = Market.TPEX
    source = "TPEX_DAILY_QUOTES_HISTORICAL"
    endpoint = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"

    def __init__(
        self,
        trade_date: date,
        transport: JSONDocumentTransport | None = None,
    ) -> None:
        self.trade_date = trade_date
        self.transport = transport or UrllibJSONDocumentTransport()

    @property
    def url(self) -> str:
        roc_date = f"{self.trade_date.year - 1911:03d}/{self.trade_date:%m/%d}"
        return f"{self.endpoint}?{urlencode({'date': roc_date, 'id': '', 'response': 'json'})}"

    def fetch(self) -> Sequence[JSONRecord]:
        try:
            document = self.transport.get_document(self.url)
            source_date = str(document.get("date", "")).strip()
            tables = document.get("tables")
            if not isinstance(tables, list):
                raise ProviderDataError("TPEx historical response has no tables")
            records: list[JSONRecord] = []
            for table in tables:
                if not isinstance(table, Mapping):
                    continue
                fields = table.get("fields")
                rows = table.get("data")
                if not isinstance(fields, list) or "代號" not in fields:
                    continue
                if not isinstance(rows, list):
                    continue
                indexes = {str(field): index for index, field in enumerate(fields)}
                for row in rows:
                    if not isinstance(row, list):
                        continue
                    records.append(
                        {
                            "Date": source_date,
                            "Code": row[indexes["代號"]],
                            "Name": row[indexes["名稱"]],
                            "ClosingPrice": row[indexes["收盤"]],
                            "Change": row[indexes["漲跌"]],
                        }
                    )
            if not records:
                raise MarketDateUnavailableError(
                    "TPEx has no historical data for requested date"
                )
            return records
        except ProviderDataError:
            raise
        except Exception as error:
            raise ProviderDataError(
                "TPEx historical provider response is invalid"
            ) from error
