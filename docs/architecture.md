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

The domain package owns financial records, constrained enums, and structural invariants. Money, prices, and ratios use `Decimal`; ratios are decimal fractions (`0.05` means 5%). Dates and timestamps use standard-library `date` and timezone-aware `datetime` values.

## Repositories

Each aggregate has a purpose-specific protocol. Repositories translate between domain records and persistence rows, use parameterized SQL, and expose only needed queries. They do not calculate portfolio results or silently handle database failures.

## Services

- `PortfolioService` matches holdings and quotes and calculates position and portfolio totals.
- `SnapshotService` maintains high-water marks and drawdowns and persists one record per date.
- `BootstrapService` initializes storage and seeds the known portfolio only when both seed aggregates are empty.
- `MarketDataEngine` verifies the official ROC source date, normalizes only held symbols, values the complete portfolio, and atomically persists quotes, one aggregate daily snapshot, and one position snapshot per holding. It has no scheduler or UI dependency.

`MarketDataEngine.preview()` follows the same provider, date-verification, normalization, completeness, and valuation path but does not invoke quote or snapshot writes. CLI dry runs never use write-then-delete behavior.

## Persistence

SQLite is the local adapter. Schema initialization is idempotent and versioned. One explicit `BEGIN IMMEDIATE` unit of work surrounds all writes for an ingestion run. Domain-facing repository protocols prevent SQLite details from leaking into services.

CLI update flow is: parse arguments → composed `UpdatePortfolioUseCase` → immutable `UpdateResult` → terminal/JSON rendering → exit. The use case owns automatic date resolution, dry-run routing, synchronization handling, and engine invocation. Status and verification follow the same pattern through `PortfolioStatusUseCase` and `VerifySystemUseCase`.

When an update omits `--date`, `MarketCalendar` reads the unique official date exposed by each latest-only provider. A commonly ingestible date exists only when TWSE and TPEx dates are equal. If publication is staggered, CLI returns a successful no-update outcome before calling the engine. It never treats the earlier date as retrievable, and contains no weekday or holiday tables. Manual requested dates still use strict engine verification. `status` exposes both source dates and current ingestibility; `verify` reports disagreement as WARN.

## Future Supabase migration

A future Supabase adapter should implement the existing repository protocols and preserve domain serialization semantics. Configuration would select the adapter in the composition root. Domain models and services should require no changes; schema migration, authentication, connection lifecycle, and concurrency behavior must be addressed within the new adapter and deployment layer.
