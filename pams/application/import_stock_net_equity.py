"""Controlled import of historical Stock Net Equity observations."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from domain import (
    DailySnapshot,
    HistoricalStockNetEquity,
    Liability,
    LiabilityPrincipalEvent,
    LiabilityType,
    StockNetEquityQuality,
)
from repositories import (
    SnapshotRepository,
    StockNetEquityHistoryRepository,
    StockNetEquityHistoryUnitOfWork,
)
from services import LiabilityPrincipalEngine

HISTORY_START = date(2023, 8, 7)
LEDGER_REBUILD_START = date(2026, 1, 6)
EXCEL_END = date(2026, 7, 20)
PRODUCTION_START = date(2026, 7, 21)


class StockNetEquityImportError(ValueError):
    """The controlled source cannot be imported safely."""


@dataclass(frozen=True)
class StockNetEquityImportResult:
    start_date: date
    end_date: date
    rows: tuple[HistoricalStockNetEquity, ...]
    existing: int
    missing: int
    inserted: int
    applied: bool
    database: str

    def quality_count(self, quality: StockNetEquityQuality) -> int:
        return sum(row.quality_status is quality for row in self.rows)


class ExcelV15StockNetEquitySource:
    """Parse the approved v15 workbook without calculating portfolio values."""

    source = "Excel v15 controlled reconstruction"

    def read(self, path: Path) -> tuple[HistoricalStockNetEquity, ...]:
        try:
            import openpyxl
        except ImportError as error:  # pragma: no cover - packaging guard
            raise StockNetEquityImportError(
                "historical Stock Net Equity import requires openpyxl"
            ) from error
        if not path.is_file():
            raise StockNetEquityImportError(f"source workbook not found: {path.name}")
        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
        try:
            assets = self._rows_by_date(workbook["資產趨勢"])
            liabilities = self._rows_by_date(workbook["負債資料"])
            prices = self._rows_by_date(workbook["每日價格"])
        except KeyError as error:
            raise StockNetEquityImportError(
                "source workbook is missing the approved v15 sheets"
            ) from error
        rows: list[HistoricalStockNetEquity] = []
        current = HISTORY_START
        while current <= EXCEL_END:
            asset = assets.get(current)
            liability = liabilities.get(current)
            if asset is None or liability is None:
                rows.append(self._unknown(current, path, "missing approved source row"))
            else:
                quality = self._quality(liability[4])
                total_market = _decimal_cell(asset[7], "total market value", current)
                pledge = _decimal_cell(liability[1], "pledge debt", current)
                margin = _decimal_cell(liability[2], "margin debt", current)
                total_liabilities = _decimal_cell(
                    liability[3], "total liabilities", current
                )
                net_equity = _decimal_cell(asset[6], "stock net equity", current)
                if abs(pledge + margin - total_liabilities) > Decimal("0.000001"):
                    raise StockNetEquityImportError(
                        f"liability components do not reconcile on {current}"
                    )
                if abs(total_market - total_liabilities - net_equity) > Decimal(
                    "0.000001"
                ):
                    raise StockNetEquityImportError(
                        f"Stock Net Equity does not reconcile on {current}"
                    )
                rows.append(
                    HistoricalStockNetEquity(
                        snapshot_date=current,
                        total_market_value=total_market,
                        pledge_debt=pledge,
                        margin_debt=margin,
                        total_liabilities=total_liabilities,
                        stock_net_equity=net_equity,
                        quality_status=quality,
                        source=path.name,
                        source_reference=self._source_reference(path, current, prices),
                    )
                )
            current = date.fromordinal(current.toordinal() + 1)
        return tuple(rows)

    @staticmethod
    def _source_reference(
        path: Path, day: date, prices: dict[date, tuple[object, ...]]
    ) -> str:
        reference = f"{path.name}:資產趨勢+負債資料:{day.isoformat()}"
        previous = date.fromordinal(day.toordinal() - 1)
        if (
            day in prices
            and previous in prices
            and prices[day][1:] == prices[previous][1:]
        ):
            reference += ":price=carried-forward"
        return reference

    @staticmethod
    def _rows_by_date(sheet: object) -> dict[date, tuple[object, ...]]:
        result: dict[date, tuple[object, ...]] = {}
        for raw in sheet.iter_rows(min_row=2, values_only=True):
            if raw[0] is None:
                continue
            day = raw[0].date() if isinstance(raw[0], datetime) else raw[0]
            result[day] = raw
        return result

    @staticmethod
    def _quality(value: object) -> StockNetEquityQuality:
        if value == "歷史估算":
            return StockNetEquityQuality.ESTIMATED_LIABILITY
        if value in {"無負債", "PAMS校正"}:
            return StockNetEquityQuality.VERIFIED
        raise StockNetEquityImportError(f"unsupported v15 quality label: {value!r}")

    def _unknown(self, day: date, path: Path, reason: str) -> HistoricalStockNetEquity:
        return HistoricalStockNetEquity(
            snapshot_date=day,
            quality_status=StockNetEquityQuality.UNKNOWN,
            source=path.name,
            source_reference=f"{path.name}:{day.isoformat()}:{reason}",
        )


class ImportStockNetEquityHistoryUseCase:
    """Preview or atomically insert an auditable, non-overwriting history."""

    def __init__(
        self,
        source: ExcelV15StockNetEquitySource,
        history: StockNetEquityHistoryRepository,
        snapshots: SnapshotRepository,
        liabilities: list[Liability],
        principal_events: list[LiabilityPrincipalEvent],
        unit_of_work: StockNetEquityHistoryUnitOfWork,
        database: str,
    ) -> None:
        self._source = source
        self._history = history
        self._snapshots = snapshots
        self._liabilities = liabilities
        self._principal_events = principal_events
        self._unit = unit_of_work
        self._database = database

    def execute(self, source_path: Path, *, apply: bool) -> StockNetEquityImportResult:
        excel_rows = self._rebuild_excel_liabilities(
            self._source.read(source_path), source_path.name
        )
        latest = self._snapshots.get_latest()
        production_rows = (
            self._production_rows(latest.snapshot_date) if latest is not None else ()
        )
        rows = excel_rows + production_rows
        end_date = rows[-1].snapshot_date
        existing_rows = {
            row.snapshot_date: row
            for row in self._history.list_between_dates(HISTORY_START, end_date)
        }
        self._validate_existing_rows(existing_rows, rows)
        existing_dates = set(existing_rows)
        missing_rows = [row for row in rows if row.snapshot_date not in existing_dates]
        inserted = 0
        if apply:
            with self._unit.transaction():
                inserted = self._unit.history.insert_many_if_absent(missing_rows)
        return StockNetEquityImportResult(
            HISTORY_START,
            end_date,
            rows,
            len(rows) - len(missing_rows),
            len(missing_rows),
            inserted,
            apply,
            self._database,
        )

    def _rebuild_excel_liabilities(
        self,
        rows: tuple[HistoricalStockNetEquity, ...],
        source_name: str,
    ) -> tuple[HistoricalStockNetEquity, ...]:
        """Replace the workbook's modeled debt with dated principal-ledger truth."""
        if not any(
            LEDGER_REBUILD_START <= row.snapshot_date <= EXCEL_END for row in rows
        ):
            return rows
        engine = LiabilityPrincipalEngine()
        events_by_liability: dict[str, list[LiabilityPrincipalEvent]] = {}
        for event in self._principal_events:
            events_by_liability.setdefault(event.liability_id, []).append(event)
        liability_by_type: dict[LiabilityType, list[Liability]] = {}
        for liability in self._liabilities:
            liability_by_type.setdefault(liability.liability_type, []).append(liability)
        supported = (LiabilityType.STOCK_PLEDGE, LiabilityType.MARGIN_FINANCING)
        complete = all(
            self._has_complete_zero_based_history(
                liability,
                events_by_liability.get(liability.id, []),
                EXCEL_END,
                engine,
            )
            for liability_type in supported
            for liability in liability_by_type.get(liability_type, [])
        ) and all(liability_by_type.get(liability_type) for liability_type in supported)
        if not complete:
            raise StockNetEquityImportError(
                "liability principal event history does not prove a complete "
                "zero-based opening through the historical rebuild period"
            )
        rebuilt: list[HistoricalStockNetEquity] = []
        for row in rows:
            if not LEDGER_REBUILD_START <= row.snapshot_date <= EXCEL_END:
                rebuilt.append(row)
                continue
            if row.total_market_value is None:
                rebuilt.append(
                    row.model_copy(
                        update={
                            "quality_status": StockNetEquityQuality.UNKNOWN,
                            "source_reference": (
                                f"{source_name}:資產趨勢!H:{row.snapshot_date}:"
                                "missing actual total market value"
                            ),
                        }
                    )
                )
                continue
            principal_by_type = {
                liability_type: sum(
                    (
                        engine.principal_as_of(
                            events_by_liability.get(liability.id, []),
                            row.snapshot_date,
                        )
                        for liability in liability_by_type.get(liability_type, [])
                    ),
                    Decimal("0"),
                )
                for liability_type in supported
            }
            pledge = principal_by_type[LiabilityType.STOCK_PLEDGE]
            margin = principal_by_type[LiabilityType.MARGIN_FINANCING]
            total = pledge + margin
            rebuilt.append(
                row.model_copy(
                    update={
                        "pledge_debt": pledge,
                        "margin_debt": margin,
                        "total_liabilities": total,
                        "stock_net_equity": row.total_market_value - total,
                        "quality_status": StockNetEquityQuality.VERIFIED,
                        "source": source_name,
                        "source_reference": (
                            "market_value=股票歷史資產.xlsx:資產趨勢!H;"
                            "liabilities=production_event_ledger_as_of_date;"
                            f"snapshot_date={row.snapshot_date}"
                        ),
                    }
                )
            )
        return tuple(rebuilt)

    @staticmethod
    def _has_complete_zero_based_history(
        liability: Liability,
        events: list[LiabilityPrincipalEvent],
        through: date,
        engine: LiabilityPrincipalEngine,
    ) -> bool:
        """Prove the dated prefix starts at zero without using current principal."""
        ordered = sorted(
            events,
            key=lambda item: (item.effective_date, item.sequence, item.id),
        )
        if not ordered:
            return liability.principal == 0
        first = ordered[0]
        if (
            first.resulting_principal is None
            or first.resulting_principal != first.principal_delta
        ):
            return False
        prefix = [event for event in ordered if event.effective_date <= through]
        if not prefix:
            return first.effective_date > through
        try:
            engine.timeline(prefix)
        except ValueError:
            return False
        return True

    @staticmethod
    def _validate_existing_rows(
        existing: dict[date, HistoricalStockNetEquity],
        candidates: tuple[HistoricalStockNetEquity, ...],
    ) -> None:
        fields = (
            "total_market_value",
            "pledge_debt",
            "margin_debt",
            "total_liabilities",
            "stock_net_equity",
            "quality_status",
            "source",
            "source_reference",
        )
        for candidate in candidates:
            current = existing.get(candidate.snapshot_date)
            if current is not None and any(
                getattr(current, field) != getattr(candidate, field) for field in fields
            ):
                raise StockNetEquityImportError(
                    "existing historical Stock Net Equity conflicts with rebuilt "
                    f"candidate on {candidate.snapshot_date}"
                )

    def _production_rows(self, end_date: date) -> tuple[HistoricalStockNetEquity, ...]:
        engine = LiabilityPrincipalEngine()
        events_by_liability: dict[str, list[LiabilityPrincipalEvent]] = {}
        for event in self._principal_events:
            events_by_liability.setdefault(event.liability_id, []).append(event)
        liability_by_id = {item.id: item for item in self._liabilities}
        rows: list[HistoricalStockNetEquity] = []
        for snapshot in self._snapshots.list_between_dates(PRODUCTION_START, end_date):
            principal_by_type = {
                LiabilityType.MARGIN_FINANCING: Decimal("0"),
                LiabilityType.STOCK_PLEDGE: Decimal("0"),
            }
            for liability_id, liability in liability_by_id.items():
                principal = engine.principal_as_of(
                    events_by_liability.get(liability_id, []), snapshot.snapshot_date
                )
                if liability.liability_type in principal_by_type:
                    principal_by_type[liability.liability_type] += principal
            replayed_total = sum(principal_by_type.values(), Decimal("0"))
            quality = (
                StockNetEquityQuality.VERIFIED
                if replayed_total == snapshot.total_liabilities
                else StockNetEquityQuality.UNKNOWN
            )
            rows.append(
                self._production_row(
                    snapshot, principal_by_type, replayed_total, quality
                )
            )
        return tuple(rows)

    @staticmethod
    def _production_row(
        snapshot: DailySnapshot,
        principal_by_type: dict[LiabilityType, Decimal],
        replayed_total: Decimal,
        quality: StockNetEquityQuality,
    ) -> HistoricalStockNetEquity:
        reference = f"daily_snapshots:{snapshot.snapshot_date.isoformat()}"
        if quality is StockNetEquityQuality.UNKNOWN:
            reference += (
                f":liability-mismatch:snapshot={snapshot.total_liabilities}:"
                f"ledger={replayed_total}"
            )
        return HistoricalStockNetEquity(
            snapshot_date=snapshot.snapshot_date,
            total_market_value=snapshot.total_market_value,
            pledge_debt=principal_by_type[LiabilityType.STOCK_PLEDGE],
            margin_debt=principal_by_type[LiabilityType.MARGIN_FINANCING],
            total_liabilities=snapshot.total_liabilities,
            stock_net_equity=snapshot.net_asset_value,
            quality_status=quality,
            source="PAMS production aggregate snapshot",
            source_reference=reference,
        )


def _decimal_cell(value: object, label: str, day: date) -> Decimal:
    if value is None or isinstance(value, bool):
        raise StockNetEquityImportError(f"missing {label} on {day}")
    return Decimal(str(value))
