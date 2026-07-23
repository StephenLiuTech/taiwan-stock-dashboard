# PAMS roadmap

## Release status

| Version | Status | Scope |
|---|---|---|
| v0.2 | Complete | Domain models, SQLite repositories, schema, configuration |
| v0.3 | Complete | TWSE/TPEx ingestion, normalization, historical providers |
| v0.4 | Complete | Operational CLI, market availability, status and verification |
| v0.5 | Complete | Application layer, immutable DTOs, initial dashboard |
| v0.6 | Deferred | Notifications and dividend workflow expansion |
| v0.7 | Complete | Transaction engine, ledger CLI, atomic holding rebuild |
| v0.8.0 | Release-ready | Valuation Engine, Dashboard 2.0, Daily Report Engine |
| v0.9 | In development | Deterministic snapshot analytics foundation |

## v0.8.0 — Portfolio valuation and reporting

Release date: **2026-07-22**

### Sprint 1 — Portfolio Valuation Engine

- Pure Decimal-based `ValuationEngine`
- Immutable `HoldingValuation` and `PortfolioValuation`
- Repository-driven `ValuatePortfolioUseCase`
- Typed missing-quote handling
- Human and JSON portfolio valuation CLI
- Existing snapshot workflow delegated to shared valuation calculations

### Sprint 2 — Dashboard 2.0

- Presentation-only Streamlit boundary
- One cached valuation execution per page load
- Portfolio Summary and Largest Positions
- Allocation by holding
- Top Winners and Top Losers
- Sortable full Portfolio Table

### Sprint 3 — Daily Report Engine

- Presentation-neutral `DailyReportBuilder`
- Deterministic largest-position, winner, loser, and portfolio ordering
- Independent Markdown renderer
- Independent semantic HTML renderer
- CLI stdout and UTF-8 file output

See the [v0.8.0 release notes](releases/v0.8.0.md) and
[project changelog](../CHANGELOG.md).

## Post-v0.8 direction

Future work requires separate design and acceptance criteria. Candidate areas
remain intentionally outside v0.8.0:

- dividend and corporate-action workflows
- opt-in report delivery and scheduling
- broker imports and reconciliation
- authentication and multi-user boundaries
- production database adapters, backups, and deployment operations

No future item is implied to be implemented by this release.

## v0.9 — Analytics

### Sprint 1 — Basic performance foundation

- Pure `AnalyticsEngine` over aggregate portfolio snapshots
- Starting and ending values
- Absolute profit or loss and simple total return
- Consecutive snapshot daily returns
- Peak and trough values
- Running-peak maximum drawdown

This sprint intentionally excludes cash-flow adjustment, TWR, MWR, IRR, XIRR,
volatility, Sharpe Ratio, benchmark comparison, charts, and Dashboard changes.

### Sprint 2 — Analytics Application Layer

- `AnalyzePortfolioUseCase` over the existing snapshot repository protocol
- Inclusive optional start and end date filtering
- Typed application errors for invalid periods, empty history, and repository
  failures
- Human and lossless JSON `analytics portfolio` CLI output

### Sprint 3 — Presentation Integration

- Dashboard analytics supplied by `AnalyzePortfolioUseCase`
- Snapshot-period metrics and application-provided daily-return chart
- Markdown and HTML daily-report analytics summaries
- Shared Decimal-aware formatting at the Presentation boundary
- Controlled empty, invalid-period, repository, and processing states

Sprint 3 adds no financial methodology. Allocation analytics, benchmarking,
cash-flow-adjusted return, TWR, MWR, IRR, XIRR, volatility, Sharpe and Sortino
ratios, beta, correlation, sector or currency exposure, multi-asset support,
import adapters, automation, and notifications remain unimplemented.
