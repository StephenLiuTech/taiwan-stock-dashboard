# PAMS architecture

## Dependency direction

Dependencies flow in one direction: **entry point → application layer → domain/services → repository protocols → database adapters**. Domain models sit at the center and do not import Streamlit, CLI, SQLite, Pandas, Plotly, or external APIs.

```text
CLI / future Dashboard / future Automation
                    |
                    v
       Application use cases + DTOs
                    |
                    v
        Domain models and services
                    |
                    v
     Repository protocols and adapters
```

## Domain boundaries

The `pams` command package is a composition surface. `cli.py` owns only argument parsing, DTO rendering, and exit codes. `composition.py` constructs concrete dependencies and complete use cases. `pams.application` owns the update, status, and verification workflows and returns immutable DTOs; it has no CLI or Streamlit dependency. `reporting.py` only serializes those DTOs. Importing the package creates no services or database connections.

The Streamlit dashboard follows the same boundary. `app.py` is a small composition root and passes composed application use cases into `pams.dashboard`. Dashboard modules import application DTOs and use cases only; they contain no SQLite, repository, provider, market-engine, domain-service, or SQL access. `PortfolioStatusUseCase` returns `PortfolioOverview`, including persisted KPIs and holding rows. `PortfolioHistoryUseCase` returns chronological aggregate `PortfolioHistory` points. The UI formats these values but does not recalculate them.

Dashboard read queries use an explicit 60-second Streamlit data cache. Update operations are never cached and the dashboard invokes `UpdatePortfolioUseCase` only with `dry_run=True`. Missing quotes, snapshots, allocation, or history render as unavailable values or empty-state messages. Source disagreement is a neutral waiting condition rather than a system failure.

`DemoDataUseCase` is an explicitly synthetic offline workflow. It validates that its target differs from the configured production database, creates a complete temporary SQLite database in one transaction, and atomically replaces only the selected demo target after success. It reuses the existing seed records, valuation service, snapshot service, and repository adapters; it never composes or calls market providers. Dashboard database selection remains in the `app.py` composition root via the forwarded `--database` argument.

The domain package owns financial records, constrained enums, and structural invariants. Money, prices, and ratios use `Decimal`; ratios are decimal fractions (`0.05` means 5%). Dates and timestamps use standard-library `date` and timezone-aware `datetime` values.

## Repositories

Each aggregate has a purpose-specific protocol. Repositories translate between domain records and persistence rows, use parameterized SQL, and expose only needed queries. They do not calculate portfolio results or silently handle database failures.

## Services

- `PortfolioService` matches holdings and quotes and calculates position and portfolio totals.
- `SnapshotService` maintains high-water marks and drawdowns and persists one record per date.
- `BootstrapService` initializes storage and seeds the known portfolio only when both seed aggregates are empty.
- `MarketDataEngine` verifies the official ROC source date, normalizes only held symbols, values the complete portfolio, and atomically persists quotes, one aggregate daily snapshot, and one position snapshot per holding. It has no scheduler or UI dependency.
- `TransactionEngine` deterministically orders BUY and SELL records, maintains moving weighted-average cost by `(symbol, market, currency)`, and returns immutable active positions plus realized P/L. It has no repository, SQLite, Streamlit, Pandas, or market-data dependency.

```text
transactions
     |
     v
TransactionEngine
     |
     v
Holding Change Plan
     |
     v
Explicit Apply
     |
     v
Persisted Holdings
     |
     v
Future Portfolio Snapshots
```

`RebuildHoldingsUseCase` reads transaction and holding repository protocols, invokes the transaction engine, supplies explicit holding metadata, and returns immutable projection DTOs. The v0.7 Sprint 1 workflow is dry-run only: it does not call holding write methods, and existing bootstrap holdings remain authoritative until a later persistence migration is designed.

Sprint 2 adds `ApplyRebuiltHoldingsUseCase`. It always calculates an immutable change plan and defaults to preview. An apply requires an explicit caller flag, non-empty transaction history, and explicit acknowledgement of unmatched active bootstrap holdings. A dedicated unit of work exposes only transaction reads and holding writes, disables repository auto-commit, and commits all CREATE, UPDATE, and CLOSE operations together. CLOSE means zeroing quantity and average cost while retaining the row and its metadata; hard deletion is not part of the workflow.

Transaction entry and filtered listing are separate protocol-driven application use cases. CLI parsing uses `Decimal` directly and domain `Transaction` validation remains authoritative. Neither the transaction engine nor application use cases import SQLite.

Historical `daily_snapshots` and `position_snapshots` are outside the rebuild unit of work and are never rewritten. Rebuilt holdings affect portfolio valuation only when a future market-data update creates new snapshots.

`MarketDataEngine.preview()` follows the same provider, date-verification, normalization, completeness, and valuation path but does not invoke quote or snapshot writes. CLI dry runs never use write-then-delete behavior.

## Persistence

SQLite is the local adapter. Schema initialization is idempotent and versioned. One explicit `BEGIN IMMEDIATE` unit of work surrounds all writes for an ingestion run. Domain-facing repository protocols prevent SQLite details from leaking into services.

CLI update flow is: parse arguments → composed `UpdatePortfolioUseCase` → immutable `UpdateResult` → terminal/JSON rendering → exit. The use case owns automatic date resolution, dry-run routing, synchronization handling, and engine invocation. Status and verification follow the same pattern through `PortfolioStatusUseCase` and `VerifySystemUseCase`.

When an update omits `--date`, `MarketCalendar` reads the unique official date exposed by each latest-only provider. A commonly ingestible date exists only when TWSE and TPEx dates are equal. If publication is staggered, CLI returns a successful no-update outcome before calling the engine. It never treats the earlier date as retrievable, and contains no weekday or holiday tables. Manual requested dates still use strict engine verification. `status` exposes both source dates and current ingestibility; `verify` reports disagreement as WARN.

Manual updates use date-bound `HistoricalTWSEProvider` and `HistoricalTPExProvider` instances. They adapt the exchanges' structured historical JSON documents into the same `MarketDataProvider` record protocol used by latest-only sources. Composition selects historical providers only for an explicit date; the engine remains provider-mode agnostic and retains source-date, mixed-date, completeness, duplicate, and transactional checks.

## Future Supabase migration

A future Supabase adapter should implement the existing repository protocols and preserve domain serialization semantics. Configuration would select the adapter in the composition root. Domain models and services should require no changes; schema migration, authentication, connection lifecycle, and concurrency behavior must be addressed within the new adapter and deployment layer.
