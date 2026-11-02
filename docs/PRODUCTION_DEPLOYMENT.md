# Production deployment: unverified guidance

**Not ready for production.** Real durable migrations, reconciliation and signed
webhooks passed native integration tests. Clean Compose installation, production
TLS/egress and production deployment acceptance have not passed.

For future VM/container evaluation: terminate TLS at a properly configured reverse
proxy, bind internal API/database ports privately, enforce upstream body/time limits,
restrict network egress to database CIDRs and protect registration behind provisioning
controls. Avoid logging bodies, authorization headers, connection strings or query
strings containing secrets. Do not trust arbitrary forwarded client headers.
The current direct-IP limiter needs a verified trusted-proxy design before shared
internet deployment; blindly increasing limits is not a security solution.

Production configuration requires verified metadata TLS and HTTPS CORS origins;
external databases require `sslmode=verify-full`, using system trust roots.
Custom certificate/mTLS provisioning remains unverified. Store encryption keys
and database passwords in a secret manager; preserve all keys required to decrypt
existing records. Back up metadata and encryption keys separately with access control.

OPERATIONS.md documents metadata dump/restore and schema upgrades. Test restoration
to a separate database before changing a live deployment. Never downgrade destructively.
Stop API/workers before upgrade, apply packaged Alembic head, restart and check readiness.
Database migration rights belong to the upgrade operator; runtime least-privilege
grants still need independent verification.

Monitor safe application logs, health, readiness and audit history.
Rate buckets persist in PostgreSQL but expired buckets/sessions/snapshots currently
lack maintenance/retention jobs. Discovery size bounds exist, but global concurrent
connection/discovery budgets and worker scaling are not certified.
Redis is unnecessary: PostgreSQL persists the queue/outbox. Native tests verified
worker termination/restart and two-worker recovery without duplicate target rows.
That is not a distributed/large-scale deployment certification. See
BACKGROUND_JOBS.md and its transaction/retry/monitoring boundaries.

Publication requires authorization. Candidate local artifact commands are `uv build`
and `docker compose build`; verify reproducibility and registry-neutral locks first.
Only after acceptance/authorization: tag verified images and upload distributions
using protected registry credentials. No registry publication was performed.
