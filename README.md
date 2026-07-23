# PAMS

**Personal Asset Management System for Taiwan-listed portfolios.**

PAMS is a local-first Python application for recording transactions, projecting
holdings, ingesting official TWSE and TPEx closing prices, valuing a portfolio,
and producing a Streamlit dashboard and printable daily reports. Financial
calculations use `Decimal`, and presentation entry points consume immutable
application DTOs instead of repositories or SQL.

Current release: **v1.0.0**

## Highlights

- Pure Portfolio Valuation Engine shared by CLI, dashboard, snapshots, and reports
- Canonical immutable `PortfolioValuation` returned through one application use case
- Official TWSE and TPEx latest and historical market-data providers
- Strict source-date verification with no stale-price relabeling
- Transaction ledger with moving weighted-average holding projection
- Atomic SQLite persistence for quotes and portfolio snapshots
- Dashboard 2.0 with summary, allocation, winners, losers, and sortable holdings
- Deterministic Markdown and standalone semantic HTML daily reports
- Pure snapshot-based basic performance analytics foundation
- Shared analytics presentation across CLI, Dashboard, Markdown, and HTML reports
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

Aggregate Snapshots → AnalyzePortfolioUseCase → AnalyticsEngine
                                             ↓
                                  PortfolioAnalytics
                              ├─ CLI
                              ├─ Dashboard
                              └─ Daily Reports
```

The valuation engine is the single source of truth for cost basis, market value,
unrealized profit or loss, return, portfolio totals, and holding weights.
`ValuatePortfolioUseCase` is the single production entry point for current
valuation. Streamlit and report renderers only format, order, and display
application-provided values.

## Requirements

- Python 3.11 or 3.12
- SQLite, included with supported Python installations
- Network access only when fetching official TWSE or TPEx data

## Installation

Create and activate an isolated environment:

```bash
python -m venv .venv
```

Linux or macOS:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install PAMS and its runtime dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

For development and release validation:

```bash
python -m pip install -e ".[dev]"
```

Both `python -m pams` and the installed `pams` console command are supported.

Confirm the installed release with:

```bash
python -m pams --version
```

## Configuration

PAMS has safe local defaults and does not require credentials. Copy
`.env.example` to `.env` only when overrides are needed:

```text
PAMS_ENVIRONMENT=development
PAMS_LOG_LEVEL=INFO
PAMS_DATABASE_URL=sqlite:///data/pams.db
PAMS_APP_TITLE=PAMS
```

Environment variables override `.env`. The default database path is
`data/pams.db`; local databases are ignored by Git.

## First run

PAMS has no separate schema-initialization command. The easiest first run is
the isolated demo workflow, which creates a fully initialized database with
synthetic holdings, quotes, and snapshots:

Create an isolated synthetic portfolio and launch the dashboard:

```bash
python -m pams demo-data
python -m streamlit run app.py -- --database data/pams_demo.db
```

The demo command reports the absolute database path and launch command. By
default it writes `data/pams_demo.db`.

Demo quotes are deterministic fixtures marked `demo_fixture`. They are intended
for software demonstration only, not investment decisions.

For a new production-style local database, the existing write workflows
initialize schema as needed. Read-only commands do not silently create missing
databases; they return a controlled error with first-run guidance.

## Dashboard

Launch the configured portfolio:

```bash
python -m streamlit run app.py
```

To launch a specific database:

```bash
python -m streamlit run app.py -- --database data/pams_demo.db
```

Dashboard 2.0 executes `ValuatePortfolioUseCase` once per cached page load and
renders:

- Portfolio Summary
- Largest Positions
- Allocation by holding
- Top Winners and Top Losers
- Sortable full Portfolio Table
- Snapshot-period analytics summary and application-provided daily returns

The dashboard obtains valuation and analytics through composed Application
Layer use cases. It has no repository, SQL, provider, valuation formula,
return formula, or drawdown formula access. Decimal values remain intact until
the chart rendering boundary.

## Command-line interface

### Portfolio valuation

```bash
python -m pams portfolio valuate --database data/pams_demo.db
python -m pams portfolio valuate --json --database data/pams_demo.db
```

### Portfolio analytics

Analyze all aggregate daily snapshots or an inclusive requested period:

```bash
python -m pams analytics portfolio --database data/pams_demo.db
python -m pams analytics portfolio --from 2026-01-01 --database data/pams_demo.db
python -m pams analytics portfolio --from 2026-01-01 --to 2026-07-22 --database data/pams_demo.db
python -m pams analytics portfolio --json --database data/pams_demo.db
```

The application layer loads `DailySnapshot` history and delegates every
performance calculation to the pure `AnalyticsEngine`. This foundation does
not adjust for cash flows and does not implement TWR, MWR, IRR, volatility, or
benchmark comparison.

### Daily reports

Markdown is printed to standard output by default. Reports can also be written
as UTF-8 Markdown or standalone HTML files.

```bash
python -m pams report generate --database data/pams_demo.db
python -m pams report generate --html --database data/pams_demo.db
python -m pams report generate --output report.md --database data/pams_demo.db
python -m pams report generate --html --output report.html --database data/pams_demo.db
```

Without `--output`, reports are written to standard output. Relative output
paths such as `report.md` and `report.html` are created in the current working
directory. UTF-8 is used for report files.

Daily reports call `AnalyzePortfolioUseCase` through the existing composition
root and include a concise snapshot analytics summary. If analytics are
unavailable, the report remains valid and displays a controlled status instead
of substituting zero performance.

### Market-data updates

Automatic mode reads each market's latest official date and selects the newer
date available from both markets:

```text
min(TWSE latest date, TPEx latest date)
```

When the dates match, PAMS uses the latest providers. When publication is
staggered, it uses the existing date-bound historical providers for the common
date:

```bash
python -m pams update
python -m pams update --dry-run
```

The Market Data Engine still requires both fetched source dates to exactly
match the selected trade date. PAMS never guesses weekends or holidays and
never relabels prices.

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

The actual analytics command includes its required `portfolio` subcommand.
Run `python -m pams analytics --help` or
`python -m pams report generate --help` for command-specific options.

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

- [Product vision](docs/product-vision.md)
- [Development standard](docs/development-standard.md)
- [Architecture](docs/architecture.md)
- [Database design](docs/database.md)
- [Roadmap](docs/roadmap.md)
- [v1.0.0 release notes](docs/releases/v1.0.0.md)
- [v0.8.0 release notes](docs/releases/v0.8.0.md)
- [Changelog](CHANGELOG.md)
- [Contributing guide](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [MIT License](LICENSE)

## Development and release validation

```bash
python -m black --check .
python -m ruff check .
python -m pytest --basetemp=.pytest_tmp
python -m compileall .
git diff --check
```

The test suite is offline and covers domain behavior, repositories, application
workflows, CLI routing, dashboard projections, market-data integrity,
transactions, valuation, and report generation.

## Project structure

```text
app.py             Streamlit composition root
.agents/           Agent-specific repository development rules
.github/           CI workflow and collaboration templates
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

## First-run and unavailable-data behavior

- Missing database: controlled configuration error; no empty database is created.
- No holdings: valuation reports that no portfolio holdings are available.
- Missing quote: valuation identifies the affected holding instead of reusing a
  stale price.
- No snapshots: analytics reports that history is unavailable.
- Missing analytics in a report: the valuation report remains valid and labels
  analytics unavailable rather than showing a zero return.
- Use `--verbose` only for local diagnostics when a traceback is needed.

## Known limitations

v1.0 supports Taiwan-listed equity portfolios in one currency context. It does
not include allocation analytics, benchmarks, TWR, MWR, IRR, XIRR, broker
imports, multi-asset valuation, FX conversion, scheduling, notifications,
authentication, cloud persistence, or automated backup management.
