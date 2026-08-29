# PAMS

PAMS follows the broker portfolio cost convention. Trading fees and taxes are
tracked separately as investment expenses. Holding average cost and total cost
use executed quantity and price only; sell fees and taxes reduce realized
profit but never alter the remaining holding average cost.

Schema v9 adds immutable calendar-year investment P/L facts. Every SELL is
replayed through the same moving-average `TransactionEngine`, so realized P/L
remains queryable after a position closes. Daily annual snapshots preserve
realized P/L YTD, unrealized P/L, paid dividend income YTD, explicit financing
and other investment costs, and total P/L YTD.

Annual P/L follows this deterministic formula:

```text
Total P/L YTD = Realized P/L YTD + Unrealized P/L
                + Dividend Income YTD
                - Financing Cost YTD - Other Cost YTD
```

BUY fees/taxes are excluded from broker-style holding cost and therefore enter
`Other Cost YTD` once. SELL fees/taxes already reduce realized net proceeds and
are not subtracted again. Financing costs come only from explicit dated
investment-cost events; PAMS never reconstructs them from liability notes.

Inspect the ledger-derived and immutable annual results with:

```powershell
python -m pams pnl realized --year 2026
python -m pams pnl realized --symbol 2330 --json
python -m pams pnl summary --year 2026
python -m pams pnl summary --year 2026 --as-of 2026-08-28 --json
python -m pams pnl history --year 2026
python -m pams pnl history --year 2026 --from 2026-01-01 --to 2026-08-28
```

Historical USD flows use the latest persisted USD/TWD rate on or before each
flow date. A missing eligible historical rate is an error; no future rate is
fabricated.

Schema v10 records splits, reverse splits, and ETF quantity conversions as
first-class non-cash events. They preserve total cost basis and create no
trade, cash flow, fee, tax, expense, dividend, or financing change:

```bash
python -m pams corporate-action add --symbol 00631L --market TWSE \
  --effective-date 2026-03-31 --ratio 22 --source brokerage \
  --reference statement-20260828 --notes "1:22 quantity adjustment"
```

The one-time broker-statement reconciliation is read-only unless a separately
reviewed apply workflow is introduced:

```bash
python -m pams transaction bootstrap-import --dry-run
```

After reviewing a passing reconciliation, apply it interactively with:

```bash
python -m pams transaction bootstrap-import --apply
```

Automation may use `--apply --yes`. Apply replaces the reconciled 2026 ledger,
rebuilds holdings, and verifies the persisted result in one transaction. Any
failure rolls back the entire operation; rerunning an identical import is a
successful no-op.

For future statements, use the broker-neutral read-only reconciliation CLI:

```powershell
python -m pams broker reconcile data/imports/statement.csv
python -m pams broker reconcile data/imports/statement.csv `
  --from 2026-01-01 --to 2026-12-31 --json
```

It reports `MATCHED`, `NEW`, `MISMATCH`, `AMBIGUOUS`, and `UNSUPPORTED` rows
and identifies the source by SHA-256. Real broker files are local evidence and
must not be committed. Schema v13 adds structured provenance and explicit safe
apply:

```powershell
python -m pams broker import data/imports/statement.csv --dry-run
python -m pams broker import data/imports/statement.csv --apply
```

Apply reconciles again, verifies the displayed fingerprint, and writes only
dependency-complete `NEW` transactions with provenance in one transaction.
Mismatch, ambiguity, unsupported accounting rows, duplicate source rows, or
unresolved financing principal changes block the entire apply.

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
- SQLite and PostgreSQL repository providers selected by database URL
- Atomic persistence for quotes and portfolio snapshots
- Dashboard 2.0 with summary, allocation, winners, losers, and sortable holdings
- Deterministic Markdown and standalone semantic HTML daily reports
- Pure snapshot-based basic performance analytics foundation
- Shared analytics presentation across CLI, Dashboard, Markdown, and HTML reports
- Operational `status`, `verify`, dry-run, and JSON CLI modes
- Resend delivery for personal installations; Microsoft Graph for enterprises
- Isolated synthetic demo-data workflow
- Modular daily-report sections for allocation, events, dividends, news,
  deterministic insights, risk facts, watchlist, and transactions

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
- SQLite, included with supported Python installations, or PostgreSQL
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

### Modular daily-report sections

The email report keeps Summary, Trend, Contributors, and Holdings as its core.
After Holdings it renders, in a stable order, Allocation, Market Snapshot,
Upcoming Events, Dividend Calendar, AI News, Semiconductor News, Insights,
Risk Monitor, Watchlist, and the report-date Transaction Summary. Each section
can be switched independently with `PAMS_REPORT_SHOW_*`; the event horizon and
news limit default to 30 days and five items. The Dividend Calendar defaults to
the report's full calendar year with
`PAMS_REPORT_DIVIDEND_SCOPE=current_year`; `next_90_days` and `all` are also
supported. The deprecated `PAMS_REPORT_DIVIDEND_HORIZON_DAYS` is ignored; when
the new setting is absent, `current_year` remains the default. Risk warnings default to 30% for
one holding, 70% for the top three, and 80% for one market.

Allocation, insights, risk, persisted/manual dividends, watchlist, and
transactions use existing PAMS repositories and calculations. Market indices,
earnings/economic events, and news require a configured provider. PAMS never
substitutes invented values: an unavailable optional provider yields a compact
unavailable state and does not prevent the core report from being delivered.
Sector allocation is intentionally unavailable until a classification provider
is configured.

Watchlist entries are informational and are not recommendations:

```bash
python -m pams watchlist add 2330 --market TWSE --display-name TSMC
python -m pams watchlist list
python -m pams watchlist show 2330
python -m pams watchlist remove 2330
```

Official dividend announcements for active holdings can be refreshed and
inspected independently:

```bash
python -m pams dividend update
python -m pams dividend update --market TWSE --from 2026-07-01 --to 2026-12-31
python -m pams dividend list                         # current calendar year
python -m pams dividend list --scope next_90_days
python -m pams dividend list --scope all
python -m pams dividend list --from 2026-07-01 --to 2026-12-31
python -m pams dividend show 0050 --year 2026
```

PAMS reads official TWSE `exchangeReport/TWT48U_ALL` and `TWT49U` datasets and
TPEx `tpex_exright_prepost` and `bulletin/exDailyQ` datasets. Cash payment dates
are enriched only from the official MOPS company dividend announcement report
`https://mopsov.twse.com.tw/mops/web/ajax_t108sb27`. Matching requires one
unique event with the same market, normalized symbol, and ex-dividend date;
symbol/year-only matching is never used.

Refreshes use the shared bounded retry transport and upsert one row per
deterministic event key. Existing events and known payment dates survive refresh
failures. If MOPS has no safely joinable payment date, including many ETF
distributions, the value remains `N/A` and is never inferred.
Report eligibility uses transactions whose `trade_date` is before the
ex-dividend date; `settlement_date` is not used. Calendar amounts are estimates,
not confirmed deposits. `Paid` means only that the official payment date has
passed, not that a bank receipt was verified. `Actual Cash Received` is derived
as the estimated dividend once its official payment date has passed; otherwise
it is zero (or `N/A` when the estimate is unavailable). Actual Cash Received
means the dividend is expected to have been paid according to the official
payment date. It does not verify actual broker settlement.

The database backend is selected automatically:

```text
PAMS_DATABASE_URL=sqlite:///data/pams.db
PAMS_DATABASE_URL=postgresql://user:password@localhost:5432/pams
```

No command flag is needed. PostgreSQL tables and schema-version rows are
created idempotently by write composition. Credentials in PostgreSQL URLs are
redacted from operational output.

### SQLite to PostgreSQL migration

Set the existing SQLite database as the migration source and PostgreSQL as the
configured destination:

```text
PAMS_MIGRATION_SOURCE_URL=sqlite:///data/pams.db
PAMS_DATABASE_URL=postgresql://user:password@localhost:5432/pams
```

Then run:

```bash
python -m pams migrate
python -m pams verify
```

`verify` is strict by default. Scheduled delivery may instead use:

```bash
python -m pams verify --allow-market-source-warning
```

This mode reports temporary TWSE endpoint, TPEx endpoint, and Market Calendar
probe failures as `WARN` and exits successfully when those are the only
problems. Configuration, database, schema, holdings, liabilities, and Market
Data Engine composition failures remain fatal.

Migration copies holdings, liabilities, transactions, dividends, quotes,
aggregate and position snapshots, report-delivery history, and schema-version
metadata. The PostgreSQL destination must contain no application rows. Copying
and row-count validation use one destination transaction; failure rolls it
back, and the SQLite source is never deleted or modified.

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

TWSE resolves its newest official historical publication. TPEx resolves its
latest official `tpex_mainboard_daily_close_quotes` OpenAPI publication. When
publication is staggered, PAMS uses the existing date-bound historical
providers for the common date:

```bash
python -m pams update
python -m pams update --dry-run
```

The Market Data Engine still requires both fetched source dates to exactly
match the selected trade date. PAMS never guesses weekends or holidays and
never relabels prices.

Official HTTP reads use one bounded retry policy shared by latest and
historical providers. PAMS completely reads the response bytes before decoding
JSON and retries only incomplete reads, transient connection failures, and
HTTP 429/500/502/503/504/520. The default is four total attempts with exponential
backoff and jitter. Configure `PAMS_MARKET_HTTP_TIMEOUT_SECONDS` and
`PAMS_MARKET_HTTP_ATTEMPTS` (maximum 4) when needed. Complete malformed or
semantically invalid datasets and source-date mismatches are not retried.

Manual prior-date mode uses official historical date-query providers. For the
local current date, TPEx first uses its official structured OpenAPI completed-
close feed and falls back to `dailyQuotes` only when that endpoint actually
contains the same requested date:

```bash
python -m pams update --date 2026-07-22
python -m pams update --date 2026-07-22 --dry-run --json
python -m pams update --date 2026-07-22 --force
```

The source date from each exchange must match the requested date. PAMS never
relabels a latest payload as historical data.

An existing snapshot remains an idempotent no-op unless `--force` is supplied.
Forced update rebuilds holdings from the complete current transaction ledger
and atomically replaces that date's quotes, aggregate snapshot, and position
snapshots. The regular Market Data Engine duplicate guard remains active.

### Historical FX backfill

Historical foreign-currency cash flows require an authentic persisted rate on
or before their effective date. Preview and apply an inclusive bounded range:

```bash
python -m pams fx backfill --pair USD/TWD --from 2026-06-26 --to 2026-08-04 --dry-run
python -m pams fx backfill --pair USD/TWD --from 2026-06-26 --to 2026-08-04 --apply
```

The Alpha Vantage adapter makes one compact `FX_DAILY` request when it covers
the requested range and one bounded full-series fallback only when necessary.
Only actual provider observations inside the range are considered. Apply runs
in one database transaction and inserts missing dates without updating an
existing value or its fetch provenance. Repeating the same apply is a no-op.

### Transactions and holdings

```bash
python -m pams transaction add --id tx-001 --symbol 2330 --market TWSE \
  --type buy --trade-date 2026-07-01 \
  --quantity 100 --price 1800 --fees 20 --taxes 0 --currency TWD

python -m pams transaction add --symbol 2027 --market TWSE --type buy \
  --trade-date 2026-08-21 --quantity 2000 --price 50 --currency TWD \
  --financing margin

python -m pams transaction list --symbol 2330 --json
python -m pams holdings list
python -m pams holdings show 2330
python -m pams holdings list --as-of 2026-07-31
python -m pams holdings show 2330 --as-of 2026-07-31
python -m pams holdings rebuild
python -m pams holdings rebuild --apply --allow-unmatched
```

`--financing margin` records one explicitly classified Taiwan margin BUY. PAMS
uses `PAMS_MARGIN_SELF_FUNDING_RATIO` (default `0.40`) and derives the financed
ratio as `1 - self_funding_ratio`. Gross value excludes fees and taxes. The
transaction, rebuilt holding, structured margin-financed quantity, and margin
principal update commit atomically. Normal transactions are unchanged. Margin
SELLs, US transactions, non-TWD transactions, and invalid ratios are rejected.

`--settlement-date` is optional and defaults to `--trade-date`. Explicit
settlement dates remain compatible when the record needs one:

```bash
python -m pams transaction add --symbol 2330 --market TWSE --type buy \
  --trade-date 2026-07-01 --settlement-date 2026-07-03 \
  --quantity 100 --price 1800
```

Portfolio state is effective on `trade_date`; settlement date remains stored
as transaction metadata and does not delay holdings, valuation, history, or
daily-report inclusion. `holdings list` and `holdings show` project active
positions directly from transactions and reuse the shared valuation engine
with latest persisted quotes. Detail output also includes transaction count
and the first and latest trade dates. Holdings are effective by trade date. The
current model has no true intraday chronology, so same-day buys are applied
before same-day sells; transaction ID is only a deterministic tie-breaker among
transactions on the same date and side. Settlement date is not an ordering
input. True intraday ordering would require a future explicit sequence or
timestamp field. Oversells are rejected because short positions are
unsupported. If an active holding has no latest quote, quantity and cost remain
visible while market-dependent fields are displayed as `N/A`.

`--as-of` is an inclusive historical cutoff based only on `trade_date`.
Transactions after the requested date are excluded, and historical valuation
uses the latest persisted quote on or before that date. A later quote is never
substituted. When no eligible quote exists, quantity and cost remain available
while market fields and quote date render as `N/A`. The same day-level
BUY-before-SELL policy applies at the cutoff; true intraday chronology remains
unsupported.

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
.github/           CI, scheduled delivery, and collaboration templates
config/            Environment and validated YAML configuration
database/          SQLite/PostgreSQL providers and versioned schemas
domain/            Framework-independent models, ledger, and valuation DTOs
market_calendar/   Official cross-market availability resolution
market_data/       Providers, normalization, integrity checks, and ingestion
pams/application/  Presentation-neutral workflows
pams/dashboard/    Streamlit-only presentation
pams/reporting/    Daily report builder and renderers
repositories/      Protocols plus SQLite/PostgreSQL adapters
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
imports, multi-asset valuation, FX conversion, non-email notifications,
authentication, or automated backup management.

## Automated cloud delivery

Production report delivery can run without a local computer:

```text
GitHub Actions
    â†“
Supabase PostgreSQL
    â†“
PAMS daily-report workflow
    â†“
Resend
    â†“
Email recipient
```

The workflow is
[`.github/workflows/daily-report.yml`](.github/workflows/daily-report.yml).
It runs on weekdays at `13:35 Asia/Taipei`, using:

```text
35 13 * * 1-5
timezone: Asia/Taipei
```

Configure these repository-level GitHub Actions secrets:

| Secret | Purpose |
|---|---|
| `PAMS_DATABASE_URL` | Supabase PostgreSQL connection URL |
| `PAMS_RESEND_API_KEY` | Resend sending API key |
| `PAMS_EMAIL_FROM` | Sender on a Resend-verified domain |
| `PAMS_EMAIL_TO` | Daily-report recipient |
| `PAMS_SUPABASE_URL` | Supabase project HTTPS URL for Storage |
| `PAMS_SUPABASE_SERVICE_ROLE_KEY` | Server-side Storage upload credential |
| `PAMS_REPORT_ASSET_BUCKET` | Public report-image bucket |
| `PAMS_REPORT_ASSET_PREFIX` | Stable random deployment-specific object prefix |
| `PAMS_ALPHA_VANTAGE_API_KEY` | Alpha Vantage key for opt-in US closes and USD/TWD FX |

The job sets `PAMS_EMAIL_TRANSPORT=resend`,
`PAMS_US_MARKET_DATA_PROVIDER=alphavantage`,
`PAMS_FX_PROVIDER=alphavantage`, `PAMS_ENVIRONMENT=production`, and
`PAMS_LOG_LEVEL=INFO`; these values are not
secrets. Secret values are scoped only to the verification and delivery steps
that require them and are never echoed by workflow steps.

Every run installs PAMS from `pyproject.toml`, including PostgreSQL and chart
dependencies, then executes:

```bash
python -m pams verify --allow-market-source-warning
python -m pams daily-report send --debug
```

The relaxed verification mode prevents a transient preliminary market probe
from blocking the delivery step. It does not bypass date integrity or update
checks: the daily-report workflow still fails if it cannot resolve and validate
the report date when it performs the actual update.

The scheduled path never uses `--force`. Normal snapshot and report-delivery
idempotency therefore prevents repeated workflow runs from resending the same
report. The workflow has read-only GitHub token permissions, a 15-minute
timeout, pip dependency caching, and a non-cancelling `pams-daily-report`
concurrency group.

### Manual cloud execution

Open **Actions → Daily Portfolio Report → Run workflow**. Leave
`force_rebuild` disabled for the normal idempotent path. Enable it only for an
intentional emergency rebuild; that dispatch executes:

```bash
python -m pams daily-report send --force --debug
```

Scheduled runs remain non-forced regardless of manual input history.

### Cloud troubleshooting

- If verification fails, confirm the four repository secrets exist and that
  the Supabase URL accepts connections from GitHub-hosted runners.
- If schema or holdings verification fails, migrate the SQLite ledger to the
  configured PostgreSQL database before enabling the schedule.
- If market-date resolution fails, inspect the TWSE/TPEx verification rows;
  PAMS will not send a report with an unverified date.
- If Resend fails, verify the API key and ensure `PAMS_EMAIL_FROM` uses a
  verified sending domain.
- Check the final daily-report lines for the resolved report date, recipient,
  and delivery result. GitHub masks configured secrets; never add diagnostic
  steps that echo environment variables or the database URL.

Any failed verification, database operation, market validation, rendering, or
delivery command terminates the job with a non-zero status.

## Daily report email delivery

PAMS can update the portfolio, build a report from persisted aggregate and
position snapshots, and deliver plain-text/HTML email. Resend is recommended
for personal installations because it requires only an API key and a verified
sender. Microsoft Graph remains available for enterprise Microsoft 365
environments. PAMS does not use basic SMTP authentication for Hotmail or
Outlook.com.

The V1.0 email report places the portfolio's signed daily profit/loss and
percentage immediately below its dates. That value aggregates the persisted
position movements produced by the normal holdings/quote snapshot workflow.
Its contributor ranking and holdings table expose each position's signed
daily P/L impact and percentage, ranked by absolute monetary contribution
rather than price-change percentage. Email-safe summary cards and explicit
green, red, or neutral styling preserve both color and signed values.
It also includes the most recent 30 available aggregate snapshots as total
stock market value and net stock equity, while plain text includes the same
dated values as a readable table. Histories shorter than 30 are valid; a
single snapshot produces an explicit no-trend-yet message.

The PNG is generated at high resolution. SMTP and Microsoft Graph keep the
multipart/related CID resource controlled by PAMS and mark it
`Content-Disposition: inline` without a downloadable filename. Resend never
receives a chart attachment or `contentId`: PAMS first upserts the PNG to a
public Supabase Storage object and renders its HTTPS URL in the HTML. The
displayed image scales to the email width for Outlook, Gmail, Apple Mail, and
mobile clients.

```bash
python -m pams daily-report send
python -m pams daily-report send --date 2026-07-22
python -m pams daily-report send --dry-run
python -m pams daily-report send --force
```

Automatic mode runs the existing idempotent market update and then uses the
latest live commonly ingestible TWSE/TPEx date. Production date resolution
queries official live publications and never ignores a newer trustworthy
dataset. A complete current-date persisted snapshot can be delivered without
another live probe. After a transient source outage, the report may use only a
complete non-future persisted aggregate and position snapshot, always labeled
with its actual date. Structural or source-date failures remain strict.

`pams status` labels persisted quote/snapshot dates separately from the latest
live TWSE date, live TPEx date, and live commonly ingestible date.

### Choosing an Email Transport

Set exactly one `PAMS_EMAIL_TRANSPORT` value. Only the selected adapter's
credentials are validated; sender and recipient are common to every adapter.
The CLI prints the selected adapter before delivery without printing
credentials.

Resend:

```dotenv
PAMS_EMAIL_TRANSPORT=resend
PAMS_RESEND_API_KEY=your-resend-sending-api-key
PAMS_SUPABASE_URL=https://your-project.supabase.co
PAMS_SUPABASE_SERVICE_ROLE_KEY=your-server-side-service-role-key
PAMS_REPORT_ASSET_BUCKET=pams-report-assets
PAMS_REPORT_ASSET_PREFIX=your-stable-random-deployment-prefix
PAMS_EMAIL_FROM=PAMS <reports@your-verified-domain.example>
PAMS_EMAIL_TO=recipient@example.com
```

Microsoft Graph:

```dotenv
PAMS_EMAIL_TRANSPORT=microsoft_graph
PAMS_MICROSOFT_CLIENT_ID=your-public-client-application-id
PAMS_MICROSOFT_TENANT=consumers
PAMS_MICROSOFT_TOKEN_CACHE=data/msal_token_cache.json
PAMS_EMAIL_FROM=your-account@example.com
PAMS_EMAIL_TO=recipient@example.com
```

SMTP:

```dotenv
PAMS_EMAIL_TRANSPORT=smtp
PAMS_SMTP_HOST=smtp.example.com
PAMS_SMTP_PORT=587
PAMS_SMTP_USERNAME=your-smtp-user
PAMS_SMTP_PASSWORD=your-local-secret
PAMS_EMAIL_FROM=sender@example.com
PAMS_EMAIL_TO=recipient@example.com
```

### Personal users: Resend (recommended)

Create a Resend API key restricted to sending email, verify a sending domain,
then copy `.env.example` to `.env` and configure:

```dotenv
PAMS_EMAIL_TRANSPORT=resend
PAMS_RESEND_API_KEY=your-resend-sending-api-key
PAMS_SUPABASE_URL=https://your-project.supabase.co
PAMS_SUPABASE_SERVICE_ROLE_KEY=your-server-side-service-role-key
PAMS_REPORT_ASSET_BUCKET=pams-report-assets
PAMS_REPORT_ASSET_PREFIX=your-stable-random-deployment-prefix
PAMS_EMAIL_FROM=PAMS <reports@your-verified-domain.example>
PAMS_EMAIL_TO=recipient@example.com
```

The `PAMS_EMAIL_FROM` domain must be verified in Resend. Resend's testing mode
may restrict delivery to the account owner's address until a domain is
verified. API keys are loaded as secret settings and are never included in
application output or transport errors.

Create a **public** Supabase Storage bucket named `pams-report-assets`, or set
`PAMS_REPORT_ASSET_BUCKET` to another public bucket. The service-role key is
used only by the server-side upload step and must remain a GitHub/local secret.
Set `PAMS_REPORT_ASSET_PREFIX` to a stable random value unique to the
deployment. If omitted, PAMS derives a stable opaque prefix from the deployment
configuration without exposing the key.

Chart objects use one deterministic path per report date:

```text
<prefix>/daily-report/YYYY-MM-DD/asset-change.png
```

Forced rebuilds upsert that same object rather than creating duplicates.
Public Storage URLs can be accessed by anyone who obtains the URL; object names
therefore contain no portfolio values, recipient addresses, account IDs, or
credentials. Unexpected Storage authentication, upload, or network failures
fail the delivery before Resend is called.

### Enterprise users: Microsoft Graph

1. In Microsoft Entra, register an application that supports personal
   Microsoft accounts or the required enterprise accounts.
2. Under **Authentication**, enable public-client flows. Device-code flow does
   not require a redirect URI or client secret.
3. Add only the delegated Microsoft Graph `Mail.Send` permission. The MSAL
   public-client flow also requests the standard `openid`, `profile`, and
   `offline_access` scopes needed for sign-in and silent token renewal.
4. Copy `.env.example` to `.env` and set:

```dotenv
PAMS_EMAIL_TRANSPORT=microsoft_graph
PAMS_MICROSOFT_CLIENT_ID=your-public-client-application-id
PAMS_MICROSOFT_TENANT=consumers
PAMS_MICROSOFT_TOKEN_CACHE=data/msal_token_cache.json
PAMS_EMAIL_FROM=your-account@hotmail.com
PAMS_EMAIL_TO=recipient@example.com
```

Complete first-time consent interactively:

```bash
python -m pams email authorize
```

The command prints Microsoft's verification URL and user code, waits for
consent, and stores the MSAL cache locally. It never prints access or refresh
tokens. The cache and `.env` are ignored by Git; keep both accessible only to
the local Windows user. Subsequent scheduled invocations acquire or refresh a
token silently. If the cache is absent or consent has expired, the
non-interactive send fails with instructions to run `email authorize` again.

The sender must be the personal account that grants consent. PAMS sends the
standards-compliant MIME message to Graph's `/me/sendMail` endpoint, preserving
both plain-text and HTML alternatives.

`PAMS_EMAIL_TRANSPORT=smtp` remains available for providers that
support authenticated STARTTLS. Configure the `PAMS_SMTP_*` variables shown in
`.env.example`; do not select SMTP basic authentication for Hotmail or
Outlook.com.

`--dry-run` requires only sender and recipient settings. It renders the
intended recipient and subject without starting OAuth, reading a token, making
a network connection, or creating a SENT record. Delivery is idempotent for
`(report type, report date, recipient)`; FAILED attempts remain retryable and
`--force` intentionally resends.

## Multi-market and currency valuation

PAMS can combine TWSE/TPEX holdings in TWD with opt-in US equities and ETFs in
USD. TWD remains the reporting currency. Native USD average cost and closing
price are preserved, while US cost basis, market value, and P/L are translated
with one persisted USD/TWD rate on or before the report date. This is
**constant report-date FX translation** and excludes historical FX gain/loss.
Portfolio trends can still move when the report-date FX rate changes.

US and Taiwan rows may have different quote dates because each uses its latest
completed eligible session. Missing FX never defaults to 1. Configure
`PAMS_US_MARKET_DATA_PROVIDER=alphavantage`,
`PAMS_FX_PROVIDER=alphavantage`, and `PAMS_ALPHA_VANTAGE_API_KEY`; their default
`disabled` values preserve Taiwan-only operation.

`Market.US` represents the portfolio market, while NASDAQ, NYSE, and NYSE Arca
are exchange/venue metadata and are not part of holding uniqueness. The current
Alpha Vantage adapters are replaceable and opt-in. Operational deployments must
account for the provider plan's request-per-minute and request-per-day limits;
the adapter performs one daily-series request per US symbol plus one FX request.
PAMS uses only completed daily closes and never labels intraday data as a close.
For the current MU and DRAM portfolio this is three requests per update run:
one request for each distinct US symbol and one USD/TWD request. A transient or
quota failure retains eligible persisted US quotes and FX rates; without an
eligible persisted value, the affected translated fields remain unavailable
and Taiwan valuation continues.
US symbol requests are paced conservatively to avoid provider call-frequency
envelopes. Response classification distinguishes valid time-series payloads,
authentication failures, rejected symbols, explicit quota/frequency messages,
generic provider information, and malformed documents. Each symbol is isolated:
a valid quote is retained even if another symbol fails.
Historical FX backfill is a separate explicit Application workflow; it does
not create portfolio snapshots or synthesize rates for weekends and holidays.

## Liability principal history

Schema v11 makes debt principal replayable from dated events:

```bash
python -m pams liability history
python -m pams liability history --liability-id liability-margin-financing
python -m pams liability principal --liability-id liability-margin-financing --date 2026-08-28
python -m pams liability interest --date 2026-08-29
```

The cutoff is inclusive. Same-day events use an explicit accounting sequence;
timestamps and identifiers never imply replay order.
