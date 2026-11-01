# ModelBridge

Application-data migration and reconciliation platform, **development in progress**.
The runnable foundation has a PostgreSQL metadata database, API, scoped workspace
credentials, audit events, heartbeat worker, CLI and live status dashboard.
**Data migration and database connectors are not implemented yet. Do not use with
production/customer databases.** See `docs/IMPLEMENTATION_STATUS.md` for evidence.
The thirteen original root reports are historical assessments, preserved unchanged.

## Requirements

Development: Python 3.12-3.14, uv 0.13.6, Node 26.13.0/npm 11, PostgreSQL 17,
or Docker Engine/Desktop with Compose v2 for the local Compose profile.
Application containers target Linux; native Windows development uses PowerShell.
Docker Desktop/VM configuration is an operator prerequisite, not installed by scripts.
No Redis is required by the heartbeat-only foundation. Remote deployment and
production security acceptance are not yet supported or verified.

## Docker local deployment

From the repository root (PowerShell):

```powershell
python scripts\configure.py
docker compose up --build -d
docker compose exec api modelbridge bootstrap --name "My workspace"
```

The configuration command creates `.env` with random local secrets and refuses to
overwrite an existing file. The bootstrap command intentionally displays a newly
generated owner token once; store it securely. Never paste it into logs or reports.
Open `http://localhost:3000`, enter the token in the masked access field, and inspect
your workspace and audit history. API docs: `http://localhost:8000/api/docs`.
Health: `/health`; readiness: `/ready`; version: `/version`. Readiness returns 503
unless schema is current and the configured worker heartbeat is fresh.

Compose starts metadata PostgreSQL, an explicit one-shot schema upgrade, API,
worker and dashboard. Published ports bind to loopback. Data lives in the named
metadata volume; `docker compose down` preserves it. **Do not use `down -v` unless
you intentionally want to destroy disposable metadata.** No migrations target
external application databases at this stage.

## Native development

First provision an owned disposable PostgreSQL database and user; use a strong
password and grant access only to that metadata database.

```powershell
python scripts\configure.py
# Edit documented MODELBRIDGE_DATABASE_URL in .env to your disposable PostgreSQL.
uv venv --python 3.12
uv pip sync --require-hashes requirements-dev.lock
uv pip install --no-deps -e .
uv run --no-sync modelbridge db upgrade
uv run --no-sync modelbridge bootstrap --name "My workspace"
uv run --no-sync uvicorn modelbridge.api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
# A second terminal, repository root:
uv run --no-sync python -m modelbridge.worker
# A third terminal:
Set-Location frontend
npm ci
npm run dev
```

The frontend proxies a strict route allowlist to `MODELBRIDGE_API_URL` (default
`http://127.0.0.1:8000`). Tokens remain in memory, not localStorage. This is a
single-workspace access-credential foundation, not finished account onboarding.
The worker writes heartbeat metadata only and does not execute migrations.

## Verification

```powershell
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv build
Set-Location frontend
npm test
npm run lint
npm run typecheck
npm run build
```

PostgreSQL integration tests skip unless both
`MODELBRIDGE_TEST_DATABASE_URL` and `MODELBRIDGE_TEST_ALLOW_DATABASE_WRITES=true`
are set. Use only a disposable database; tests isolate objects in random schemas.
`uv run --no-sync python scripts\smoke.py` requires configured metadata and starts/stops its
own API/worker on unused loopback ports. It verifies real readiness, scoped API
access and metadata persistence; it is not a clean VM/Compose acceptance test.
Add `--browser` after building the frontend and running `npx playwright install chromium`
to exercise the live dashboard through the real API.

`requirements.lock` and `requirements-dev.lock` pin transitive versions and artifact
hashes without environment-specific registry URLs. `uv.lock` is a local resolver
artifact and is intentionally excluded: this host requires an organization package
mirror for downloads. On restricted networks, configure an approved package index;
never disable TLS verification. Public-network clean installation remains a release
acceptance requirement.

## Configuration and operations

See `.env.example` for every environment variable, `docs/OPERATIONS.md` for startup,
backup/restore, upgrade and recovery, and `docs/SECURITY_ARCHITECTURE.md` for
security boundaries. MIT license; see LICENSE and CONTRIBUTING.md.
Do not publish images/packages or push this implementation without authorization.
