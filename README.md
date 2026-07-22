# PAMS

PAMS (Personal Asset Management System) is a typed Python and Streamlit application for tracking a personal investment portfolio. The first dashboard reads persisted portfolio snapshots through the application layer and keeps update operations dry-run only.

## Implemented

- Validated holdings, transactions, dividends, liabilities, quotes, valuations, and snapshots
- Portfolio valuation and daily snapshot services
- Explicit TWSE/TPEx end-of-day ingestion and quote normalization
- Decimal-safe `price_quotes`, aggregate `daily_snapshots`, and holding-level `position_snapshots` persistence
- Versioned SQLite schema and domain-specific repositories
- Idempotent bootstrap of the current known portfolio
- Environment-backed secrets/configuration and validated non-secret YAML settings
- Single-page portfolio dashboard with KPIs, holdings, allocation, history, and market availability

## Installation

Python 3.11 or 3.12 is required.

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env` if environment overrides are needed.

## Running

```bash
streamlit run app.py
```

The dashboard displays `—` for unavailable financial values and clear empty states when quotes or snapshots do not exist. Its **Refresh data** action performs validation and valuation as a dry run; it never persists an update. **Reload dashboard** clears the short-lived read cache and reruns the page.

To create a populated synthetic demo database without touching the configured production database:

```bash
python -m pams demo-data
python -m streamlit run app.py -- --database data/pams_demo.db
```

Use `python -m pams demo-data --database data/custom_demo.db` for a custom demo path. Demo generation is deterministic, uses no live market providers, and recreates only the selected demo database. All demo quotes are marked `demo_fixture`. This synthetic data is for software demonstration only and must not be used for investment decisions.

Run an explicit historical market-data update:

```bash
python -m pams update --date 2026-07-22
pams update --date 2026-07-22
```

An explicit `--date` uses the official TWSE and TPEx date-query providers. Each response carries its authoritative source date, which must match the request before any write occurs. Historical updates never relabel the latest payload as an earlier date.

Omit `--date` to use the latest-only providers and update only when TWSE and TPEx expose the same official date. PAMS never guesses weekends or holidays. When publication is staggered, automatic update returns success with `no_update_sources_unsynchronized` and performs no ingestion:

```bash
python -m pams update --dry-run
```

```text
Automatic update → latest-only providers → synchronization check → engine
Manual update    → historical date-query providers → source-date check → engine
```

Preview the complete fetch, source-date validation, normalization, and valuation without writing quotes or snapshots:

```bash
python -m pams update --date 2026-07-22 --dry-run
python -m pams update --date 2026-07-22 --dry-run --json
```

Use `--database PATH` to override SQLite and `--verbose` for diagnostic tracebacks. Duplicate persisted dates remain protected; there is intentionally no force option.

Inspect local database state and operational readiness:

```bash
python -m pams status
python -m pams verify
```

Record and query transactions through the application layer:

```bash
python -m pams transaction add --id tx-001 --symbol 2330 --market TWSE \
  --type buy --trade-date 2026-07-01 --settlement-date 2026-07-03 \
  --quantity 100 --price 1800 --fees 20 --taxes 0 --currency TWD
python -m pams transaction list
python -m pams transaction list --symbol 2330 --from-date 2026-07-01 \
  --to-date 2026-07-31 --json
```

Preview a transaction-derived holding rebuild—the default performs no writes:

```bash
python -m pams holdings rebuild
python -m pams holdings rebuild --dry-run --json
```

Applying is deliberately explicit. Active bootstrap holdings without matching transaction history produce migration warnings and block the apply unless acknowledged:

```bash
python -m pams holdings rebuild --apply
python -m pams holdings rebuild --apply --allow-unmatched
```

Rebuilds never delete holding rows. Positions absent from the active ledger are closed by setting quantity and average cost to zero. Existing daily and position snapshots are immutable history and are never rewritten; only future market-data updates use rebuilt holdings.

Exit codes: `0` success, `1` unexpected, `2` arguments/date, `3` provider data, `4` source-date freshness, `5` missing/suspended security, `6` duplicate snapshot, `7` configuration/database, and `8` verification failed.

## Testing

```bash
python -m black --check .
python -m ruff check .
python -m pytest
python -m compileall .
```

## Project structure

```text
app.py          Small Streamlit composition root
pams/           Application use cases/DTOs, CLI, dashboard, composition, reporting
market_calendar/ Official cross-market availability-date resolution
config/         Environment and validated YAML configuration
domain/         Framework-independent models and enums
services/       Portfolio, snapshot, and bootstrap use cases
repositories/   Persistence protocols and SQLite adapters
database/       SQLite connection and versioned schema
tests/          Unit and SQLite integration tests
docs/           Architecture, database, and roadmap documentation
```

See [architecture](docs/architecture.md), [database design](docs/database.md), and the [roadmap](docs/roadmap.md).
