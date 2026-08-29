"""Adapter for the verified Taiwan brokerage transaction CSV format."""

import csv
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from domain import Currency, FinancingType, Market, TransactionType
from pams.brokerage.models import BrokerRecordKind, NormalizedBrokerRecord


class BrokerStatementError(ValueError):
    """Raised when a source statement cannot be normalized safely."""


class TaiwanBrokerCsvParser:
    """Normalize statement rows without performing persistence."""

    REQUIRED = {
        "股名",
        "日期",
        "成交股數",
        "淨收付",
        "成交單價",
        "成交價金",
        "手續費",
        "交易稅",
        "稅款",
        "委託書號",
        "幣別",
        "備註",
    }

    def parse(
        self,
        source: Path,
        securities: Mapping[str, tuple[str, Market]],
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[int, tuple[NormalizedBrokerRecord, ...]]:
        if not source.is_file():
            raise BrokerStatementError(f"Broker statement does not exist: {source}")
        records: list[NormalizedBrokerRecord] = []
        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = csv.DictReader(stream)
            if not rows.fieldnames or not self.REQUIRED.issubset(rows.fieldnames):
                raise BrokerStatementError("Broker statement columns are incomplete")
            source_rows = 0
            for row_number, row in enumerate(rows, start=2):
                source_rows += 1
                record = self._normalize(row, row_number, securities)
                effective_date = record.trade_date
                if effective_date is not None and (
                    (start_date is not None and effective_date < start_date)
                    or (end_date is not None and effective_date > end_date)
                ):
                    continue
                records.append(record)
        return source_rows, tuple(records)

    def _normalize(
        self,
        row: Mapping[str, str],
        row_number: int,
        securities: Mapping[str, tuple[str, Market]],
    ) -> NormalizedBrokerRecord:
        name = row["股名"].strip()
        identity = securities.get(name)
        try:
            trade_date = date.fromisoformat(row["日期"].strip().replace("/", "-"))
            quantity = _decimal(row["成交股數"])
            price = _decimal(row["成交單價"])
            gross = _decimal(row["成交價金"])
            fee = _decimal(row["手續費"])
            tax = _decimal(row["交易稅"]) + _decimal(row["稅款"])
            net = _decimal(row["淨收付"])
        except (ValueError, InvalidOperation) as error:
            raise BrokerStatementError(
                f"Malformed broker statement row {row_number}"
            ) from error
        if identity is None:
            return NormalizedBrokerRecord(
                "taiwan-broker",
                row["委託書號"].strip() or f"row-{row_number}",
                row_number,
                BrokerRecordKind.UNSUPPORTED,
                trade_date,
                trade_date,
                None,
                None,
                None,
                None,
                quantity,
                price,
                gross,
                fee,
                tax,
                net,
                Currency.TWD,
                f"Security market is unknown for broker name {name!r}",
            )
        symbol, market = identity
        transaction_type = TransactionType.BUY if net < 0 else TransactionType.SELL
        note = row["備註"].strip()
        kind = self._record_kind(note)
        financing = (
            FinancingType.MARGIN
            if "融資" in note and kind is BrokerRecordKind.TRADE
            else None
        )
        return NormalizedBrokerRecord(
            "taiwan-broker",
            row["委託書號"].strip() or f"row-{row_number}",
            row_number,
            kind,
            trade_date,
            trade_date,
            symbol,
            market,
            transaction_type if kind is BrokerRecordKind.TRADE else None,
            financing,
            quantity,
            price,
            gross,
            fee,
            tax,
            net,
            Currency.TWD,
            (
                "Broker statement does not explicitly identify financing type"
                if kind is BrokerRecordKind.TRADE and financing is None
                else None
            ),
        )

    @staticmethod
    def _record_kind(note: str) -> BrokerRecordKind:
        if any(value in note for value in ("股利", "股息", "現金分配")):
            return BrokerRecordKind.DIVIDEND
        if any(value in note for value in ("分割", "反分割", "減資", "合併")):
            return BrokerRecordKind.CORPORATE_ACTION
        if any(value in note for value in ("融資償還", "融資息", "融資結算")):
            return BrokerRecordKind.FINANCING_SETTLEMENT
        return BrokerRecordKind.TRADE


def _decimal(value: str) -> Decimal:
    normalized = value.strip().replace(",", "")
    return Decimal(normalized or "0")
