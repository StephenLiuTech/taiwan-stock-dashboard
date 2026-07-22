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

## v1.0 — Deployment-ready system

Add production persistence, migrations, authentication, backups, monitoring, security review, deployment automation, and operational documentation.
