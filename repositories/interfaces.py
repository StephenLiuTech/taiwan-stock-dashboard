"""Domain-specific repository contracts."""

from contextlib import AbstractContextManager
from datetime import date, datetime
from typing import Protocol

from domain import (
    AnnualPnlSnapshot,
    CorporateAction,
    DailySnapshot,
    Dividend,
    DividendEvent,
    FxRate,
    Holding,
    InvestmentCostEvent,
    Liability,
    LiabilityPrincipalEvent,
    PositionSnapshot,
    PriceQuote,
    Transaction,
    WatchlistItem,
)


class LiabilityPrincipalEventRepository(Protocol):
    """Persistence for replayable liability principal changes."""

    def list_all(self) -> list[LiabilityPrincipalEvent]: ...
    def list_by_liability(self, liability_id: str) -> list[LiabilityPrincipalEvent]: ...
    def insert_many_if_absent(self, events: list[LiabilityPrincipalEvent]) -> int: ...


class AnnualPnlSnapshotRepository(Protocol):
    def get_by_date(self, snapshot_date: date) -> AnnualPnlSnapshot | None: ...
    def add(self, snapshot: AnnualPnlSnapshot) -> None: ...
    def list_between_dates(self, start: date, end: date) -> list[AnnualPnlSnapshot]: ...
    def list_for_year(self, year: int) -> list[AnnualPnlSnapshot]: ...


class InvestmentCostEventRepository(Protocol):
    def add(self, event: InvestmentCostEvent) -> None: ...
    def list_between_dates(
        self, start: date, end: date
    ) -> list[InvestmentCostEvent]: ...


class HoldingRepository(Protocol):
    """Persistence operations required for holdings."""

    def list_all(self) -> list[Holding]: ...
    def get_by_id(self, holding_id: str) -> Holding | None: ...
    def upsert(self, holding: Holding) -> None: ...
    def delete(self, holding_id: str) -> None: ...


class TransactionRepository(Protocol):
    """Persistence operations required for transactions."""

    def list_all(self) -> list[Transaction]: ...
    def get_by_id(self, transaction_id: str) -> Transaction | None: ...
    def list_by_symbol(self, symbol: str) -> list[Transaction]: ...
    def exists(self, transaction_id: str) -> bool: ...
    def list_filtered(
        self,
        *,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[Transaction]: ...
    def add(self, transaction: Transaction) -> None: ...
    def upsert(self, transaction: Transaction) -> None: ...
    def delete(self, transaction_id: str) -> None: ...


class CorporateActionRepository(Protocol):
    """Persistence for replayable non-cash quantity conversions."""

    def list_all(self) -> list[CorporateAction]: ...
    def list_filtered(
        self,
        *,
        symbol: str | None = None,
        end_date: date | None = None,
    ) -> list[CorporateAction]: ...
    def get_by_id(self, action_id: str) -> CorporateAction | None: ...
    def add(self, action: CorporateAction) -> None: ...


class DividendRepository(Protocol):
    """Persistence operations required for dividends."""

    def list_all(self) -> list[Dividend]: ...
    def get_by_id(self, dividend_id: str) -> Dividend | None: ...
    def list_by_symbol(self, symbol: str) -> list[Dividend]: ...
    def upsert(self, dividend: Dividend) -> None: ...
    def delete(self, dividend_id: str) -> None: ...


class DividendEventRepository(Protocol):
    """Normalized official dividend-event persistence."""

    def upsert_many(self, events: list[DividendEvent]) -> int: ...
    def list_filtered(
        self,
        *,
        symbol: str | None = None,
        market: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        year: int | None = None,
    ) -> list[DividendEvent]: ...


class LiabilityRepository(Protocol):
    """Persistence operations required for liabilities."""

    def list_all(self) -> list[Liability]: ...
    def get_by_id(self, liability_id: str) -> Liability | None: ...
    def upsert(self, liability: Liability) -> None: ...
    def delete(self, liability_id: str) -> None: ...


class SnapshotRepository(Protocol):
    """Persistence operations required for daily snapshots."""

    def get_by_date(self, snapshot_date: date) -> DailySnapshot | None: ...
    def get_latest(self) -> DailySnapshot | None: ...
    def get_highest(self) -> DailySnapshot | None: ...
    def list_between_dates(self, start: date, end: date) -> list[DailySnapshot]: ...
    def add(self, snapshot: DailySnapshot) -> None: ...
    def replace(self, snapshot: DailySnapshot) -> None: ...


class PriceQuoteRepository(Protocol):
    """Persistence operations required for normalized market prices."""

    def upsert_many(self, quotes: list[PriceQuote]) -> None: ...
    def list_by_date(self, trade_date: date) -> list[PriceQuote]: ...
    def get_latest(self, symbol: str, market: str) -> PriceQuote | None: ...
    def get_latest_on_or_before(
        self, symbol: str, market: str, trade_date: date
    ) -> PriceQuote | None: ...
    def get_latest_date(self) -> date | None: ...
    def replace_many_for_date(
        self, trade_date: date, quotes: list[PriceQuote]
    ) -> None: ...


class FxRateRepository(Protocol):
    """Persistence operations for native/reporting currency rates."""

    def upsert(self, rate: FxRate) -> None: ...
    def insert_if_absent(self, rate: FxRate) -> bool: ...
    def list_between(
        self,
        base_currency: str,
        quote_currency: str,
        start_date: date,
        end_date: date,
    ) -> list[FxRate]: ...
    def get_latest_on_or_before(
        self,
        base_currency: str,
        quote_currency: str,
        rate_date: date,
    ) -> FxRate | None: ...


class PositionSnapshotRepository(Protocol):
    """Persistence operations for holding-level snapshot rows."""

    def add_many(self, snapshots: list[PositionSnapshot]) -> None: ...
    def list_by_date(self, snapshot_date: date) -> list[PositionSnapshot]: ...
    def get_latest_date(self) -> date | None: ...
    def replace_many(
        self, snapshot_date: date, snapshots: list[PositionSnapshot]
    ) -> None: ...


class ReportDeliveryRepository(Protocol):
    """Idempotent report delivery persistence."""

    def claim(self, report_type: str, report_date: date, recipient: str) -> bool: ...
    def mark_sent(
        self, report_type: str, report_date: date, recipient: str, sent_at: datetime
    ) -> None: ...
    def mark_failed(
        self, report_type: str, report_date: date, recipient: str, error: str
    ) -> None: ...


class WatchlistRepository(Protocol):
    """Persistence operations for manually selected watchlist instruments."""

    def list_all(self) -> list[WatchlistItem]: ...
    def get(self, symbol: str, market: str | None = None) -> WatchlistItem | None: ...
    def add(self, item: WatchlistItem) -> None: ...
    def remove(self, symbol: str, market: str | None = None) -> bool: ...


class MarketDataUnitOfWork(Protocol):
    """Atomic persistence boundary for one valuation snapshot."""

    price_quotes: PriceQuoteRepository
    fx_rates: FxRateRepository
    daily_snapshots: SnapshotRepository
    position_snapshots: PositionSnapshotRepository

    def transaction(self) -> AbstractContextManager[None]: ...


class HoldingRebuildRepository(Protocol):
    """Only holding operations permitted during a rebuild."""

    def list_all(self) -> list[Holding]: ...
    def upsert(self, holding: Holding) -> None: ...


class TransactionLedgerRepository(Protocol):
    """Read-only transaction access permitted during a rebuild."""

    def list_all(self) -> list[Transaction]: ...


class HoldingRebuildUnitOfWork(Protocol):
    """Atomic repository boundary for applying a holding rebuild."""

    holdings: HoldingRebuildRepository
    transactions: TransactionLedgerRepository

    def transaction(self) -> AbstractContextManager[None]: ...


class MarginTransactionUnitOfWork(Protocol):
    """Atomic persistence boundary for a margin BUY and its projections."""

    holdings: HoldingRepository
    transactions: TransactionRepository
    liabilities: LiabilityRepository

    def transaction(self) -> AbstractContextManager[None]: ...


class BootstrapImportUnitOfWork(Protocol):
    """Atomic full-ledger replacement and holding rebuild boundary."""

    holdings: HoldingRepository
    transactions: TransactionRepository

    def transaction(self) -> AbstractContextManager[None]: ...
