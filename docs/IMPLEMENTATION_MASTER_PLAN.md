# Authoritative implementation specification

Date: October 31, 2026. Status: development in progress.

## Inputs and conflict resolution

All thirteen original root-level reports were read and remain unchanged. They
record an empty repository, not an existing architecture or implemented features.
The implementation master prompt is the actual product specification. Prior
reports are historical evidence and must not be interpreted as current test results.
Security/data safety wins over convenience; runtime tests win over planned claims.

## Product scope

ModelBridge is an API-first, self-hosted application-data migration and
reconciliation platform. Initial target: PostgreSQL source to PostgreSQL or
Supabase's PostgreSQL interface. No universal database compatibility, external AI
record transfer, arbitrary transformation code, or implicit destructive writes.
The initial release requires the real Connect -> Discover -> Compare -> Map ->
Transform -> Validate -> Dry run -> Sample -> Execute -> Reconcile workflow.

## Architecture and domain

Modular monolith; Python 3.12+, FastAPI/Pydantic, SQLAlchemy 2, Alembic,
PostgreSQL metadata; Next.js/strict TypeScript dashboard; shared application
services for API/CLI/worker. Domain is framework-independent. One API and one
worker are the initial documented deployment profile. Use PostgreSQL coordination
before introducing Redis; no distributed execution claim until tested.

Domain entities: workspace, scoped access credential/role, connection and encrypted
credential envelope, schema snapshot, comparison, versioned approved mapping,
transformation specification, validation, migration job, batch/checkpoint,
crosswalk, rejected record, reconciliation, audit event, worker heartbeat.
Bootstrap creates a workspace and owner access credential explicitly; no anonymous
admin API or automatic default user. Audit events and heartbeat form the foundation.

## Connector architecture

Typed capability interfaces for test/discover/read/write/transactions. PostgreSQL
is the first real implementation. Supabase uses PostgreSQL protocol with TLS and
documented network/pooler constraints; a local PostgreSQL test does not establish
live Supabase compatibility. Pin resolved IPs to prevent DNS rebinding and reject
targets outside configured network policy. Credential encryption uses a required
key ring and authenticated encryption with explicit rotation.

## API and lifecycle

Versioned `/api/v1`; health/readiness/version separate from authenticated domain
endpoints. Consistent safe error envelopes, pagination, scoped roles, correlation
IDs, explicit startup migration verification. CLI calls shared services for local
administration and public API for migration operations.

Migration states: draft -> reviewed -> validated -> queued -> running ->
succeeded/failed/cancelled. Dry runs do not write. Bounded ordered batches, stable
identifier crosswalks, durable checkpoints, explicit conflict policies and
reconciliation. Never claim cross-database atomicity. Retry uncertain commits only
with an independently verifiable idempotency design; no silent replay or overwrite.

## Security model

High-entropy scoped credentials stored only as hashes; fail-closed configuration;
required encryption key ring; no raw input/credentials in errors or logs; owner,
administrator, migration engineer and auditor roles. TLS at the deployment edge;
outbound policy, input validation, safe SQL composition, scoped queries, audit
history, controlled transforms. No production/customer data during development.

## Sequential milestones and dependencies

| Milestone | Deliverable | Prerequisite / acceptance |
|---|---|---|
| 1 | Foundation, API, dashboard, metadata, migrations, worker, Compose, CI | Real startup, migrations, endpoints, complete Compose and clean install |
| 2 | Secure connection management and PostgreSQL/Supabase connectors | 1; real disposable DB connections and encrypted/scoped storage |
| 3 | Schema discovery, snapshots, drift | 2; verified relationships and persistent snapshots |
| 4 | Comparison, explainable recommendations, reviewed mappings | 3; tested ambiguity and approval/versioning |
| 5 | Registered deterministic transformations/previews | 4; edge-case and property tests |
| 6 | Bounded profiling and validation | 5; required fields, referential integrity, loss detection |
| 7 | Durable safe execution and recovery | 6; dry/sample/full, restart, retries, cancellation |
| 8 | Reconciliation and audit exports | 7; independent destination verification |
| 9 | Complete API and CLI | 8; public workflow tested |
| 10 | Guided dashboard workflow | 9; actual backend data and browser E2E |
| 11 | Complete multi-tenancy/security acceptance | 10; adversarial execution tests |
| 12 | Observability and operating procedures | 11; restore/upgrade/fault exercises |
| 13 | Additional connectors | Initial workflow verified; certify individually |

Security is implemented from milestone 1, not postponed to milestone 11.
Do not mark milestone 1 complete merely because its files exist. If local
infrastructure blocks a required test, continue independent foundation work and
record the gate as blocked; do not skip dependent milestones.

## Testing strategy and acceptance

Unit/config/security negative tests, API tests, real PostgreSQL Alembic and service
tests, frontend unit/lint/type/build, TCP full-stack smoke, container/clean-install
checks, later synthetic student migration, fault injection, independent destination
SQL, connector and performance measurements. Skipped/blocked != passed. CI is
configuration until an actual hosted run occurs. No readiness without tested profile.

## Known limitations and deferred features

No implementation existed at start. Container runtime and PostgreSQL were absent
from PATH. Native development tests are not VM-isolated acceptance. No live
Supabase credentials are available. Distributed workers, arbitrary custom policies,
cycles, additional connectors and SDKs require explicit implementation/testing;
none may be advertised before that. License chosen for new source: MIT, with
third-party license compliance required before distribution.
