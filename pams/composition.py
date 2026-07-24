"""Concrete dependency composition for local PAMS commands."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from pydantic import SecretStr

from config import get_settings, load_logging_config
from core.constants import PROJECT_ROOT
from core.logging import configure_logging
from database import initialize_database, initialize_schema
from market_calendar import MarketCalendar, OfficialMarketDateProvider
from market_data.engine import MarketDataEngine
from market_data.providers import (
    HistoricalTPExProvider,
    HistoricalTWSEProvider,
    MarketDataProvider,
    TPExProvider,
    TWSEProvider,
)
from pams.application import (
    AddTransactionUseCase,
    AnalyzePortfolioUseCase,
    ApplyRebuiltHoldingsUseCase,
    AuthorizeMicrosoftEmailUseCase,
    DemoDataUseCase,
    ListTransactionsUseCase,
    PortfolioHistoryUseCase,
    PortfolioStatusUseCase,
    SendDailyReportUseCase,
    UpdatePortfolioUseCase,
    ValuatePortfolioUseCase,
    VerifySystemUseCase,
)
from pams.application.send_daily_report import EmailEnvelope
from pams.delivery import (
    DailyEmailReportRenderer,
    MicrosoftGraphAuthenticator,
    MicrosoftGraphEmailTransport,
    ResendEmailTransport,
    SMTPEmailTransport,
)
from pams.operations import OperationalStatusService, VerificationService
from repositories import (
    SQLiteHoldingRebuildUnitOfWork,
    SQLiteHoldingRepository,
    SQLiteLiabilityRepository,
    SQLiteMarketDataUnitOfWork,
    SQLitePositionSnapshotRepository,
    SQLitePriceQuoteRepository,
    SQLiteReportDeliveryRepository,
    SQLiteSnapshotRepository,
    SQLiteTransactionRepository,
)
from services import BootstrapService, HoldingProjectionMetadata, TransactionEngine


@dataclass(frozen=True)
class ApplicationContext:
    """Resources composed for one local CLI invocation."""

    connection: sqlite3.Connection
    engine: MarketDataEngine
    database_path: Path
    seeded: bool
    calendar: MarketCalendar
    status: OperationalStatusService
    verification: VerificationService
    update_portfolio: UpdatePortfolioUseCase
    portfolio_status: PortfolioStatusUseCase
    verify_system: VerifySystemUseCase
    portfolio_history: PortfolioHistoryUseCase | None = None
    apply_rebuilt_holdings: ApplyRebuiltHoldingsUseCase | None = None
    add_transaction: AddTransactionUseCase | None = None
    list_transactions: ListTransactionsUseCase | None = None
    valuate_portfolio: ValuatePortfolioUseCase | None = None
    analyze_portfolio: AnalyzePortfolioUseCase | None = None
    send_daily_report: SendDailyReportUseCase | None = None


class _DryRunEmailTransport:
    def send(self, envelope: EmailEnvelope) -> None:
        del envelope
        raise RuntimeError("dry-run transport must never be called")


def resolve_database_path(override: Path | None = None) -> Path:
    """Resolve an override or configured SQLite URL to an absolute path."""
    if override is not None:
        return override.expanduser().resolve()
    database_url = get_settings().database_url
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("CLI supports only sqlite:/// database URLs")
    raw_path = database_url.removeprefix(prefix)
    if not raw_path or raw_path == ":memory:":
        raise ValueError("CLI requires a file-backed SQLite database")
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def compose_demo_data() -> DemoDataUseCase:
    """Build the isolated demo-data workflow with production protection."""
    return DemoDataUseCase(resolve_database_path())


@contextmanager
def compose_application(
    database_override: Path | None = None,
    *,
    verbose: bool = False,
    providers: tuple[MarketDataProvider, ...] | None = None,
) -> Iterator[ApplicationContext]:
    """Initialize storage, bootstrap data, and wire a market-data engine."""
    with _compose(
        database_override,
        verbose=verbose,
        providers=providers,
        initialize=True,
        bootstrap=True,
    ) as context:
        yield context


@contextmanager
def compose_daily_report(
    database_override: Path | None = None,
    *,
    verbose: bool = False,
    dry_run: bool = False,
    providers: tuple[MarketDataProvider, ...] | None = None,
) -> Iterator[ApplicationContext]:
    """Compose updating, persisted-report, and configured email delivery."""
    with compose_application(
        database_override, verbose=verbose, providers=providers
    ) as context:
        settings = get_settings()
        required: dict[str, str | SecretStr | None] = {
            "PAMS_EMAIL_FROM": settings.email_from,
            "PAMS_EMAIL_TO": settings.email_to,
        }
        if not dry_run:
            if settings.email_transport == "microsoft_graph":
                required.update(
                    {
                        "PAMS_MICROSOFT_CLIENT_ID": settings.microsoft_client_id,
                        "PAMS_MICROSOFT_TENANT": settings.microsoft_tenant,
                        "PAMS_MICROSOFT_TOKEN_CACHE": (
                            str(settings.microsoft_token_cache)
                            if settings.microsoft_token_cache is not None
                            else None
                        ),
                    }
                )
            elif settings.email_transport == "resend":
                required["PAMS_RESEND_API_KEY"] = settings.resend_api_key
            else:
                required.update(
                    {
                        "PAMS_SMTP_HOST": settings.smtp_host,
                        "PAMS_SMTP_USERNAME": settings.smtp_username,
                        "PAMS_SMTP_PASSWORD": settings.smtp_password,
                    }
                )
        configured = _require_transport_configuration(
            _transport_display_name(settings.email_transport), required
        )
        sender = configured["PAMS_EMAIL_FROM"]
        recipient = configured["PAMS_EMAIL_TO"]
        if dry_run:
            transport = _DryRunEmailTransport()
        elif settings.email_transport == "microsoft_graph":
            transport = MicrosoftGraphEmailTransport(
                MicrosoftGraphAuthenticator(
                    configured["PAMS_MICROSOFT_CLIENT_ID"],
                    configured["PAMS_MICROSOFT_TENANT"],
                    _resolve_local_path(Path(configured["PAMS_MICROSOFT_TOKEN_CACHE"])),
                )
            )
        elif settings.email_transport == "resend":
            transport = ResendEmailTransport(configured["PAMS_RESEND_API_KEY"])
        else:
            transport = SMTPEmailTransport(
                configured["PAMS_SMTP_HOST"],
                settings.smtp_port,
                configured["PAMS_SMTP_USERNAME"],
                configured["PAMS_SMTP_PASSWORD"],
            )
        snapshots = SQLiteSnapshotRepository(context.connection)
        positions = SQLitePositionSnapshotRepository(context.connection)
        holdings = SQLiteHoldingRepository(context.connection)
        use_case = SendDailyReportUseCase(
            context.update_portfolio,
            snapshots,
            positions,
            holdings,
            SQLiteReportDeliveryRepository(context.connection),
            DailyEmailReportRenderer(),
            transport,
            sender,
            recipient,
        )
        yield replace(context, send_daily_report=use_case)


def compose_email_authorization() -> AuthorizeMicrosoftEmailUseCase:
    """Compose first-time Microsoft delegated device authorization."""
    settings = get_settings()
    if settings.email_transport != "microsoft_graph":
        raise ValueError(
            "PAMS_EMAIL_TRANSPORT must be microsoft_graph for Microsoft authorization"
        )
    configured = _require_transport_configuration(
        "Microsoft Graph",
        {
            "PAMS_MICROSOFT_CLIENT_ID": settings.microsoft_client_id,
            "PAMS_MICROSOFT_TENANT": settings.microsoft_tenant,
            "PAMS_MICROSOFT_TOKEN_CACHE": (
                str(settings.microsoft_token_cache)
                if settings.microsoft_token_cache is not None
                else None
            ),
        },
    )
    authenticator = MicrosoftGraphAuthenticator(
        configured["PAMS_MICROSOFT_CLIENT_ID"],
        configured["PAMS_MICROSOFT_TENANT"],
        _resolve_local_path(Path(configured["PAMS_MICROSOFT_TOKEN_CACHE"])),
    )
    return AuthorizeMicrosoftEmailUseCase(authenticator)


def _resolve_local_path(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = PROJECT_ROOT / expanded
    return expanded.resolve()


def selected_email_transport() -> str:
    """Return the selected adapter name without exposing credentials."""
    return get_settings().email_transport


def _transport_display_name(transport: str) -> str:
    return {
        "microsoft_graph": "Microsoft Graph",
        "resend": "Resend",
        "smtp": "SMTP",
    }[transport]


def _require_transport_configuration(
    transport_name: str,
    values: dict[str, str | SecretStr | None],
) -> dict[str, str]:
    configured: dict[str, str] = {}
    missing: list[str] = []
    for name, value in values.items():
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        if raw_value is None or not raw_value.strip():
            missing.append(name)
        else:
            configured[name] = raw_value.strip()
    if missing:
        raise ValueError(
            f"Missing configuration for {transport_name} transport:\n"
            + "\n".join(missing)
        )
    return configured


@contextmanager
def compose_operations(
    database_override: Path | None = None,
    *,
    verbose: bool = False,
    providers: tuple[MarketDataProvider, ...] | None = None,
) -> Iterator[ApplicationContext]:
    """Wire read-only operational commands without migration or bootstrap."""
    with _compose(
        database_override,
        verbose=verbose,
        providers=providers,
        initialize=False,
        bootstrap=False,
    ) as context:
        yield context


@contextmanager
def compose_ledger_operations(
    database_override: Path | None = None,
    *,
    verbose: bool = False,
    providers: tuple[MarketDataProvider, ...] | None = None,
) -> Iterator[ApplicationContext]:
    """Initialize schema for ledger writes without bootstrapping holdings."""
    with _compose(
        database_override,
        verbose=verbose,
        providers=providers,
        initialize=True,
        bootstrap=False,
    ) as context:
        yield context


@contextmanager
def _compose(
    database_override: Path | None,
    *,
    verbose: bool,
    providers: tuple[MarketDataProvider, ...] | None,
    initialize: bool,
    bootstrap: bool,
) -> Iterator[ApplicationContext]:
    logging_config = load_logging_config()
    settings = get_settings()
    configure_logging("DEBUG" if verbose else settings.log_level, logging_config.format)
    database_path = resolve_database_path(database_override)
    if not initialize and not database_path.exists():
        raise ValueError(
            f"Database does not exist: {database_path}. "
            "Run 'python -m pams demo-data' for a usable first-run database."
        )
    connection = initialize_database(f"sqlite:///{database_path.as_posix()}")
    try:
        if initialize:
            initialize_schema(connection)
        holdings = SQLiteHoldingRepository(connection)
        liabilities = SQLiteLiabilityRepository(connection)
        seeded = (
            BootstrapService(connection, holdings, liabilities).initialize()
            if bootstrap
            else False
        )
        market_providers = providers or (TWSEProvider(), TPExProvider())
        engine = MarketDataEngine(
            market_providers,
            holdings,
            liabilities,
            SQLiteMarketDataUnitOfWork(connection),
        )
        date_providers = tuple(
            OfficialMarketDateProvider(provider) for provider in market_providers
        )
        calendar = MarketCalendar(date_providers)
        status_service = OperationalStatusService(connection, database_path)
        verification_service = VerificationService(
            connection, database_path, date_providers, calendar, engine
        )
        snapshots = SQLiteSnapshotRepository(connection)
        position_snapshots = SQLitePositionSnapshotRepository(connection)
        quotes = SQLitePriceQuoteRepository(connection)
        transaction_repository = SQLiteTransactionRepository(connection)
        transaction_engine = TransactionEngine()
        valuate_portfolio = ValuatePortfolioUseCase(
            holdings,
            quotes,
            transactions=transaction_repository,
            transaction_engine=transaction_engine,
        )
        holding_metadata = {
            (holding.symbol, holding.market, holding.currency): (
                HoldingProjectionMetadata(holding.name, holding.holding_type)
            )
            for holding in holdings.list_all()
        }

        def historical_engine(trade_date: date) -> MarketDataEngine:
            return MarketDataEngine(
                (
                    HistoricalTWSEProvider(trade_date),
                    HistoricalTPExProvider(trade_date),
                ),
                holdings,
                liabilities,
                SQLiteMarketDataUnitOfWork(connection),
            )

        yield ApplicationContext(
            connection=connection,
            engine=engine,
            database_path=database_path,
            seeded=seeded,
            calendar=calendar,
            status=status_service,
            verification=verification_service,
            update_portfolio=UpdatePortfolioUseCase(
                calendar,
                engine,
                database_path,
                historical_engine,
                snapshots,
                transaction_repository,
                holdings,
                transaction_engine,
            ),
            portfolio_status=PortfolioStatusUseCase(
                calendar,
                status_service,
                holdings,
                snapshots,
                position_snapshots,
                quotes,
            ),
            verify_system=VerifySystemUseCase(verification_service),
            portfolio_history=PortfolioHistoryUseCase(snapshots),
            apply_rebuilt_holdings=ApplyRebuiltHoldingsUseCase(
                SQLiteHoldingRebuildUnitOfWork(connection), holding_metadata
            ),
            add_transaction=AddTransactionUseCase(transaction_repository),
            list_transactions=ListTransactionsUseCase(transaction_repository),
            valuate_portfolio=valuate_portfolio,
            analyze_portfolio=AnalyzePortfolioUseCase(snapshots),
        )
    finally:
        connection.close()
