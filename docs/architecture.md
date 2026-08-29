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

`TransactionEngine` is a pure service. Holdings are effective by `trade_date`.
Because the current model does not preserve true intraday chronology, ledger
reconstruction orders records by `(trade_date, BUY-before-SELL priority,
transaction id)`. The ID is only a deterministic tie-breaker among transactions
on the same date and side; it does not represent chronology. Settlement date
does not alter portfolio-effective ordering. A future true intraday model would
require an explicit sequence or timestamp field. The engine maintains moving
weighted-average cost per
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

`trade_date` is the portfolio-effective transaction date. `settlement_date`
remains persisted for compatibility and deterministic record ordering, but an
omitted value defaults to `trade_date` and never delays holdings, valuation,
history, or reporting.

`QueryHoldingsUseCase` is the read boundary for `holdings list` and `holdings
show`. It loads the complete transaction ledger, asks `TransactionEngine` for
active transaction-derived holdings, loads their latest quotes, and delegates
all financial values to `ValuationEngine`. It returns immutable query DTOs;
the CLI only formats them. Persisted bootstrap-only holdings are intentionally
excluded from these ledger query commands. Invalid histories, including a sell
before a buy or an oversell, are rejected; short positions are unsupported. A
holding without a latest quote remains queryable with quantity and canonical
cost basis, while price and market-dependent fields render as unavailable.
An optional inclusive `as_of` cutoff filters ledger events by `trade_date <=
as_of`; settlement date remains irrelevant. Historical valuation selects each
instrument's latest persisted quote whose trade date is not later than the
cutoff and exposes that quote date in the immutable result. Later quotes are
never substituted, and a missing eligible quote leaves only market-dependent
fields unavailable. The same deterministic day-level ordering and oversell
rules apply to the filtered ledger.

PAMS follows the broker portfolio cost convention. BUY quantity multiplied by
execution price enters moving-average holding cost; BUY fees and taxes do not.
SELL transactions reduce quantity at the unchanged moving average. SELL fees
and taxes reduce realized profit. BUY fees, SELL fees, and taxes remain
immutable ledger expenses and are reported separately; they never enter
holding cost, unrealized profit or loss, or holding return.

### Margin-financed transaction boundary

```text
CLI transaction add --financing margin
        ↓
AddTransactionUseCase
        ↓
MarginFinancingService (Decimal rules)
        ↓ one Unit of Work
Transaction + rebuilt Holding + margin Liability
```

Margin financing is an explicit nullable transaction classification, not a
note convention. Schema version 8 also stores the financed symbol and quantity
on the margin liability. The pure domain service applies the configured
self-funding ratio to `quantity × price`; fees and taxes retain their existing
expense semantics and never enter financed principal. The application workflow
rejects duplicates before writing and commits transaction, holding projection,
and liability together. Existing descriptive liability metadata remains
read-compatible during migration and accrued-interest notes are preserved.

### Liability principal event ledger

Schema version 11 adds immutable `liability_principal_events`. A pure
`LiabilityPrincipalEngine` starts from zero and replays each liability account
by `(effective_date, sequence, id)`. The sequence is explicit accounting order;
timestamps and IDs never imply chronology. Events are effective inclusively on
their date, so they change that date's opening/accruable principal. Replay
rejects negative principal and validates optional resulting-principal facts.

The margin liability represents the brokerage margin debt account. Its
financed-symbol fields remain a current-position summary; historical references
can identify fully repaid instrument exposure without creating a false current
liability. This ledger does not calculate interest.

The one-time bootstrap importer has a read-only preview and an explicit apply
boundary. Apply replaces only the reconciled 2026 ledger, writes deterministic
statement and synthetic transaction IDs, rebuilds holdings, and performs a
persisted post-write reconciliation inside one database transaction. A failed
write or quantity/cost mismatch rolls back everything. An equivalent imported
ledger and matching holdings produce an idempotent no-op.

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

TPEx same-day and historical publications have separate adapters. The current
adapter uses TPEx's official OpenAPI
`/openapi/v1/tpex_mainboard_daily_close_quotes`, whose structured rows expose
the publication date, security code/name, close, and signed change. For the
local current date, date-aware ingestion tries that completed-close publication
first and accepts it only when it is non-empty, single-date, exactly dated, and
contains every active TPEx holding. The explicit-date `dailyQuotes` adapter
remains authoritative for prior dates and is the same-day fallback only when it
actually returns that exact date. A stale OpenAPI publication or an empty
same-day historical response therefore leaves TPEx unavailable rather than
relabeling the prior session.

Automatic calendar resolution uses the current TPEx OpenAPI publication date,
while TWSE retains official newest-date discovery. Their minimum remains the
common ingestible date. The OpenAPI feed can itself publish after market close
with an operational delay; until its `Date` advances and the required rows are
present, PAMS deliberately remains on the prior common date.

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

Strict verification retains every failed check as fatal. The explicit
`--allow-market-source-warning` production policy is applied by
`VerifySystemUseCase`: only failed TWSE endpoint, TPEx endpoint, and Market
Calendar checks are converted to warnings. Configuration, database, schema,
holdings, liabilities, and Market Data Engine composition remain fatal. The
underlying verification service retains its original probe results, and actual
market ingestion continues to enforce source-date integrity independently.

Latest and historical official providers share one HTTP transport boundary.
It fully buffers response bytes before JSON decoding and applies at most four
attempts to incomplete reads, transient socket failures, and HTTP
429/500/502/503/504/520. Parsing, dataset semantics, and source-date validation
occur after that boundary and are never retried as transport failures. All
retries therefore finish before the Market Data Engine opens its atomic
persistence transaction.

TWSE and TPEx availability probes are isolated. A transient failure after the
bounded retry policy produces an unavailable result for only that market and
retains the other market's verified live date. Automatic ingestion still
requires both dates and never fabricates a common date. Daily Report delivery
may instead use the latest non-future persisted aggregate snapshot only when
its position-snapshot grain covers every active holding exactly once. The
actual persisted snapshot date remains the report date; stale data is never
relabeled as the current day. Structural payload errors, source-date failures,
and other data-integrity errors remain fatal and are not eligible for this
transport fallback.

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
Forced delivery rebuilds a live-resolved date when an update is required; a
complete current-date snapshot or temporary-calendar fallback is not rebuilt
without verified live availability. Normal delivery reuses its snapshot or
creates it when absent. `report_deliveries` has one row per report
type, date, and recipient;
an atomic `SENDING` claim prevents concurrent duplicate sends. SENT is a normal
no-op, FAILED is retryable, and dry-run performs neither a claim,
authentication, nor a network call.

If a complete snapshot already exists for the current date, automatic Daily
Report delivery does not depend on another live calendar probe merely to render
and send persisted facts. If live calendar resolution is temporarily
unavailable and no current-date snapshot exists, delivery may use the latest
complete non-future snapshot and labels the report with that exact persisted
date. If no complete persisted valuation exists, delivery fails explicitly.

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
local PNG; one snapshot produces a controlled fallback message. A
transport-neutral `ChartSource` selects either a CID URI with an inline image
or an HTTPS URI without an attachment. Financial values remain Decimal until
chart pixel mapping.

SMTP and Microsoft Graph share PAMS-controlled MIME construction. Their PNG is
inline-only, has a matching CID, and has no attachment filename. Resend does
not receive CID data or attachments. `SendDailyReportUseCase` publishes the
generated PNG through the `ReportAssetStore` protocol, rerenders with the
returned HTTPS URL, and only then invokes Resend. Supabase Storage is one
replaceable infrastructure implementation; business and portfolio services do
not depend on it.

Supabase objects are upserted at a stable, deployment-prefixed path per report
date. Storage failures enter the existing typed retryable delivery path and
prevent a broken email from being sent. Public URLs are intentionally
unguessable by prefix but remain accessible to anyone who obtains the URL.

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

## Modular daily-report sections

```text
Repositories / optional providers
              |
              v
 BuildReportSectionsUseCase
              |
              +--> pure ReportSectionService / NewsService
              |
              v
 immutable DailyReportSections
              |
              v
 DailyReportSectionRenderer (HTML + text)
```

Core portfolio construction remains in the existing valuation/report path.
Section orchestration loads data through repository protocols; calculations
are deterministic services; renderers only format immutable DTOs. Optional
market, event, and news providers are explicit protocols. Their failures are
logged by section and exception type, converted to unavailable states, and do
not fail core delivery. No provider payload or credential is logged.

`watchlist` is the only new persisted grain: one row per `(symbol, market)`.
Schema version 5 adds the same table to SQLite and PostgreSQL and includes it in
transactional migration. Dividend eligibility is reconstructed from ledger
transactions effective on the ex-dividend date. Transaction summaries use
`trade_date`, never `settlement_date`.

## Official dividend calendar

```text
TWSE / TPEx event adapters + MOPS payment-date adapter
          ↓
NormalizeDividendEventsUseCase
          ↓
DividendEventRepository (SQLite/PostgreSQL)
          ↓
BuildReportSectionsUseCase
          ↓
HTML and plain-text Dividend Calendar
```

Schema version 6 adds `dividend_events`, one row per deterministic official
distribution key. It remains separate from the legacy `dividends` financial
event table because issuers may publish multiple distributions per year and
official payment dates can arrive later. TWSE `TWT48U_ALL`/`TWT49U` and TPEx
`tpex_exright_prepost`/`exDailyQ` adapters include ETF rows. The official MOPS
`ajax_t108sb27` company dividend report is an isolated payment-date adapter. It
enriches only one unique event matching market, normalized symbol, and
ex-dividend date; it never joins solely on symbol/year. Provider failure is
logged and isolated, so valid event updates and known payment dates remain.

Normalization validates ROC dates and Decimal amounts before persistence.
Eligibility delegates to the Transaction Engine using
`trade_date < ex_dividend_date`, the explicit day-level final-eligible-position
assumption. Status is derived from report date, ex-date, and official payment
date and is not persisted. `Paid` means only that the official payment date has
passed. The report defaults to the full current calendar year and also supports
`next_90_days` and `all`; unavailable payment dates are never inferred.

`Actual Cash Received` is another non-persisted deterministic report fact. For
an available estimate it equals estimated eligible quantity times official
cash dividend per share only when `payment_date <= report_date`; otherwise it
is zero. An unavailable estimate produces `N/A`. Actual Cash Received means the
dividend is expected to have been paid according to the official payment date.
It does not verify actual broker settlement.

## Multi-market valuation boundary

`GlobalMarketDataEngine` coordinates the existing official Taiwan engine with
separate `USMarketDataProvider` and `FXRateProvider` ports. Vendor parsing stays
in adapters. The pure `MultiCurrencyValuationEngine` uses `Decimal` and constant
report-date USD/TWD translation. Repositories persist quote and FX provenance.
Taiwan-only portfolios retain the existing flow. The orchestrator requests
each distinct active US symbol once and USD/TWD once. Optional-provider
failures are isolated from Taiwan ingestion: eligible non-future persisted US
quotes and FX may be reused, and failures never delete persisted market data.
Without an eligible fallback the affected US translated fields remain
unavailable rather than fabricated, while the Taiwan snapshot stays valid.
All values selected for a successful snapshot are written at one atomic
quote/rate/snapshot boundary.

`Market.US` is the canonical portfolio market. NASDAQ, NYSE, NYSE Arca, and
similar identifiers are listing or execution venues, not portfolio identity.
Holding and quote identity remains `(symbol, market)`; venue metadata may be
added later without changing that identity if a reliable provider supplies it.

### Historical FX backfill

```text
CLI fx backfill
       ↓
BackfillFxRatesUseCase
       ↓
AlphaVantageFXRateProvider.fetch_between
       ↓
FxRateRepository.insert_if_absent
```

The provider filters one daily series to an inclusive requested range, using a
compact response when it covers the start and one bounded full-series fallback
otherwise. It returns only real provider dates and Decimal closes. The
application compares persisted pair/date observations and applies missing rows
inside the existing market-data transaction boundary. Insert conflicts do
nothing, so historical values and `fetched_at` provenance are immutable under
backfill and repeated runs are idempotent. No weekend or holiday row is
synthesized, and no rate after the requested end date is returned.
## Annual investment P/L (schema v9)

`TransactionEngine` emits an immutable `RealizedSale` for every SELL, including
sold quantity, moving-average unit and total basis, gross and net proceeds,
fees, taxes, realized P/L, and realized return. This history remains queryable
after a holding reaches zero.

```text
TransactionRepository ──> TransactionEngine ──> RealizedSale
DailySnapshot -----------┐
DividendEvent -----------┼─> AnnualPnlUseCase ─> AnnualPnlEngine
InvestmentCostEvent -----┤                       │
Historical FxRate -------┘                       v
                                      annual_pnl_snapshots
```

`annual_pnl_snapshots` has one immutable row per calendar date. YTD flows are
filtered by calendar year, so a new year resets realized, dividend, financing,
and other-cost totals without deleting previous years. The formula is
`realized + unrealized + dividends - financing costs - other costs`.

Broker-style holding basis excludes BUY fees/taxes; those expenses enter
`other_cost_ytd` once. SELL fees/taxes already reduce realized net proceeds and
are not subtracted again. `investment_cost_events` contains explicit dated
financing or other expenses only; liability notes are never parsed into
historical costs. Foreign-currency flows use the latest persisted FX rate on or
before their effective date and fail safely when no eligible rate exists.

## Corporate-action ledger events (schema v10)

`corporate_actions` is a separate persisted event grain for stock splits,
reverse splits, and equivalent ETF quantity conversions. The Transaction
Engine replays same-day BUY, corporate action, then SELL. A positive Decimal
multiplier changes only active quantity, preserves total cost basis exactly,
and recalculates average cost. It creates no realized P/L, cash flow, fee, tax,
expense, dividend, or financing change. An action against no active position
is invalid; corporate actions never masquerade as BUY or SELL transactions.
