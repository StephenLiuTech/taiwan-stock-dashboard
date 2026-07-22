# PAMS

PAMS (Personal Asset Management System) is a typed Python and Streamlit foundation for tracking a personal investment portfolio. Version 0.2 implements the first domain, service, and local persistence layers without external market-data or notification integrations.

## Implemented

- Validated holdings, transactions, dividends, liabilities, quotes, valuations, and snapshots
- Portfolio valuation and daily snapshot services
- Explicit TWSE/TPEx end-of-day ingestion and quote normalization
- Decimal-safe `price_quotes`, aggregate `daily_snapshots`, and holding-level `position_snapshots` persistence
- Versioned SQLite schema and domain-specific repositories
- Idempotent bootstrap of the current known portfolio
- Environment-backed secrets/configuration and validated non-secret YAML settings

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

Run an explicit local market-data update:

```bash
python -m pams update --date 2026-07-22
pams update --date 2026-07-22
```

Preview the complete fetch, source-date validation, normalization, and valuation without writing quotes or snapshots:

```bash
python -m pams update --date 2026-07-22 --dry-run
python -m pams update --date 2026-07-22 --dry-run --json
```

Use `--database PATH` to override SQLite and `--verbose` for diagnostic tracebacks. Duplicate persisted dates remain protected; there is intentionally no force option.

Exit codes: `0` success, `1` unexpected, `2` arguments/date, `3` provider data, `4` source-date freshness, `5` missing/suspended security, `6` duplicate snapshot, and `7` configuration/database.

## Testing

```bash
python -m black --check .
python -m ruff check .
python -m pytest
python -m compileall .
```

## Project structure

```text
app.py          Streamlit composition root
pams/           CLI parsing, composition, and terminal reporting
config/         Environment and validated YAML configuration
domain/         Framework-independent models and enums
services/       Portfolio, snapshot, and bootstrap use cases
repositories/   Persistence protocols and SQLite adapters
database/       SQLite connection and versioned schema
tests/          Unit and SQLite integration tests
docs/           Architecture, database, and roadmap documentation
```

See [architecture](docs/architecture.md), [database design](docs/database.md), and the [roadmap](docs/roadmap.md).
