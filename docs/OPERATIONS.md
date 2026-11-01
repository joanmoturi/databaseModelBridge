# Foundation operations

Only local development is supported at this stage. See README for startup.
Secrets generated into .env must be protected by operator-owned file permissions
(Windows ACLs; mode 0600 where supported). Back up the key ring separately from
the database; losing keys loses access to future encrypted connection envelopes.

## Upgrade

Stop API and worker, back up metadata, review changes, rebuild locked artifacts,
run `modelbridge db upgrade`, then start worker and API. API/worker refuse stale
schema. Initial schema has no safe destructive downgrade: restore a verified
backup into a separate database instead. Do not run concurrent upgrade processes.

## Metadata backup and restore

For the local Compose profile:

```powershell
docker compose exec metadata pg_dump -U modelbridge -d modelbridge -Fc -f /tmp/modelbridge.dump
docker compose cp metadata:/tmp/modelbridge.dump .\modelbridge.dump
```

The dump contains sensitive metadata and credential hashes: protect it and keep it
out of Git. Restore only into a separately provisioned empty disposable database
using `pg_restore --no-owner --exit-on-error`, then point documented metadata
configuration at it. Never restore over a live user's database automatically.
Verify schema, workspace/audit counts, owner access, and worker heartbeat after
restore. These instructions require an executed recovery test before release.

## Diagnostics and failure recovery

`/health` checks API liveness only. `/ready` checks current metadata schema and
configured worker freshness. Missing/stopped/stale worker yields 503. Use
`modelbridge ready` for JSON and a nonzero failure code. Worker retries heartbeat
on database errors; it does not retry migration jobs (not implemented).

`docker compose logs api worker upgrade` shows fixed structured events. Access
logs are disabled to avoid logging request inputs. Correlation ID appears in
API response headers. Do not paste secrets or raw environment/Compose config
into reports. No job metrics/tracing are implemented yet.

## Production limitations

Compose is a loopback local development profile. Production needs a TLS edge,
verified metadata TLS (`sslmode=verify-full` and trusted CA), key custody, scoped
database permissions, backups, alerting and executed security/restore acceptance.
Do not expose this development deployment publicly.
