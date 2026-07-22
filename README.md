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
config/         Environment and validated YAML configuration
domain/         Framework-independent models and enums
services/       Portfolio, snapshot, and bootstrap use cases
repositories/   Persistence protocols and SQLite adapters
database/       SQLite connection and versioned schema
tests/          Unit and SQLite integration tests
docs/           Architecture, database, and roadmap documentation
```

See [architecture](docs/architecture.md), [database design](docs/database.md), and the [roadmap](docs/roadmap.md).
