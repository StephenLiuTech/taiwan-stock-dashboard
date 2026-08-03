# PAMS Product Vision

Version: Draft v1.0

## What is PAMS?

PAMS (Personal Asset Management System) is a platform for managing personal
financial assets.

It is designed around a transaction-first architecture where every portfolio
state can be reproduced from historical financial events.

PAMS is not a stock tracker.

PAMS is not a broker.

PAMS is not an accounting system.

PAMS is a personal asset management platform.

## Product Goals

PAMS aims to provide:

- Accurate portfolio valuation
- Reproducible historical snapshots
- Reliable performance analytics
- Multiple presentation interfaces
- Extensible data adapters

Every feature should strengthen one of these goals.

## Design Principles

### 1. Transaction is the single source of truth

Portfolio state must always be reproducible from historical transactions.

Snapshots are derived.

Analytics are derived.

Reports are derived.

No duplicated business truth.

### 2. Domain before UI

The Domain Model is the product.

CLI, Dashboard and Reports are only consumers.

UI must never contain business rules.

### 3. Pure business logic

Business calculations must not depend on:

- SQLite
- Files
- Streamlit
- CLI
- HTTP
- Pandas

The Domain Layer should be deterministic and testable.

### 4. Application orchestrates

```text
Repository
    ↓
Domain Engine
    ↓
DTO
```

Application Layer coordinates this flow and never performs business
calculations.

### 5. Infrastructure is replaceable

SQLite is an implementation detail.

Future replacements may include:

- PostgreSQL
- MySQL
- Cloud Storage

Domain code should remain unchanged.

### 6. Presentation is disposable

Presentation includes:

- CLI
- Dashboard
- HTML
- Markdown
- REST API
- Mobile

Presentation may change without affecting the Domain.

## Domain Model

```text
Portfolio
├── Asset
├── Transaction
├── Holding
├── Valuation
├── Snapshot
└── Analytics
```

## Layered Architecture

```text
Presentation
    ↓
Application
    ↓
Domain
    ↓
Repository
    ↓
Infrastructure
```

Dependencies always point downward.

No upward dependency is allowed.

## Domain Engines

Current engines:

- Transaction Engine
- Valuation Engine
- Analytics Engine

Future engines:

- Allocation Engine
- Risk Engine
- Forecast Engine

Each engine must remain independent.

`PortfolioValuation` is the canonical current-valuation result. CLI, Dashboard,
reports, and future domain capabilities consume it through the Application
Layer rather than reproducing valuation calculations.

## Asset Types

Current:

- Taiwan Stocks

Planned:

- US Stocks
- ETF
- Bond
- Crypto
- Cash
- Gold
- Real Estate

All assets should share a common abstraction.

## Financial Events

Financial events include:

- Buy
- Sell
- Dividend
- Deposit
- Withdrawal
- Fee
- Tax
- Interest
- Transfer

Future features should model events rather than hard-code asset behavior.

## Presentation Layer

Supported interfaces:

- CLI
- Dashboard
- Report
- Email delivery
- JSON API (future)

All interfaces consume the same Application Layer.

No duplicated business logic.

Email delivery is replaceable infrastructure behind an application transport
protocol. Personal installations can use Resend, while enterprise Microsoft
accounts can use delegated Graph OAuth2 rather than password-based SMTP.
Provider authentication and credential storage do not enter the Domain.
Report images use replaceable asset-storage infrastructure: MIME-capable
transports may consume CID assets, while REST transports may consume published
HTTPS assets without changing report or portfolio calculations.
The V1.0 daily report consumes the same persisted position and aggregate
snapshot facts as the other presentation interfaces, including signed daily
movement, position contribution impact, and a locally embedded recent
asset-history chart.
Automatic delivery resolves official market availability before selecting a
persisted report snapshot, preserving the principle that snapshots are
derived facts rather than a source of market-date truth.
Transaction and holdings CLI queries follow the same principle: holdings are
projected from trade-date-effective ledger events and current financial values
come from the shared valuation engine.

## Roadmap

### v0.8

Core Portfolio Management

Completed.

### v0.9

Analytics Foundation

Completed.

### v1.0

Personal Asset Management Platform

Available:

- Stable Domain
- Stable Application Layer
- Multiple Presentation Layers
- SQLite and PostgreSQL persistence
- Automated cloud report delivery
- Taiwan equity ledger, valuation, analytics, Dashboard, and reports

### v2.0

Multi-Asset Platform

Planned:

- Multiple asset classes
- Benchmark analytics
- Cash flow analytics
- Risk engine

## Non-Goals

PAMS will not become:

- Online brokerage
- Order execution system
- ERP
- Accounting software

These responsibilities belong to external systems.

## Definition of Done

A feature is complete only if:

- Domain is clean
- Tests pass
- Documentation updated
- Architecture remains consistent

Features that violate architecture should be rejected even if they work.
