# Architecture actually being implemented

The Python package has domain, application, infrastructure, API and CLI boundaries.
Domain role definitions have no web/database dependency. Application services
enforce scoped credentials and permissions. SQLAlchemy persists metadata;
Alembic upgrades are an explicit deployment step. FastAPI owns resource lifecycle.
Next.js consumes actual API state; it does not synthesize migration metrics.

API and worker run as separate processes. The foundation worker only writes a
durable heartbeat: it is not yet a migration executor. PostgreSQL coordination
avoids a redundant Redis dependency at this stage. No multi-worker claim.

The initial Compose profile uses a PostgreSQL service, one-shot upgrade service,
API, worker and dashboard, loopback-bound published ports, persistent metadata
volume, non-root application containers and health-based startup dependencies.

ADR-001: use PostgreSQL metadata for both development and deployment, not a silent
SQLite fallback. Tests needing PostgreSQL are explicitly gated on disposable DBs.
ADR-002: fail API startup on absent/stale schema rather than creating tables
implicitly; readiness also checks schema and worker freshness.
ADR-003: scoped opaque tokens precede account/session UI. This is a development
authentication foundation, not completed account management.
