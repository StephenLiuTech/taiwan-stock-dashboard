# PAMS roadmap

## v0.2 — Domain and persistence

Typed domain models, portfolio and snapshot calculations, repository contracts, versioned SQLite storage, validated configuration, and safe initial portfolio seeding.

## v0.3 — Taiwan price ingestion

Implemented foundation: official TWSE/TPEx latest-only and historical date-query provider boundaries, normalization, provenance, quote persistence, and explicit portfolio refresh. Manual updates can retrieve an earlier official trading date while automatic updates retain cross-market latest-publication synchronization. Scheduling remains intentionally out of scope.

## v0.4 — Dashboard

Sprint 1 implements the local update CLI, JSON/human reporting, dry-run validation, explicit exit codes, and dependency composition. v0.4.1 adds latest-only source synchronization semantics plus operational `status` and `verify` commands. Automatic ingestion waits until both markets publish the same official date. Dashboard views remain a later sprint.

## v0.5 — Application layer

Sprint 1 introduces immutable application DTOs and dedicated update, portfolio-status, and system-verification use cases. Sprint 2 adds the single-page Streamlit portfolio dashboard, the `PortfolioOverview` read model, and persisted `PortfolioHistoryUseCase`. CLI, Dashboard, and future automation invoke workflows through composition; presentation entry points do not call repositories, providers, or `MarketDataEngine` directly.

The dashboard development workflow includes an isolated deterministic demo database command. It produces 30 synthetic history points and is protected from targeting the configured production database. Demo fixtures are not official market data.

## v0.5 — Notifications and dividends

Add explicit notification ports, dividend workflows, scheduling, consent, and delivery observability.

## v0.7 — Transaction engine

Sprint 1 adds deterministic moving weighted-average BUY/SELL accounting, realized P/L, immutable ledger results, and a protocol-driven dry-run holding projection use case. Sprint 2 adds manual transaction entry/listing, immutable holding change plans, migration warnings, and an explicit atomic apply boundary. Rebuild remains dry-run by default; bootstrap migration requires complete history or explicit unmatched-holding acknowledgement. Historical snapshots are never rewritten.

## v0.8 — Portfolio valuation engine

Sprint 1 complete: a pure Decimal-based `ValuationEngine`, immutable holding and portfolio valuation DTOs, application-owned quote loading and missing-quote validation, shared portfolio calculations, and human/JSON `pams portfolio valuate` output.

Sprint 2 complete: Dashboard 2.0 is a presentation-only consumer of one cached `PortfolioValuation`, with summary KPIs, position ranking, allocation, winners, losers, and a sortable holdings table.

Sprint 3 complete: the repository-free `DailyReportBuilder` prepares a structured, presentation-neutral report from `PortfolioValuation`; independent Markdown and semantic HTML renderers support CLI output and UTF-8 files without email, messaging, or scheduling.

## v1.0 — Deployment-ready system

Add production persistence, migrations, authentication, backups, monitoring, security review, deployment automation, and operational documentation.
