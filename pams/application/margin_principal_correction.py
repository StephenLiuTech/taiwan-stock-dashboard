"""Controlled correction of broker-rounded margin principal and derivatives."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from domain import (
    AnnualPnlSnapshot,
    DailySnapshot,
    FinancingType,
    HistoricalStockNetEquity,
    InvestmentCostEvent,
    Liability,
    LiabilityPrincipalEvent,
    LiabilityPrincipalEventType,
    TransactionType,
)
from pams.application.financing_interest import financing_interest_event_id
from repositories.postgresql import (
    PostgreSQLAnnualPnlSnapshotRepository,
    PostgreSQLInvestmentCostEventRepository,
    PostgreSQLLiabilityPrincipalEventRepository,
    PostgreSQLLiabilityRepository,
    PostgreSQLSnapshotRepository,
    PostgreSQLStockNetEquityHistoryRepository,
    PostgreSQLTransactionRepository,
)
from repositories.sqlite import (
    SQLiteAnnualPnlSnapshotRepository,
    SQLiteInvestmentCostEventRepository,
    SQLiteLiabilityPrincipalEventRepository,
    SQLiteLiabilityRepository,
    SQLiteSnapshotRepository,
    SQLiteStockNetEquityHistoryRepository,
    SQLiteTransactionRepository,
)
from services import LiabilityPrincipalEngine, MarginFinancingService

MARGIN_ID = "liability-margin-financing"
PLEDGE_ID = "liability-stock-pledge"
CATCHUP_ID = "financing-catchup-margin-through-2026-08-28"
ANNUAL_RATE = Decimal("0.065")


class MarginPrincipalCorrectionError(ValueError):
    """The reviewed production state changed or cannot be reconciled."""


@dataclass(frozen=True)
class PrincipalUpdate:
    effective_date: date
    expected_delta: Decimal
    corrected_delta: Decimal


@dataclass(frozen=True)
class PrincipalInsert:
    transaction_id: str
    effective_date: date
    corrected_delta: Decimal


@dataclass(frozen=True)
class MarginPrincipalCorrectionRequest:
    event_updates: tuple[PrincipalUpdate, ...]
    event_inserts: tuple[PrincipalInsert, ...]
    expected_master_principal: Decimal
    corrected_master_principal: Decimal
    first_affected_date: date
    expected_latest_snapshot: date


@dataclass(frozen=True)
class MarginPrincipalCorrectionPlan:
    fingerprint: str
    event_updates: tuple[tuple[LiabilityPrincipalEvent, LiabilityPrincipalEvent], ...]
    event_inserts: tuple[LiabilityPrincipalEvent, ...]
    master: tuple[Liability, Liability]
    daily: tuple[tuple[DailySnapshot, DailySnapshot], ...]
    history: tuple[tuple[HistoricalStockNetEquity, HistoricalStockNetEquity], ...]
    interest: tuple[tuple[InvestmentCostEvent, InvestmentCostEvent], ...]
    annual: tuple[tuple[AnnualPnlSnapshot, AnnualPnlSnapshot], ...]
    position_fingerprint: str
    already_applied: bool = False


def approved_2027_request() -> MarginPrincipalCorrectionRequest:
    """Return the reviewed September 2026 broker-principal correction facts."""
    return MarginPrincipalCorrectionRequest(
        event_updates=(
            PrincipalUpdate(date(2026, 8, 25), Decimal("60960"), Decimal("60000")),
            PrincipalUpdate(date(2026, 8, 27), Decimal("58560"), Decimal("58000")),
            PrincipalUpdate(date(2026, 8, 28), Decimal("87480"), Decimal("87000")),
            PrincipalUpdate(date(2026, 8, 31), Decimal("57720"), Decimal("57000")),
            PrincipalUpdate(date(2026, 9, 4), Decimal("59160"), Decimal("59000")),
        ),
        event_inserts=(
            PrincipalInsert(
                "6244d304-525e-4cc9-be1a-110d0bf3a2e3",
                date(2026, 9, 7),
                Decimal("58000"),
            ),
            PrincipalInsert(
                "e522cf25-fdf9-411b-9300-064ba465b481",
                date(2026, 9, 9),
                Decimal("58000"),
            ),
            PrincipalInsert(
                "6954d39f-3bc0-42d5-97ee-bb0d489a9a00",
                date(2026, 9, 21),
                Decimal("56000"),
            ),
            PrincipalInsert(
                "b231db07-61f0-4331-a239-d8dd99f8e601",
                date(2026, 9, 23),
                Decimal("55000"),
            ),
            PrincipalInsert(
                "cddffe34-5559-4b3a-b821-1c418c89af6e",
                date(2026, 9, 29),
                Decimal("85000"),
            ),
        ),
        expected_master_principal=Decimal("1404650"),
        corrected_master_principal=Decimal("1400000"),
        first_affected_date=date(2026, 8, 25),
        expected_latest_snapshot=date(2026, 9, 30),
    )


class MarginPrincipalCorrectionUseCase:
    """Preview or atomically apply one reviewed principal restatement."""

    def __init__(self, connection: object, repositories: object) -> None:
        self.connection = connection
        self.repos = repositories
        postgres = getattr(connection, "backend", None) == "postgresql"
        namespace = (
            (
                PostgreSQLTransactionRepository,
                PostgreSQLLiabilityRepository,
                PostgreSQLLiabilityPrincipalEventRepository,
                PostgreSQLSnapshotRepository,
                PostgreSQLStockNetEquityHistoryRepository,
                PostgreSQLInvestmentCostEventRepository,
                PostgreSQLAnnualPnlSnapshotRepository,
            )
            if postgres
            else (
                SQLiteTransactionRepository,
                SQLiteLiabilityRepository,
                SQLiteLiabilityPrincipalEventRepository,
                SQLiteSnapshotRepository,
                SQLiteStockNetEquityHistoryRepository,
                SQLiteInvestmentCostEventRepository,
                SQLiteAnnualPnlSnapshotRepository,
            )
        )
        self.transactions = namespace[0](connection, auto_commit=False)
        self.liabilities = namespace[1](connection, auto_commit=False)
        self.events = namespace[2](connection, auto_commit=False)
        self.daily = namespace[3](connection, auto_commit=False)
        self.history = namespace[4](connection, auto_commit=False)
        self.costs = namespace[5](connection, auto_commit=False)
        self.annual = namespace[6](connection, auto_commit=False)
        self.principal_engine = LiabilityPrincipalEngine()

    def preview(
        self, request: MarginPrincipalCorrectionRequest
    ) -> MarginPrincipalCorrectionPlan:
        return self._build_plan(request)

    def apply(
        self,
        request: MarginPrincipalCorrectionRequest,
        *,
        reviewed_fingerprint: str,
    ) -> MarginPrincipalCorrectionPlan:
        with self._transaction():
            plan = self._build_plan(request)
            if plan.fingerprint != reviewed_fingerprint:
                raise MarginPrincipalCorrectionError(
                    "Production changed since reviewed dry-run"
                )
            if plan.already_applied:
                return plan
            for _, corrected in plan.event_updates:
                self.events.replace(corrected)
            inserted = self.events.insert_many_if_absent(list(plan.event_inserts))
            if inserted != len(plan.event_inserts):
                raise MarginPrincipalCorrectionError(
                    "principal event insert assertion failed"
                )
            self.liabilities.upsert(plan.master[1])
            for _, corrected in plan.daily:
                self.daily.replace(corrected)
            for _, corrected in plan.history:
                self.history.replace(corrected)
            for _, corrected in plan.interest:
                self.costs.replace(corrected)
            for _, corrected in plan.annual:
                self.annual.replace(corrected)
            if (
                self._position_fingerprint(
                    request.first_affected_date, request.expected_latest_snapshot
                )
                != plan.position_fingerprint
            ):
                raise MarginPrincipalCorrectionError(
                    "position snapshots changed during principal correction"
                )
            if self.liabilities.get_by_id(MARGIN_ID) != plan.master[1]:
                raise MarginPrincipalCorrectionError("margin master assertion failed")
            return plan

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        if getattr(self.connection, "backend", None) == "postgresql":
            self.connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        else:
            self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def _build_plan(
        self, request: MarginPrincipalCorrectionRequest
    ) -> MarginPrincipalCorrectionPlan:
        latest = self.daily.get_latest()
        if latest is None or latest.snapshot_date != request.expected_latest_snapshot:
            raise MarginPrincipalCorrectionError("latest snapshot changed")
        margin = self.liabilities.get_by_id(MARGIN_ID)
        if margin is None:
            raise MarginPrincipalCorrectionError("margin liability is missing")
        if margin.principal not in (
            request.expected_master_principal,
            request.corrected_master_principal,
        ):
            raise MarginPrincipalCorrectionError("margin master principal changed")
        existing_events = self.events.list_all()
        original_events = list(existing_events)
        margin_by_date = {
            item.effective_date: item
            for item in existing_events
            if item.liability_id == MARGIN_ID
        }
        replacements: dict[str, LiabilityPrincipalEvent] = {}
        event_changes = []
        for spec in request.event_updates:
            old = margin_by_date.get(spec.effective_date)
            if old is None or old.principal_delta not in (
                spec.expected_delta,
                spec.corrected_delta,
            ):
                raise MarginPrincipalCorrectionError(
                    f"principal event precondition failed for {spec.effective_date}"
                )
            replacements[old.id] = old.model_copy(
                update={"principal_delta": spec.corrected_delta}
            )
        inserted = []
        transactions = {item.id: item for item in self.transactions.list_all()}
        for spec in request.event_inserts:
            transaction = transactions.get(spec.transaction_id)
            if (
                transaction is None
                or transaction.trade_date != spec.effective_date
                or transaction.transaction_type is not TransactionType.BUY
                or transaction.financing_type is not FinancingType.MARGIN
            ):
                raise MarginPrincipalCorrectionError(
                    f"margin BUY precondition failed: {spec.transaction_id}"
                )
            expected = (
                MarginFinancingService(Decimal("0.40"))
                .apply(
                    transaction,
                    margin.model_copy(
                        update={
                            "principal": Decimal("0"),
                            "financed_symbol": transaction.symbol,
                            "financed_quantity": Decimal("0"),
                        }
                    ),
                )
                .financed_principal
            )
            if expected != spec.corrected_delta:
                raise MarginPrincipalCorrectionError(
                    f"broker rounding mismatch: {spec.transaction_id}"
                )
            event_id = str(
                uuid5(NAMESPACE_URL, f"margin-principal|{MARGIN_ID}|{transaction.id}")
            )
            current = next(
                (item for item in existing_events if item.id == event_id), None
            )
            proposed = LiabilityPrincipalEvent(
                id=event_id,
                liability_id=MARGIN_ID,
                effective_date=spec.effective_date,
                sequence=10,
                event_type=LiabilityPrincipalEventType.INCREASE,
                principal_delta=spec.corrected_delta,
                resulting_principal=None,
                source="broker statement correction",
                reference=transaction.id,
                notes="Broker-rounded margin principal",
                created_at=datetime.combine(
                    spec.effective_date, datetime.min.time(), UTC
                ),
            )
            if current is not None:
                comparable = proposed.model_copy(
                    update={"resulting_principal": current.resulting_principal}
                )
                if current != comparable:
                    raise MarginPrincipalCorrectionError(
                        f"existing correction event conflicts: {event_id}"
                    )
            else:
                inserted.append(proposed)
        combined = [replacements.get(item.id, item) for item in existing_events]
        combined.extend(inserted)
        principal = Decimal("0")
        normalized = []
        for event in sorted(
            [item for item in combined if item.liability_id == MARGIN_ID],
            key=lambda item: (item.effective_date, item.sequence, item.id),
        ):
            principal += event.principal_delta
            normalized_event = event.model_copy(
                update={"resulting_principal": principal}
            )
            normalized.append(normalized_event)
            if event.id in replacements:
                replacements[event.id] = normalized_event
            for index, candidate in enumerate(inserted):
                if candidate.id == event.id:
                    inserted[index] = normalized_event
        if principal != request.corrected_master_principal:
            raise MarginPrincipalCorrectionError(
                f"corrected principal does not reconcile: {principal}"
            )
        for spec in request.event_updates:
            old = margin_by_date[spec.effective_date]
            corrected = replacements[old.id]
            if old != corrected:
                event_changes.append((old, corrected))
        corrected_events = [
            (
                next((value for value in normalized if value.id == item.id), item)
                if item.liability_id == MARGIN_ID
                else item
            )
            for item in combined
        ]
        corrected_master = margin.model_copy(
            update={"principal": request.corrected_master_principal}
        )
        daily_changes = self._daily_changes(request, corrected_events)
        history_changes = self._history_changes(request, corrected_events)
        interest_changes = self._interest_changes(
            request, original_events, corrected_events
        )
        annual_changes = self._annual_changes(request, interest_changes)
        position_fingerprint = self._position_fingerprint(
            request.first_affected_date, request.expected_latest_snapshot
        )
        payload = repr(
            (
                event_changes,
                inserted,
                margin,
                corrected_master,
                daily_changes,
                history_changes,
                interest_changes,
                annual_changes,
                position_fingerprint,
            )
        )
        already = (
            not event_changes
            and not inserted
            and margin == corrected_master
            and all(before == after for before, after in daily_changes)
            and all(before == after for before, after in history_changes)
            and all(before == after for before, after in interest_changes)
            and all(before == after for before, after in annual_changes)
        )
        return MarginPrincipalCorrectionPlan(
            sha256(payload.encode("utf-8")).hexdigest(),
            tuple(event_changes),
            tuple(inserted),
            (margin, corrected_master),
            tuple(daily_changes),
            tuple(history_changes),
            tuple(interest_changes),
            tuple(annual_changes),
            position_fingerprint,
            already,
        )

    def _daily_changes(
        self,
        request: MarginPrincipalCorrectionRequest,
        corrected_events: list[LiabilityPrincipalEvent],
    ) -> list[tuple[DailySnapshot, DailySnapshot]]:
        liabilities = self.liabilities.list_all()
        rows = self.daily.list_between_dates(
            request.first_affected_date, request.expected_latest_snapshot
        )
        previous = self.daily.get_highest_before(request.first_affected_date)
        high = previous.high_water_mark if previous else Decimal("0")
        result = []
        for old in rows:
            as_of = self.principal_engine.liabilities_as_of(
                liabilities, corrected_events, old.snapshot_date
            )
            debt = sum((item.principal for item in as_of), Decimal("0"))
            nav = old.total_market_value - debt
            high = max(high, nav)
            corrected = old.model_copy(
                update={
                    "total_liabilities": debt,
                    "net_asset_value": nav,
                    "leverage_ratio": (
                        debt / old.total_market_value
                        if old.total_market_value
                        else Decimal("0")
                    ),
                    "high_water_mark": high,
                    "drawdown": (nav - high) / high if high else Decimal("0"),
                }
            )
            result.append((old, corrected))
        return result

    def _history_changes(
        self,
        request: MarginPrincipalCorrectionRequest,
        corrected_events: list[LiabilityPrincipalEvent],
    ) -> list[tuple[HistoricalStockNetEquity, HistoricalStockNetEquity]]:
        liabilities = self.liabilities.list_all()
        result = []
        for old in self.history.list_between_dates(
            request.first_affected_date, request.expected_latest_snapshot
        ):
            if old.total_market_value is None:
                raise MarginPrincipalCorrectionError(
                    f"history market value is unavailable: {old.snapshot_date}"
                )
            as_of = {
                item.id: item.principal
                for item in self.principal_engine.liabilities_as_of(
                    liabilities, corrected_events, old.snapshot_date
                )
            }
            pledge = as_of[PLEDGE_ID]
            margin = as_of[MARGIN_ID]
            total = pledge + margin
            corrected = old.model_copy(
                update={
                    "pledge_debt": pledge,
                    "margin_debt": margin,
                    "total_liabilities": total,
                    "stock_net_equity": old.total_market_value - total,
                }
            )
            result.append((old, corrected))
        return result

    def _interest_changes(
        self,
        request: MarginPrincipalCorrectionRequest,
        original_events: list[LiabilityPrincipalEvent],
        corrected_events: list[LiabilityPrincipalEvent],
    ) -> list[tuple[InvestmentCostEvent, InvestmentCostEvent]]:
        costs = self.costs.list_between_dates(
            date(request.first_affected_date.year, 1, 1),
            request.expected_latest_snapshot,
        )
        by_id = {item.id: item for item in costs}
        catchup = by_id.get(CATCHUP_ID)
        if catchup is None:
            raise MarginPrincipalCorrectionError("margin interest catch-up is missing")
        adjustment = Decimal("0")
        current = request.first_affected_date
        while current <= date(2026, 8, 28):
            old = self.principal_engine.principal_as_of(
                [item for item in original_events if item.liability_id == MARGIN_ID],
                current,
            )
            new = self.principal_engine.principal_as_of(
                [item for item in corrected_events if item.liability_id == MARGIN_ID],
                current,
            )
            adjustment += (new - old) * ANNUAL_RATE / Decimal("365")
            current += timedelta(days=1)
        catchup_after = catchup.model_copy(
            update={"amount": catchup.amount + adjustment}
        )
        result = [(catchup, catchup_after)]
        current = date(2026, 8, 29)
        while current <= request.expected_latest_snapshot:
            event_id = financing_interest_event_id(MARGIN_ID, current)
            old = by_id.get(event_id)
            if old is None:
                raise MarginPrincipalCorrectionError(
                    f"margin interest event is missing: {current}"
                )
            principal = self.principal_engine.principal_as_of(
                [item for item in corrected_events if item.liability_id == MARGIN_ID],
                current,
            )
            amount = principal * ANNUAL_RATE / Decimal("365")
            corrected = old.model_copy(
                update={
                    "amount": amount,
                    "description": (
                        f"{MARGIN_ID}; {current.isoformat()}; principal={principal}; "
                        f"rate={ANNUAL_RATE}; Actual/365; v11 principal replay"
                    ),
                }
            )
            result.append((old, corrected))
            current += timedelta(days=1)
        return result

    def _annual_changes(
        self,
        request: MarginPrincipalCorrectionRequest,
        interest_changes: list[tuple[InvestmentCostEvent, InvestmentCostEvent]],
    ) -> list[tuple[AnnualPnlSnapshot, AnnualPnlSnapshot]]:
        changes = {after.id: (before, after) for before, after in interest_changes}
        result = []
        for old in self.annual.list_between_dates(
            date(2026, 8, 28), request.expected_latest_snapshot
        ):
            delta = sum(
                (
                    after.amount - before.amount
                    for before, after in changes.values()
                    if after.event_date <= old.snapshot_date
                ),
                Decimal("0"),
            )
            financing_cost = old.financing_cost_ytd + delta
            corrected = old.model_copy(
                update={
                    "financing_cost_ytd": financing_cost,
                    "total_pnl_ytd": (
                        old.realized_pnl_ytd
                        + old.unrealized_pnl
                        + old.dividend_income_ytd
                        - financing_cost
                        - old.other_cost_ytd
                    ),
                }
            )
            result.append((old, corrected))
        return result

    def _position_fingerprint(self, start: date, end: date) -> str:
        rows = self.repos.position_snapshots.list_between_dates(start, end)
        return sha256(repr(rows).encode("utf-8")).hexdigest()
