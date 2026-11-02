# Known limitations

Real PostgreSQL migration, durable recovery, reconciliation and signed webhooks
are native integration tested. **Not production-ready:** clean container
build/start acceptance is blocked by the local Docker backend. No other connector
is certified. PostgreSQL 17.11 is the only database version actually exercised.
See MIGRATION_EXECUTION.md for the deliberately narrow scalar/single-key/DAG
profile and dry-run/server-constraint boundaries.

The dashboard shows status/workspaces/audit, not a guided migration editor.
Automatic recommendations, standalone transformation preview, CDC, cyclic or
composite relationships, rich/custom types, schema creation and destructive
overwrite are absent. Explicit reconciliation is synchronous. Sample migrations
do not automatically expand parent closure.

No distributed transaction/exactly-once guarantee. Sources must be quiescent;
destination triggers must not have non-idempotent external side effects.
1,000-row recovery coverage is not a large-dataset benchmark. Drivers may
materialize an oversized source row before the engine rejects its size.

Production TLS, custom CA/mTLS, cloud deployment, proxy-aware rate limiting,
global concurrency budgets, metadata retention and runtime least-privilege
database roles remain unverified/incomplete. Account reset/MFA/session
administration and owner transfer are absent. Do not expose the development
profile publicly.

Container base images use version tags, not pinned digests. No image build or
public-network clean install is certified. This host uses an approved dependency
mirror; TLS verification was not disabled. Hosted CI has not run for unpublished
changes. Historical root reports intentionally describe earlier implementation.

The upstream TestClient/httpx deprecation warning remains unsuppressed.
