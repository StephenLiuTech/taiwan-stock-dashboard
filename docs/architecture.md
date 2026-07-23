# PAMS architecture

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

Automatic updates use latest-only providers. `MarketCalendar` reads the
official date exposed by each provider; a commonly ingestible dataset exists
only when TWSE and TPEx expose the same date. Staggered publication produces a
normal no-update result before the engine is called.

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

Historical snapshots are immutable. Transaction-derived holding changes affect
only future valuations and snapshots.

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

## Out of scope for v0.8.0

- scheduling and background jobs
- email, Telegram, or other delivery channels
- broker imports and corporate actions
- authentication and multi-user access
- cloud persistence

Future adapters should implement existing repository protocols and preserve
transaction, Decimal, source-date, and snapshot-grain semantics.
