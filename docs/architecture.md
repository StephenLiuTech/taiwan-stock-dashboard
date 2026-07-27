# PAMS architecture

## Database-provider boundary

`PAMS_DATABASE_URL` is interpreted only by composition and infrastructure.
`sqlite:///` selects the preserved SQLite repository family;
`postgresql://` selects the PostgreSQL family. Application use cases and domain
engines receive the same repository and Unit of Work protocols in either case,
so no business rule branches on the configured database.

Schema creation and the existing schema-version history are idempotent for both
providers. Market ingestion and holding rebuilds use provider-specific atomic
transactions: SQLite retains `BEGIN IMMEDIATE`, while PostgreSQL uses its native
transaction.

SQLite-to-PostgreSQL migration is an Application Layer orchestration:

```text
SQLite source -> MigrateDatabaseUseCase -> PostgreSQL destination
```

It copies every persisted repository table plus schema-version metadata,
validates each destination row count, and commits once. The destination must be
empty to avoid merging independent ledgers. Failure rolls back all copied rows;
the SQLite source is read-only and is never deleted.

## Cloud automation boundary

The production scheduler is deployment infrastructure rather than business
logic:

```text
GitHub Actions schedule/manual dispatch
        â†“
PAMS CLI
        â†“
Application use cases
        â†“
Supabase PostgreSQL + official market providers + Resend
```

`.github/workflows/daily-report.yml` invokes `verify` and then the existing
`daily-report send` Application workflow. It does not separately update,
calculate, render, or deliver portfolio data. Scheduled runs use the normal
idempotent path; a Boolean manual input is the only route to the existing
explicit force behavior. A non-cancelling concurrency group prevents two
delivery jobs from executing simultaneously.

GitHub Actions secrets enter only as process environment variables. The
workflow has `contents: read` permission and no repository write capability.
Persistent application state remains in PostgreSQL; runners and dependency
caches hold no portfolio data.

## Architectural principles

PAMS uses inward-facing dependencies:

```text
CLI / Streamlit
       ↓
Application use cases + immutable DTOs
       ↓
Domain models + pure services + repository protocols
       ↓
SQLite repositories + official-market providers
```

- Domain services do not import Streamlit, CLI, SQLite, Pandas, Plotly, or HTTP.
- Application use cases own workflow orchestration and data loading.
- Entry points parse input and render returned DTOs.
- Repository adapters translate persistence records without calculating results.
- Money, prices, quantities, and ratios use `Decimal`.
- Dates use `date`; timestamps are timezone-aware.

`pams/composition.py` is the concrete dependency root. Importing a package does
not open a database connection or call a provider.

## Portfolio lifecycle

```text
Transactions
     ↓
TransactionEngine
     ↓
Holding change plan
     ↓ explicit atomic apply
Persisted holdings
     ↓
ValuatePortfolioUseCase ← latest persisted quotes
     ↓
ValuationEngine
     ↓
PortfolioValuation
  ├─ Portfolio CLI
  ├─ Dashboard 2.0
  └─ Daily Report Engine
```

### Transactions

`TransactionEngine` is a pure service. It deterministically orders BUY and SELL
records, maintains moving weighted-average cost per
`(symbol, market, currency)`, rejects oversells, and returns immutable active
positions and realized profit or loss.

`ApplyRebuiltHoldingsUseCase` produces an immutable change plan before any
write. Applying requires explicit authorization, non-empty transaction history,
and acknowledgement of unmatched bootstrap holdings. A dedicated unit of work
commits CREATE, UPDATE, and CLOSE operations atomically. Closing a holding sets
quantity and average cost to zero; it does not delete historical identity or
rewrite snapshots.

Operational update and current-valuation workflows load the complete
transaction ledger and call `TransactionEngine.project_current_holdings`.
Transaction-backed instruments replace their persisted seed projection;
persisted instruments without ledger history remain available. The projection
groups by `(symbol, market, currency)`, includes every same-day transaction,
and uses the engine's moving weighted-average cost method. The resulting
holdings are passed unchanged into market update, position snapshots, current
portfolio valuation, Dashboard, and daily-report snapshot rendering.

### Valuation

`ValuatePortfolioUseCase` loads holdings and each holding's latest matching
quote through repository protocols. An incomplete quote set raises
`MissingQuoteError`; stale prices are not silently reused.

`ValuationEngine` is the single source of truth for:

- cost basis
- market value
- unrealized profit or loss
- holding return
- portfolio totals and return

The engine accepts holdings and quotes and returns immutable
`HoldingValuation` and `PortfolioValuation` objects. It has no repository access.
It owns every current-valuation calculation, including Decimal portfolio
weights. `PortfolioValuation` is the canonical valuation contract consumed by
CLI, Dashboard, and Daily Reports.

`ValuatePortfolioUseCase` is the single production entry point for current
valuation. It loads holdings and quotes through repository protocols, rejects
an incomplete quote set, translates repository failures into typed Application
errors, and returns the engine result without modifying financial values.

The legacy `PortfolioService` delegates its overlapping calculations to
`ValuationEngine`, including position weights, preserving snapshot compatibility
without duplicating current-valuation formulas. Snapshot-only calculations such
as liabilities, leverage, and previous-close daily movement remain in that
workflow and are not part of `PortfolioValuation`.

## Market data

```text
Official TWSE / TPEx payloads
             ↓
Provider date verification
             ↓
Normalization and symbol completeness
             ↓
MarketDataEngine
             ↓ one SQLite transaction
Quotes + aggregate snapshot + position snapshots
```

For automatic updates, `MarketCalendar` reads the latest official date exposed
by each market and selects `min(TWSE, TPEx)` as the newest date jointly
available. Production resolution probes the official historical date-query
endpoints from the current date backward. It assumes neither weekends nor
holidays: only the typed absence of an official dataset advances to the prior
calendar date, while transport, structure, or source-date failures abort.
Date-provider results are not cached. Production automatic ingestion uses the
same date-bound historical providers for the selected common date, ensuring
the fetched dataset is exactly the one discovered by live resolution.
Injected/offline latest providers retain their existing test and adapter
contract. `synchronized` remains available as informational publication state.

Manual updates use date-bound historical providers. Both source dates must
match the requested date. The engine rejects wrong dates, mixed dates,
ambiguous freshness, missing requested symbols, suspended/no-trade securities,
provider failures, and duplicate snapshots. Prices are never relabeled.

`MarketDataEngine.preview()` performs the same retrieval, verification,
normalization, completeness, and valuation path without persistence.

## Persistence and snapshot grain

SQLite is the local adapter. Schema initialization is ordered, versioned, and
idempotent.

- `price_quotes`: one normalized symbol/market quote per trade date
- `daily_snapshots`: one aggregate portfolio row per snapshot date
- `position_snapshots`: one holding-level valuation row per holding and date

The aggregate and position snapshot grains are distinct. Rows are never copied
between these tables.

One explicit `BEGIN IMMEDIATE` unit of work surrounds quote upserts, the
aggregate snapshot, and all position snapshots for an ingestion run. Any
failure rolls back the complete run.

Normal repeated updates remain idempotent and do not call providers. An
explicit forced update uses `MarketDataEngine.rebuild`, leaving the regular
`refresh` duplicate guard intact. Rebuild computes transaction-derived holdings
first, then atomically replaces the selected date's complete quote set,
aggregate snapshot, and position snapshots. Forced daily-report delivery
invokes this rebuild before loading report facts, so it cannot intentionally
resend a stale persisted snapshot.

Historical snapshots are immutable during normal operation. Only the explicit
force workflow may replace a selected date after transaction-ledger correction.

## Dashboard boundary

`app.py` composes `ValuatePortfolioUseCase` for Streamlit. Dashboard 2.0
executes it once per cached page load and reuses the returned
`PortfolioValuation` for every section:

- Portfolio Summary
- Largest Positions
- Allocation
- Top Winners and Top Losers
- Full Portfolio Table

Dashboard modules import application DTOs and use cases only. They do not access
repositories, SQL, providers, the market-data engine, or valuation formulas.
Sorting and formatting are presentation concerns; displayed financial values
originate from the application DTO.

## Daily report boundary

```text
PortfolioValuation
        ↓
DailyReportBuilder
        ↓
DailyReport
   ┌────┴────┐
   ↓         ↓
Markdown    HTML
```

`DailyReportBuilder` selects and deterministically orders valuation DTO values
into a presentation-neutral `DailyReport`. It has no CLI, dashboard,
repository, or SQL dependency.

`MarkdownReportRenderer` and `HtmlReportRenderer` are independent. The HTML
renderer produces standalone semantic HTML with no JavaScript or external CSS.
CLI owns terminal output and UTF-8 file writing.

The former single reporting module is retained as
`pams.reporting.legacy`; its functions are re-exported by the package for
backward compatibility.

## Analytics boundary

```text
Aggregate DailySnapshots
          ↓
    AnalyticsEngine
          ↓
   PortfolioAnalytics
```

`AnalyticsEngine` is a pure service over aggregate daily snapshots. It orders
inputs chronologically and treats `DailySnapshot.net_asset_value` as the total
portfolio value after that day's transactions and cash movements. It returns
immutable Decimal-based period totals, consecutive daily returns, peak, trough,
and running-peak maximum drawdown.

The v0.9 Sprint 1 foundation deliberately does not infer deposits,
withdrawals, dividends, or benchmarks and does not implement TWR, MWR, IRR,
XIRR, volatility, Sharpe Ratio, or benchmark comparison. It has no repository,
application, CLI, dashboard, or chart dependency.

`AnalyzePortfolioUseCase` is the integration boundary. It validates an optional
inclusive start/end period, loads aggregate snapshots through the existing
`SnapshotRepository.list_between_dates` contract, translates empty history,
invalid periods, repository failures, and engine input errors into typed
application exceptions, then returns `PortfolioAnalytics`. Repository access
does not enter the engine.

The CLI route `analytics portfolio` parses `--from`, `--to`, and `--json`,
invokes the composed use case once, and renders the returned DTO. Human and
JSON renderers do not calculate performance.

Sprint 3 extends the same application boundary to every existing presentation:

```text
                  Presentation
        ┌──────────────┼──────────────┐
        ↓              ↓              ↓
       CLI         Dashboard       Reports
        └──────────────┼──────────────┘
                       ↓
            AnalyzePortfolioUseCase
                       ↓
                 AnalyticsEngine
                       ↓
             PortfolioAnalytics
```

The Dashboard receives the composed use case, executes it once per cached page
load, and maps returned metrics and daily observations to Streamlit elements.
Markdown and HTML report generation invokes the same use case and attaches the
returned DTO to the existing `DailyReport`. Missing or invalid analytics remain
distinct controlled states rather than zero values.

`pams.analytics_reporting` is the shared Presentation mapper. It formats dates,
Decimal amounts, and percentages without deriving financial results. Decimal
daily returns are converted to `float` only inside the Plotly chart construction
boundary. Presentation modules do not import `AnalyticsEngine`, query snapshot
repositories, execute analytics SQL, or calculate return or drawdown.

This integration adds no analytics methodology. Cash-flow adjustment, TWR,
MWR, IRR, XIRR, volatility, risk ratios, allocation analytics, and benchmarks
remain out of scope.

## Operational boundaries

The CLI follows:

```text
parse arguments → call composed use case → render DTO → exit
```

`status` reads local operational state. `verify` checks configuration,
database/schema health, portfolio inputs, official endpoint reachability,
market availability, and dependency composition. Source publication
disagreement is a warning rather than data corruption.

Demo-data generation is isolated from production configuration. It creates a
complete deterministic SQLite database transactionally and never calls live
providers. Demo quotes are marked `demo_fixture`.

## v1.0 deployment boundary

PAMS v1.0 is a local, single-user application installed from the repository.
The default adapter is file-backed SQLite. `demo-data` is the supported
first-run workflow and produces an isolated database without provider access.
Read-only composition rejects a missing database instead of creating an
uninitialized file.

Expected missing-data states are typed Application outcomes: no holdings,
missing quotes, and no analytics snapshots are not represented as valid zero
financial values.

## Out of scope for v1.0.0

- scheduling and background jobs
- Telegram and other delivery channels
- broker imports and corporate actions
- authentication and multi-user access
- cloud persistence
- allocation and benchmark analytics
- TWR, MWR, IRR, XIRR, and risk ratios
- multi-asset and FX conversion

Future adapters should implement existing repository protocols and preserve
transaction, Decimal, source-date, and snapshot-grain semantics.
## Daily report delivery

```text
UpdatePortfolioUseCase (automatic mode only)
                ↓
Persisted aggregate + position snapshots
                ↓
SendDailyReportUseCase
        ┌───────┴────────┐
        ↓                ↓
Email renderer    Delivery repository claim
        └───────┬────────┘
                ↓
       EmailTransport protocol
     ┌──────────┬──────────┐
     ↓          ↓          ↓
  Resend   Microsoft Graph  SMTP
 REST API  delegated OAuth2 fallback
```

Explicit dates require an exact aggregate snapshot. Automatic delivery reuses
the idempotent update workflow and loads the exact live-resolved date returned
by that workflow; it never substitutes `SnapshotRepository.get_latest()`.
Forced delivery rebuilds that resolved date. Normal delivery reuses its
snapshot or creates it when absent. `report_deliveries` has one row per report
type, date, and recipient;
an atomic `SENDING` claim prevents concurrent duplicate sends. SENT is a normal
no-op, FAILED is retryable, and dry-run performs neither a claim,
authentication, nor a network call.

The V1.0 renderer receives two additional application-prepared facts. Signed
daily portfolio movement is calculated in the pure `PortfolioService` by
aggregating the already-persisted per-position `daily_value_change` values and
dividing by their derived previous market value. The application use case
loads at most the latest 30 aggregate snapshots through `SnapshotRepository`;
it does not recalculate holdings or valuation.

The same service returns immutable per-position daily contribution facts:
amount, return against previous position market value, and share of net
portfolio daily P/L when the net total is non-zero. The email renderer ranks
these facts by absolute monetary impact; it never ranks portfolio impact from
price-change percentage alone.

At the presentation boundary, `DailyEmailReportRenderer` maps those immutable
Decimal values to plain text and an email chart. Multiple snapshots produce a
local PNG attached with a CID reference; one snapshot produces a controlled
fallback message. SMTP and Microsoft Graph share MIME construction, and Resend
uses its equivalent CID attachment payload. No report image is hosted
externally, and financial values remain Decimal until chart pixel mapping.
The MIME-related PNG is inline-only and has no attachment filename in SMTP or
Graph messages, minimizing downloadable attachment previews. Its high-resolution
source is responsively constrained by email-safe inline HTML attributes.

The `EmailTransport` protocol keeps delivery infrastructure outside the
application workflow. Personal installations default to the Resend REST
adapter, which sends the existing plain-text and HTML report representations
using an environment-provided secret API key. Resend authentication, HTTP, and
response handling remain infrastructure concerns and do not change delivery
claims or report construction.

Enterprise Microsoft delivery uses a
public-client MSAL device-code flow with tenant `consumers`. First-time
interactive authorization is isolated behind
`AuthorizeMicrosoftEmailUseCase`; normal delivery acquires or refreshes a
delegated token silently from a locally persisted, ignored cache. The Graph
adapter sends multipart/alternative MIME through `/me/sendMail`. Only
`Mail.Send` plus MSAL's reserved `openid`, `profile`, and `offline_access`
scopes are involved. Tokens and authorization headers never enter application
DTOs or logs, and this flow uses no client secret.

Authenticated STARTTLS SMTP remains an optional infrastructure adapter for
non-Microsoft providers and is selected explicitly through environment-backed
configuration.
