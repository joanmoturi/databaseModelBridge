# Self-hosting

Supported evaluation profile: native Windows, Python 3.12, disposable PostgreSQL
17.11, loopback API and durable migration worker. Not production-certified.
README contains exact native dependency, upgrade and startup commands.
CLI installation is the Python package's `modelbridge` entry point.

Compose defines API, durable worker, metadata PostgreSQL, upgrade job and optional
dashboard. PostgreSQL persists jobs/outbox; Redis is not required.
Start without dashboard using:

```powershell
python scripts\configure.py
# Edit the documented registration/network settings in .env if needed.
docker compose up --build -d api worker
```

Dependencies start metadata and schema upgrade automatically. `/health` checks API
availability; `/ready` requires current metadata and a fresh heartbeat.
The API can run without the dashboard; the worker is still required for readiness.

The generator refuses to overwrite secrets. See `.env.example` for every server
setting. Keep secrets outside version control. Connection allowlist defaults empty.
Containers must reach permitted database addresses from their own network namespace.
Never connect to existing customer/production data during evaluation.
For API accounts, follow API_AUTHENTICATION.md's operator registration-code command
and private recipient verification. Registration opt-in alone is insufficient.

Compose clean installation/image builds remain blocked on this host's unusable
Docker Linux backend (CLI is installed). Native checks are not a VM equivalence
claim. Preserve the metadata volume; `down -v` destroys it.
See DOCKER_DEPLOYMENT.md for the clean-snapshot acceptance helper.
