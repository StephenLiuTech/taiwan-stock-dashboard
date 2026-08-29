"""Application orchestration for read-only broker reconciliation."""

from datetime import date
from hashlib import sha256
from pathlib import Path

from domain import Market
from pams.application.bootstrap_import import SECURITIES
from pams.brokerage import ReconciliationPlan, TaiwanBrokerCsvParser
from repositories import HoldingRepository, TransactionRepository
from services.broker_reconciliation import BrokerReconciliationEngine


class ReconcileBrokerUseCase:
    """Parse a source and compare it with the persisted ledger without writes."""

    def __init__(
        self,
        holdings: HoldingRepository,
        transactions: TransactionRepository,
        parser: TaiwanBrokerCsvParser | None = None,
        engine: BrokerReconciliationEngine | None = None,
    ) -> None:
        self.holdings = holdings
        self.transactions = transactions
        self.parser = parser or TaiwanBrokerCsvParser()
        self.engine = engine or BrokerReconciliationEngine()

    def execute(
        self,
        source: Path,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> ReconciliationPlan:
        if start_date and end_date and start_date > end_date:
            raise ValueError(
                "broker reconciliation start date must not exceed end date"
            )
        fingerprint = sha256(source.read_bytes()).hexdigest()
        transactions = self.transactions.list_all()
        securities: dict[str, tuple[str, Market]] = dict(SECURITIES)
        known_by_symbol = {
            item.symbol: item.market
            for item in [*transactions, *self.holdings.list_all()]
        }
        # Broker names require explicit known mappings; symbol-keyed evidence remains useful
        # for statements that use symbols in the name column.
        securities.update(
            {symbol: (symbol, market) for symbol, market in known_by_symbol.items()}
        )
        source_rows, records = self.parser.parse(
            source, securities, start_date=start_date, end_date=end_date
        )
        return self.engine.reconcile(fingerprint, source_rows, records, transactions)
