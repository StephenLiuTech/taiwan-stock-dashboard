# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [1.0.0] - 2026-07-23

### Added

- Transaction ledger and deterministic holding projection.
- Official TWSE and TPEx latest and historical market-data ingestion.
- SQLite quote, aggregate snapshot, and position snapshot persistence.
- Canonical Decimal-based Portfolio Valuation capability.
- Pure snapshot-based Analytics Engine foundation.
- Immutable portfolio analytics and consecutive daily-return DTOs.
- Analytics application use case with inclusive period filtering.
- Human and JSON `analytics portfolio` CLI output.
- Dashboard analytics supplied by `AnalyzePortfolioUseCase`.
- Markdown and HTML daily-report analytics summaries.
- Operational CLI status, verification, dry-run, and JSON modes.
- Synthetic demo-data workflow.
- Project governance, contribution guidance, security policy, and CI.

### Changed

- Analytics formatting is shared at the Presentation boundary.
- Dashboard and report presentation consume `PortfolioAnalytics` without
  recalculating return or drawdown.
- Analytics repository and processing failures are translated into controlled
  presentation states.
- `PortfolioValuation` is the canonical valuation contract for CLI, Dashboard,
  and reports.
- Portfolio weights are calculated by `ValuationEngine` instead of the
  Application Layer or snapshot adapter.
- Valuation repository failures are translated into a typed Application error.
- First-run read commands no longer create missing SQLite databases.
- Empty holdings, missing quotes, and missing snapshots remain distinct from
  valid zero-valued financial results.

### Quality

- Python 3.11 and 3.12 supported.
- Automated Black, Ruff, Pytest, and Compileall validation.
- Lossless Decimal JSON serialization.
- Controlled user-facing infrastructure errors.

### Known limitations

- Taiwan-listed equities and SQLite persistence only.
- No allocation analytics, benchmarks, cash-flow-adjusted performance, FX
  conversion, multi-asset support, scheduling, or notification delivery.

## [0.8.0] - 2026-07-22

### Added

- Portfolio Valuation Engine
- Portfolio valuation application use case
- Portfolio valuation CLI
- Dashboard 2.0
- Portfolio Summary
- Largest Positions
- Portfolio Allocation
- Top Winners
- Top Losers
- Full Portfolio Table
- Daily Report Builder
- Markdown Report Renderer
- HTML Report Renderer
- Report generation CLI

### Changed

- Dashboard now consumes the Application Layer instead of repositories, SQL, providers, or domain services directly.
- Portfolio valuation calculations are centralized through `ValuationEngine`.
- Existing portfolio workflows delegate valuation calculations to the shared valuation engine.
- Reporting was refactored from a single module into a builder and renderer package.
- Legacy reporting functions remain backward compatible through re-exports.

### Fixed

- Consistent `Decimal` handling across valuation, dashboard, CLI, and reporting.
- Deterministic ordering in reports.
- UTF-8 report file output.

### Quality

- 191 automated tests passing.
- Black passing.
- Ruff passing.
- Compileall passing.
- `git diff --check` passing.

### Notes

This is the first production-ready portfolio valuation and reporting release.
