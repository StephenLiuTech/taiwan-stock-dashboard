# PAMS architecture

## Dependency direction

Dependencies flow in one direction: **UI → Services → Repository protocols → Database adapters**. The Streamlit entry point is a composition root only. Domain models sit at the center and do not import Streamlit, SQLite, Pandas, Plotly, or external APIs.

## Domain boundaries

The domain package owns financial records, constrained enums, and structural invariants. Money, prices, and ratios use `Decimal`; ratios are decimal fractions (`0.05` means 5%). Dates and timestamps use standard-library `date` and timezone-aware `datetime` values.

## Repositories

Each aggregate has a purpose-specific protocol. Repositories translate between domain records and persistence rows, use parameterized SQL, and expose only needed queries. They do not calculate portfolio results or silently handle database failures.

## Services

- `PortfolioService` matches holdings and quotes and calculates position and portfolio totals.
- `SnapshotService` maintains high-water marks and drawdowns and persists one record per date.
- `BootstrapService` initializes storage and seeds the known portfolio only when both seed aggregates are empty.

## Persistence

SQLite is the local adapter. Schema initialization is idempotent and versioned, leaving a clear seam for ordered migration functions later. Domain-facing repository protocols prevent SQLite details from leaking into services.

## Future Supabase migration

A future Supabase adapter should implement the existing repository protocols and preserve domain serialization semantics. Configuration would select the adapter in the composition root. Domain models and services should require no changes; schema migration, authentication, connection lifecycle, and concurrency behavior must be addressed within the new adapter and deployment layer.
