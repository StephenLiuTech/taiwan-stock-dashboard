# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- Pure snapshot-based Analytics Engine foundation.
- Immutable portfolio analytics and consecutive daily-return DTOs.
- Analytics application use case with inclusive period filtering.
- Human and JSON `analytics portfolio` CLI output.

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
