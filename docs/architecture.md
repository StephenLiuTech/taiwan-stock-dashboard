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
The application layer adds Decimal portfolio weights to the returned holding
DTOs for presentation consumers.

The legacy `PortfolioService` delegates its overlapping calculations to
`ValuationEngine`, preserving snapshot compatibility without duplicating
valuation formulas.

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

`app.py` composes one `ValuatePortfolioUseCase` for Streamlit. Dashboard 2.0
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
