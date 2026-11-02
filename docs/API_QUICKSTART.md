# First complete API migration (disposable PostgreSQL only)

Follow README.md or DOCKER_DEPLOYMENT.md. Clean Compose acceptance is still
blocked on the tested host; native PostgreSQL acceptance has executed.
Set explicit database CIDRs and restart API/worker. For native loopback:
`MODELBRIDGE_ALLOWED_DATABASE_NETWORKS=["127.0.0.1/32"]`. Compose needs explicitly
permitted database service addresses, not host loopback. Do not use real data.

Create two **independent disposable PostgreSQL databases**, source and destination,
with separate scoped users. Source needs CONNECT/USAGE/SELECT. Destination needs
CONNECT/USAGE/SELECT/INSERT, not DROP/DELETE/UPDATE or schema-owner privileges.
Run the following initialization as their disposable database owner, not through
ModelBridge. The destination schema must already exist.

Source:

```sql
CREATE TABLE students(sid integer PRIMARY KEY, legacy_name text NOT NULL, joined text);
INSERT INTO students VALUES
  (1,' Synthetic One ','31/10/2026'),(2,'Synthetic Two',NULL);
```

Destination:

```sql
CREATE TABLE students(id uuid PRIMARY KEY, name text NOT NULL, joined_on date);
```

Use the documented operator bootstrap token from `modelbridge bootstrap`, placed
privately in `MODELBRIDGE_API_TOKEN`, and its workspace UUID in
`MODELBRIDGE_WORKSPACE_ID`. This is a complete workspace API credential; no
account registration is needed for this route. Alternatively enable registration,
privately issue an email-bound registration code with
`modelbridge registration-code --email <recipient>`, register/login and create
a workspace as described in API_AUTHENTICATION.md.

Set `MODELBRIDGE_CLIENT_URL=http://127.0.0.1:8000` and environment variables
`SOURCE_HOST`, `SOURCE_PORT`, `SOURCE_DATABASE`, `SOURCE_USERNAME`,
`SOURCE_PASSWORD` and the corresponding `DESTINATION_*` variables.
`DATABASE_SSLMODE` defaults to verify-full; explicitly choose disable **only**
for disposable development databases without TLS.

Install the package using README's pinned dependency process, start API/worker,
and execute:

```python
import os
from uuid import uuid4
from pydantic import SecretStr
from modelbridge.contracts import ConnectionInput
from modelbridge.migration_contracts import JobInput, PlanInput, TableMapping, FieldMapping
from modelbridge.sdk import ModelBridge

with ModelBridge(
    os.environ["MODELBRIDGE_CLIENT_URL"],
    token=SecretStr(os.environ["MODELBRIDGE_API_TOKEN"]),
    workspace_id=os.environ["MODELBRIDGE_WORKSPACE_ID"],
) as api:
    snapshots = []
    for role in ("SOURCE", "DESTINATION"):
        connection = api.save_connection(
            ConnectionInput(
                name=role,
                host=os.environ[role + "_HOST"],
                port=int(os.environ.get(role + "_PORT", "5432")),
                database=os.environ[role + "_DATABASE"],
                username=os.environ[role + "_USERNAME"],
                password=SecretStr(os.environ[role + "_PASSWORD"]),
                sslmode=os.environ.get("DATABASE_SSLMODE", "verify-full"),
            )
        )
        assert api.test_connection(connection.id).connected
        snapshots.append(api.discover(connection.id))
    api.compare(snapshots[0].id, snapshots[1].id)
    plan = api.create_plan(
        PlanInput(
            source_snapshot_id=snapshots[0].id,
            destination_snapshot_id=snapshots[1].id,
            tables=[
                TableMapping(
                    source_table="students",
                    destination_table="students",
                    source_key="sid",
                    destination_key="id",
                    fields={
                        "id": FieldMapping(source="sid", transform="uuid"),
                        "name": FieldMapping(source="legacy_name", transform="trim"),
                        "joined_on": FieldMapping(source="joined", transform="date_dmy"),
                    },
                )
            ],
        )
    )
    api.validate_plan(plan.id)
    api.approve_plan(plan.id)
    for mode in ("dry_run", "sample", "full"):
        job = api.create_job(
            JobInput(plan_id=plan.id, mode=mode, batch_size=2, sample_size=1),
            idempotency_key=uuid4().hex,
        )
        finished = api.wait(job.id, timeout=120, interval=2)
        assert finished.state == "completed", (finished.state, finished.error_code)
    report = api.reconcile(job.id)
    assert report.result["consistent"]
    print("Job:", job.id, "Report:", report.id)  # identifiers only, no secrets
```

Independently query destination after dry_run to assert it is still empty (pause
the loop after that mode for a manual check), after sample to assert one row,
and after full:

```sql
SELECT count(*), count(DISTINCT id) FROM students; -- 2, 2
SELECT name, joined_on FROM students ORDER BY name;
-- Synthetic One | 2026-10-31
-- Synthetic Two | NULL
```

Download JSON/CSV through `/api/v1/workspaces/{workspace_id}/reports/{report_id}`
and its `/csv` suffix. Keep report data private. For automation, the real TCP
public API acceptance test additionally verifies dry-run emptiness, five related
tables, malformed records, retry, worker restart and discrepancy injection:

```powershell
# Only an owned disposable metadata DB with CREATEDB permission for this fixture.
$env:MODELBRIDGE_TEST_DATABASE_URL="<private disposable PostgreSQL URL>"
$env:MODELBRIDGE_TEST_ALLOW_DATABASE_WRITES="true"
uv run --no-sync pytest -q tests\test_core_migration.py
```

Those tests create/drop only their own random physical application databases.
Ordinary application users do not need CREATEDB. Mappings are explicit;
automatic recommendations and guided dashboard migration controls are absent.
