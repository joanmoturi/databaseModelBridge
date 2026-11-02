# Docker deployment and clean acceptance

Docker Desktop/Engine with Linux containers and Compose is an operator
prerequisite. On Windows, Docker's virtualization/WSL prerequisites must work
before ModelBridge can start. Do not disable platform security to run tests.

From a new clone:

```powershell
python scripts\configure.py
docker compose config --quiet
docker compose up --build --detach --wait
docker compose exec api modelbridge bootstrap --name "My workspace"
```

Keep the one-time token private. Defaults bind API/dashboard to host loopback
8000/3000; optional `MODELBRIDGE_API_BIND_PORT` and
`MODELBRIDGE_DASHBOARD_BIND_PORT` avoid conflicts. API-only:
`docker compose up --build --detach metadata upgrade api worker`.

Services: PostgreSQL 17.11 metadata, one-shot Alembic upgrade, API, durable worker,
dashboard. Redis is unnecessary: PostgreSQL holds jobs/outbox/checkpoints.
Metadata has a named persistent volume. The worker/API wait for successful
schema upgrade; API health and worker readiness have separate health checks.
`/health` is liveness; `/ready` requires current schema and fresh worker heartbeat.

Generated secrets are private `.env` values. Configure only documented variables
in `.env.example`; containers use the `metadata` hostname, not a developer path.
Database/webhook CIDR lists default empty. Loopback inside a container means that
container, not the host. Configure explicit test-network CIDRs and service names.
This Compose profile is local development, not an accepted production deployment.

`docker compose restart` preserves metadata. `docker compose down` preserves the
volume; **do not use `down --volumes` on valuable metadata**. Follow OPERATIONS.md
for backup/restore and upgrades. Preserve the Fernet keyring with metadata backups.
Stopping a worker does not roll back external rows; see BACKGROUND_JOBS.md.

## Reproducible clean-room attempt

```powershell
python scripts\clean_compose_acceptance.py
```

This helper clones locally without shared Git objects, overlays the exact
unpublished non-ignored working files without committing, generates a fresh
configuration, uses a unique project/empty volumes and checks Compose syntax.
It rejects remote Docker endpoints, builds without cache, and, when a working
daemon is available, checks health/readiness/dashboard proxy, independently
queries metadata and verifies restart persistence. Only its owned project and
temporary files are cleaned up. Existing developer `.env`, index, branch and
volumes are not used. Dependency installation happens inside the Dockerfiles.

This is not a clean remote public download: unpublished fixes cannot be cloned
from GitHub until separately authorized. It is also not a substitute for the
public API real-database migration tests.

**Current result:** clean clone, fresh configuration and Compose validation
passed. Container build/start acceptance is blocked by the local Docker backend;
the installed CLI is not evidence that a functioning Linux engine exists.
No successful image build, empty-volume application startup, container restart
or containerized migration is claimed. See API_ACCEPTANCE_REPORT.md.
