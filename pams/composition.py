"""Concrete dependency composition for local PAMS commands."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from pydantic import SecretStr

from config import get_settings, load_logging_config
from core.constants import PROJECT_ROOT
from core.logging import configure_logging
from database.provider import (
    database_url_for_override,
    open_database,
    resolve_sqlite_path,
)
from domain import Market
from market_calendar import (
    MarketCalendar,
    OfficialHistoricalMarketDateProvider,
    OfficialMarketDateProvider,
)
from market_data.dividend_payments import (
    MOPSDividendPaymentProvider,
    OfficialDividendPaymentProvider,
)
from market_data.dividends import (
    CompositeDividendSource,
    OfficialDividendProvider,
    TPExDividendProvider,
    TPExHistoricalDividendProvider,
    TWSEDividendProvider,
    TWSEHistoricalDividendProvider,
)
from market_data.engine import MarketDataEngine
from market_data.providers import (
    HistoricalTPExProvider,
    HistoricalTWSEProvider,
    MarketDataProvider,
    TPExProvider,
    TWSEProvider,
)
from market_data.transport import (
    UrllibJSONDocumentTransport,
    UrllibJSONTransport,
    UrllibTextFormTransport,
)
from pams.application import (
    AddTransactionUseCase,
    AnalyzePortfolioUseCase,
    ApplyRebuiltHoldingsUseCase,
    AuthorizeMicrosoftEmailUseCase,
    BootstrapImportUseCase,
    BuildReportSectionsUseCase,
    DemoDataUseCase,
    DividendEventUseCase,
    ListTransactionsUseCase,
    MigrateDatabaseUseCase,
    PortfolioHistoryUseCase,
    PortfolioStatusUseCase,
    QueryHoldingsUseCase,
    ReportSectionSettings,
    SendDailyReportUseCase,
    UpdatePortfolioUseCase,
    ValuatePortfolioUseCase,
    VerifySystemUseCase,
    WatchlistUseCase,
)
from pams.application.send_daily_report import EmailEnvelope
from pams.delivery import (
    DailyEmailReportRenderer,
    MicrosoftGraphAuthenticator,
    MicrosoftGraphEmailTransport,
    ResendEmailTransport,
    SMTPEmailTransport,
    SupabaseReportAssetStore,
)
from pams.operations import OperationalStatusService, VerificationService
from repositories.provider import RepositoryBundle, create_repositories
from services import BootstrapService, HoldingProjectionMetadata, TransactionEngine


@dataclass(frozen=True)
class ApplicationContext:
    """Resources composed for one local CLI invocation."""

    connection: object
    engine: MarketDataEngine
    database_path: Path
    seeded: bool
    calendar: MarketCalendar
    status: OperationalStatusService
    verification: VerificationService
    update_portfolio: UpdatePortfolioUseCase
    portfolio_status: PortfolioStatusUseCase
    verify_system: VerifySystemUseCase
    repositories: RepositoryBundle | None = None
    portfolio_history: PortfolioHistoryUseCase | None = None
    apply_rebuilt_holdings: ApplyRebuiltHoldingsUseCase | None = None
    add_transaction: AddTransactionUseCase | None = None
    list_transactions: ListTransactionsUseCase | None = None
    query_holdings: QueryHoldingsUseCase | None = None
    valuate_portfolio: ValuatePortfolioUseCase | None = None
    analyze_portfolio: AnalyzePortfolioUseCase | None = None
    send_daily_report: SendDailyReportUseCase | None = None
    watchlist: WatchlistUseCase | None = None
    dividend_events: DividendEventUseCase | None = None
    bootstrap_import: BootstrapImportUseCase | None = None


class _DryRunEmailTransport:
    def send(self, envelope: EmailEnvelope) -> None:
        del envelope
        raise RuntimeError("dry-run transport must never be called")


def resolve_database_path(override: Path | None = None) -> Path:
    """Resolve an override or configured SQLite URL to an absolute path."""
    if override is not None:
        return override.expanduser().resolve()
    return resolve_sqlite_path(get_settings().database_url)


def compose_demo_data() -> DemoDataUseCase:
    """Build the isolated demo-data workflow with production protection."""
    database_url = get_settings().database_url
    production_location = (
        resolve_sqlite_path(database_url)
        if database_url.startswith("sqlite:///")
        else Path("PostgreSQL")
    )
    return DemoDataUseCase(production_location)


def compose_database_migration() -> MigrateDatabaseUseCase:
    """Compose the explicitly configured SQLite-to-PostgreSQL migration."""
    settings = get_settings()
    if not settings.migration_source_url:
        raise ValueError("PAMS_MIGRATION_SOURCE_URL is required for database migration")
    return MigrateDatabaseUseCase(settings.migration_source_url, settings.database_url)


@contextmanager
def compose_bootstrap_import(
    database_override: Path | None = None,
) -> Iterator[BootstrapImportUseCase]:
    """Compose broker reconciliation without schema or portfolio writes."""
    settings = get_settings()
    database_url = database_url_for_override(database_override, settings.database_url)
    database = open_database(database_url)
    if not database.exists():
        raise ValueError(f"Database does not exist: {database.location}")
    connection = database.connection
    try:
        repositories = create_repositories(database.backend, connection)
        yield BootstrapImportUseCase(
            repositories.holdings,
            repositories.transactions,
            TransactionEngine(),
            repositories.holding_rebuild_uow,
            database.display_url,
        )
    finally:
        connection.close()


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
                required.update(
                    {
                        "PAMS_RESEND_API_KEY": settings.resend_api_key,
                        "PAMS_SUPABASE_URL": settings.supabase_url,
                        "PAMS_SUPABASE_SERVICE_ROLE_KEY": (
                            settings.supabase_service_role_key
                        ),
                        "PAMS_REPORT_ASSET_BUCKET": settings.report_asset_bucket,
                    }
                )
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
        asset_store = None
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
            asset_store = SupabaseReportAssetStore(
                configured["PAMS_SUPABASE_URL"],
                configured["PAMS_SUPABASE_SERVICE_ROLE_KEY"],
                configured["PAMS_REPORT_ASSET_BUCKET"],
                settings.report_asset_prefix,
            )
        else:
            transport = SMTPEmailTransport(
                configured["PAMS_SMTP_HOST"],
                settings.smtp_port,
                configured["PAMS_SMTP_USERNAME"],
                configured["PAMS_SMTP_PASSWORD"],
            )
        if context.repositories is None:
            raise RuntimeError("database repositories are not composed")
        use_case = SendDailyReportUseCase(
            context.update_portfolio,
            context.repositories.daily_snapshots,
            context.repositories.position_snapshots,
            context.repositories.holdings,
            context.repositories.report_deliveries,
            DailyEmailReportRenderer(),
            transport,
            sender,
            recipient,
            asset_store,
            BuildReportSectionsUseCase(
                context.repositories.holdings,
                context.repositories.transactions,
                context.repositories.dividend_events,
                context.repositories.price_quotes,
                context.repositories.watchlist,
                ReportSectionSettings(
                    show_allocation=settings.report_show_allocation,
                    show_market_snapshot=settings.report_show_market_snapshot,
                    show_upcoming_events=settings.report_show_upcoming_events,
                    show_dividends=settings.report_show_dividends,
                    show_ai_news=settings.report_show_ai_news,
                    show_semiconductor_news=settings.report_show_semiconductor_news,
                    show_insights=settings.report_show_insights,
                    show_risk=settings.report_show_risk,
                    show_watchlist=settings.report_show_watchlist,
                    show_transactions=settings.report_show_transactions,
                    event_horizon_days=settings.report_event_horizon_days,
                    dividend_scope=settings.report_dividend_scope,
                    hide_empty_optional_sections=(
                        settings.report_hide_empty_optional_sections
                    ),
                    news_limit=settings.report_news_limit,
                    risk_single_holding_warning=(
                        settings.risk_single_holding_warning_pct / 100
                    ),
                    risk_top3_warning=settings.risk_top3_warning_pct / 100,
                    risk_market_warning=settings.risk_market_warning_pct / 100,
                ),
            ),
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
    database_url = database_url_for_override(database_override, settings.database_url)
    if (
        not initialize
        and database_url.startswith("sqlite:///")
        and not resolve_sqlite_path(database_url).exists()
    ):
        missing_path = resolve_sqlite_path(database_url)
        raise ValueError(
            f"Database does not exist: {missing_path}. "
            "Run 'python -m pams demo-data' for a usable first-run database."
        )
    database = open_database(database_url)
    database_path = database.location
    if not initialize and not database.exists():
        raise ValueError(
            f"Database does not exist: {database_path}. "
            "Run 'python -m pams demo-data' for a usable first-run database."
        )
    connection = database.connection
    try:
        if initialize or database.backend == "postgresql":
            database.initialize_schema()
        repositories = create_repositories(database.backend, connection)
        holdings = repositories.holdings
        liabilities = repositories.liabilities
        seeded = (
            BootstrapService(connection, holdings, liabilities).initialize()
            if bootstrap
            else False
        )
        using_default_providers = providers is None

        def latest_transport(market: Market) -> UrllibJSONTransport:
            return UrllibJSONTransport(
                timeout_seconds=settings.market_http_timeout_seconds,
                attempts=settings.market_http_attempts,
                provider_name=market.value,
            )

        def historical_twse(trade_date: date) -> HistoricalTWSEProvider:
            return HistoricalTWSEProvider(
                trade_date,
                UrllibJSONDocumentTransport(
                    timeout_seconds=settings.market_http_timeout_seconds,
                    attempts=settings.market_http_attempts,
                    provider_name=Market.TWSE.value,
                ),
            )

        def historical_tpex(trade_date: date) -> HistoricalTPExProvider:
            return HistoricalTPExProvider(
                trade_date,
                UrllibJSONDocumentTransport(
                    timeout_seconds=settings.market_http_timeout_seconds,
                    attempts=settings.market_http_attempts,
                    provider_name=Market.TPEX.value,
                ),
            )

        market_providers = providers or (
            TWSEProvider(latest_transport(Market.TWSE)),
            TPExProvider(latest_transport(Market.TPEX)),
        )
        engine = MarketDataEngine(
            market_providers,
            holdings,
            liabilities,
            repositories.market_data_uow,
        )
        dividend_provider = OfficialDividendProvider(
            CompositeDividendSource(
                TWSEDividendProvider(latest_transport(Market.TWSE)),
                TWSEHistoricalDividendProvider(
                    UrllibJSONDocumentTransport(
                        timeout_seconds=settings.market_http_timeout_seconds,
                        attempts=settings.market_http_attempts,
                        provider_name="TWSE dividends",
                    )
                ),
            ),
            CompositeDividendSource(
                TPExDividendProvider(latest_transport(Market.TPEX)),
                TPExHistoricalDividendProvider(
                    UrllibJSONDocumentTransport(
                        timeout_seconds=settings.market_http_timeout_seconds,
                        attempts=settings.market_http_attempts,
                        provider_name="TPEx dividends",
                    )
                ),
            ),
        )
        payment_transport = UrllibTextFormTransport(
            timeout_seconds=settings.market_http_timeout_seconds,
            attempts=settings.market_http_attempts,
            provider_name="MOPS dividend payments",
        )
        dividend_payment_provider = OfficialDividendPaymentProvider(
            MOPSDividendPaymentProvider(Market.TWSE, payment_transport),
            MOPSDividendPaymentProvider(Market.TPEX, payment_transport),
        )
        if using_default_providers:
            date_providers = (
                OfficialHistoricalMarketDateProvider(Market.TWSE, historical_twse),
                OfficialHistoricalMarketDateProvider(Market.TPEX, historical_tpex),
            )
        else:
            date_providers = tuple(
                OfficialMarketDateProvider(provider) for provider in market_providers
            )
        calendar = MarketCalendar(date_providers)
        status_service = OperationalStatusService(
            connection, database_path, repositories, database.size_bytes
        )
        verification_service = VerificationService(
            connection, database_path, date_providers, calendar, engine
        )
        snapshots = repositories.daily_snapshots
        position_snapshots = repositories.position_snapshots
        quotes = repositories.price_quotes
        transaction_repository = repositories.transactions
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
                    historical_twse(trade_date),
                    historical_tpex(trade_date),
                ),
                holdings,
                liabilities,
                repositories.market_data_uow,
            )

        yield ApplicationContext(
            connection=connection,
            repositories=repositories,
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
                prefer_historical_for_automatic=using_default_providers,
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
                repositories.holding_rebuild_uow, holding_metadata
            ),
            add_transaction=AddTransactionUseCase(transaction_repository),
            list_transactions=ListTransactionsUseCase(transaction_repository),
            query_holdings=QueryHoldingsUseCase(
                transaction_repository,
                holdings,
                quotes,
                transaction_engine,
            ),
            valuate_portfolio=valuate_portfolio,
            analyze_portfolio=AnalyzePortfolioUseCase(
                snapshots,
                transactions=transaction_repository,
                transaction_engine=transaction_engine,
            ),
            watchlist=WatchlistUseCase(repositories.watchlist, quotes),
            dividend_events=DividendEventUseCase(
                dividend_provider,
                repositories.dividend_events,
                holdings,
                dividend_payment_provider,
            ),
            bootstrap_import=BootstrapImportUseCase(
                holdings, transaction_repository, transaction_engine
            ),
        )
    finally:
        connection.close()
