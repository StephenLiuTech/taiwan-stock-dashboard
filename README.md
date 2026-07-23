# PAMS

**Personal Asset Management System for Taiwan-listed portfolios.**

PAMS is a local-first Python application for recording transactions, projecting
holdings, ingesting official TWSE and TPEx closing prices, valuing a portfolio,
and producing a Streamlit dashboard and printable daily reports. Financial
calculations use `Decimal`, and presentation entry points consume immutable
application DTOs instead of repositories or SQL.

Current release: **v0.8.0**

## Highlights

- Pure Portfolio Valuation Engine shared by CLI, dashboard, snapshots, and reports
- Official TWSE and TPEx latest and historical market-data providers
- Strict source-date verification with no stale-price relabeling
- Transaction ledger with moving weighted-average holding projection
- Atomic SQLite persistence for quotes and portfolio snapshots
- Dashboard 2.0 with summary, allocation, winners, losers, and sortable holdings
- Deterministic Markdown and standalone semantic HTML daily reports
- Pure snapshot-based basic performance analytics foundation
- Operational `status`, `verify`, dry-run, and JSON CLI modes
- Isolated synthetic demo-data workflow

## Product flow

```text
Transactions → Transaction Engine → Holdings
                                      ↓
Official Market Data → Normalization → Valuation Engine
                                      ↓
                            PortfolioValuation
                              ├─ CLI
                              ├─ Dashboard
                              └─ Daily Reports
```

The valuation engine is the single source of truth for cost basis, market value,
unrealized profit or loss, and return. Streamlit and report renderers only
format, order, and display application-provided values.

## Requirements

- Python 3.11 or 3.12
- SQLite
- Network access only when fetching official TWSE or TPEx data

## Quick start

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

Copy `.env.example` to `.env` only when configuration overrides are needed.
The default database is local SQLite.

Create an isolated synthetic portfolio and launch the dashboard:

```bash
python -m pams demo-data
python -m streamlit run app.py -- --database data/pams_demo.db
```

Demo quotes are deterministic fixtures marked `demo_fixture`. They are intended
for software demonstration only, not investment decisions.

## Dashboard

Launch the configured portfolio:

```bash
python -m streamlit run app.py
```

Dashboard 2.0 executes `ValuatePortfolioUseCase` once per cached page load and
renders:

- Portfolio Summary
- Largest Positions
- Allocation by holding
- Top Winners and Top Losers
- Sortable full Portfolio Table

The dashboard has no repository, SQL, provider, or valuation-formula access.

## Command-line interface

### Portfolio valuation

```bash
python -m pams portfolio valuate
python -m pams portfolio valuate --json
```

### Portfolio analytics

Analyze all aggregate daily snapshots or an inclusive requested period:

```bash
python -m pams analytics portfolio
python -m pams analytics portfolio --from 2026-01-01
python -m pams analytics portfolio --from 2026-01-01 --to 2026-07-22
python -m pams analytics portfolio --json
```

The application layer loads `DailySnapshot` history and delegates every
performance calculation to the pure `AnalyticsEngine`. This foundation does
not adjust for cash flows and does not implement TWR, MWR, IRR, volatility, or
benchmark comparison.

### Daily reports

Markdown is printed to standard output by default. Reports can also be written
as UTF-8 Markdown or standalone HTML files.

```bash
python -m pams report generate
python -m pams report generate --html
python -m pams report generate --output report.md
python -m pams report generate --html --output report.html
```

### Market-data updates

Automatic mode uses latest-only providers and proceeds only when TWSE and TPEx
publish the same official date:

```bash
python -m pams update
python -m pams update --dry-run
```

If publication is staggered, PAMS returns a successful
`no_update_sources_unsynchronized` outcome without ingestion or persistence.
It never guesses weekends or holidays.

Manual mode uses official historical date-query providers:

```bash
python -m pams update --date 2026-07-22
python -m pams update --date 2026-07-22 --dry-run --json
```

The source date from each exchange must match the requested date. PAMS never
relabels a latest payload as historical data.

### Transactions and holdings

```bash
python -m pams transaction add --id tx-001 --symbol 2330 --market TWSE \
  --type buy --trade-date 2026-07-01 --settlement-date 2026-07-03 \
  --quantity 100 --price 1800 --fees 20 --taxes 0 --currency TWD

python -m pams transaction list --symbol 2330 --json
python -m pams holdings rebuild
python -m pams holdings rebuild --apply --allow-unmatched
```

Holding rebuild is preview-only by default. Applying requires an explicit flag
and preserves historical snapshots.

### Operations

```bash
python -m pams status
python -m pams verify
python -m pams --help
```

Use `--database PATH` on database-backed commands to select another SQLite
file, and `--verbose` for diagnostic tracebacks.

Exit codes: `0` success, `1` unexpected error, `2` invalid arguments, `3`
provider failure, `4` source-date failure, `5` security/holding issue, `6`
duplicate snapshot, `7` configuration/database failure, and `8` verification
failure.

## Architecture

PAMS uses dependency inversion around a framework-independent domain:

```text
CLI / Streamlit
       ↓
Application use cases and immutable DTOs
       ↓
Domain services and repository protocols
       ↓
SQLite and official-market adapters
```

See:

- [Architecture](docs/architecture.md)
- [Database design](docs/database.md)
- [Roadmap](docs/roadmap.md)
- [v0.8.0 release notes](docs/releases/v0.8.0.md)
- [Changelog](CHANGELOG.md)

## Development and release validation

```bash
python -m black --check .
python -m ruff check .
python -m pytest --basetemp=.pytest_tmp
python -m compileall .
```

The test suite is offline and covers domain behavior, repositories, application
workflows, CLI routing, dashboard projections, market-data integrity,
transactions, valuation, and report generation.

## Project structure

```text
app.py             Streamlit composition root
config/            Environment and validated YAML configuration
database/          SQLite connection and versioned schema
domain/            Framework-independent models, ledger, and valuation DTOs
market_calendar/   Official cross-market availability resolution
market_data/       Providers, normalization, integrity checks, and ingestion
pams/application/  Presentation-neutral workflows
pams/dashboard/    Streamlit-only presentation
pams/reporting/    Daily report builder and renderers
repositories/      Protocols and SQLite adapters
services/          Pure domain/application services
tests/             Offline unit and integration tests
```

## Release scope

v0.8.0 intentionally does not include scheduling, email or messaging delivery,
broker imports, corporate actions, authentication, or cloud persistence.
