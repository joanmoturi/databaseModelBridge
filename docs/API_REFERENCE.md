# API reference

Development API, not a complete migration product. Live contracts are generated
by FastAPI at `/api/openapi.json`; interactive explorer: `/api/docs`.
Read the deployment's actual specification rather than assuming planned routes exist.
The running guide portal is `/api/guides`; its allowlisted Markdown guides ship
inside the Python wheel and also work from an editable clone.

Implemented `/api/v1` operations:

| Resource | Operations |
|---|---|
| auth | POST register, login, logout |
| workspaces | POST create; GET list; PATCH `/{workspace_id}` rename |
| workspace keys | POST create; GET list; DELETE `/{key_id}` revoke |
| workspace members | PUT assign/update; GET list; DELETE `/{user_id}` remove |
| workspace connections | POST create; GET list; PATCH `/{connection_id}` update; DELETE archive |
| connection operations | POST `/{connection_id}/test`; POST `/{connection_id}/discover` |
| workspace snapshots | GET list; GET `/{snapshot_id}` |
| schema comparison | POST `/schemas/compare` |
| plans | POST/GET `/plans`; GET/PATCH `/plans/{id}`; POST `/{id}/validate`, `/{id}/approve`; GET `/{id}/crosswalks` |
| jobs | POST/GET `/jobs`; GET `/{id}`; POST `/{id}/cancel`, `/{id}/retry`, `/{id}/reconcile`; GET `/{id}/records`, `/{id}/reports` |
| reports | GET `/reports/{id}` and `/{id}/csv` |
| webhooks | POST/GET `/webhooks`; DELETE `/{id}`; GET `/{id}/deliveries`, `/{id}/deliveries/{delivery_id}/attempts` |
| audit | GET `/api/v1/audit` (legacy foundation route) |

Workspace resources live beneath `/api/v1/workspaces/{workspace_id}`.
The legacy audit route takes a `workspace_id` query parameter for account sessions.
Passwords and API keys are not returned in resource listings.
Connection updates require the full configuration except password, which can be
omitted to preserve the encrypted value. Deletion clears the stored password and
archives the connection; historical snapshots remain available.
Changing host, port, database or username requires an explicit replacement
password. TLS downgrades are rejected even with a replacement; create a separate
connection if a disposable development target intentionally uses weaker TLS.

List routes accept `offset >= 0`, `1 <= limit <= 100`, default 50.
Connection/snapshot envelopes contain `items`, `offset`, `limit`, `count`;
`count` is this page's size, **not total matches**. Other lists preserve their
array contract. Ordering is deterministic; arbitrary filtering/sorting is absent.

Errors contain `error.code`, `error.message`, `error.request_id`.
Validation errors intentionally omit submitted inputs. Correlation IDs are
server-generated and returned in `X-Request-ID`. Times use timezone-aware ISO 8601.

Job creation requires `Idempotency-Key`; an identical request returns the original
job, a changed request with the same key returns 409. Other mutations do not
promise idempotent replay. Permission/resource denials intentionally return 404
to avoid revealing existence.

See MIGRATION_EXECUTION.md, BACKGROUND_JOBS.md, RECONCILIATION.md and WEBHOOKS.md.
Not implemented: automatic mapping recommendations, standalone transformation
preview, arbitrary filtering/sorting, a guided migration dashboard and connectors
other than PostgreSQL. Reconciliation initiated explicitly is synchronous.
