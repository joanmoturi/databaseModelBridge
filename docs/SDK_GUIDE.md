# Python SDK

Install the repository's Python package using README's pinned installation process;
the client is `modelbridge.sdk.ModelBridge`, not a separately published distribution.
See API_QUICKSTART.md for environment configuration and executable usage.

Implemented methods: register, login, logout, workspaces, create_workspace,
create_key, keys, revoke_key, connections, save_connection, delete_connection,
test_connection, discover, snapshots, snapshot. Set `workspace_id` before scoped calls.
Request/response models are in `modelbridge.contracts`.
Membership management currently uses REST rather than SDK helpers.

Use the context manager to close HTTP resources. Defaults: 10-second timeout,
two retries of GET requests on transport errors or 429/502/503/504 responses.
No mutation is automatically replayed. `Retry-After` numeric delays are capped
at 60 seconds; other retry delays are bounded. Redirects are not followed.
Remote API URLs require HTTPS; HTTP is allowed only for explicit loopback hosts.
Embedded URL credentials are rejected.

`ApiError` exposes status, sanitized code and correlation ID;
`TransportError` and `ContractError` do not echo payloads or network exceptions.
Issued sessions/keys wrap secrets in `SecretStr`; use `get_secret_value()` only
when passing directly to a trusted secret sink. Never log raw HTTP responses.

Migration methods: compare, create_plan, approve_plan, validate_plan, create_job,
job, wait, cancel, retry, reconcile, reports and outcomes. Typed models are in
`modelbridge.migration_contracts`. `create_job` requires an idempotency key.
`wait` has a deadline and minimum polling interval; timing out does not cancel
the durable job. Explicit reconciliation is synchronous.

Webhook administration, plan update/list, CSV downloads and crosswalk retrieval
currently use REST, not SDK helpers. No standalone transformation-preview method.
Native TCP tests exercise typed SDK plan validation/approval, actual transfer,
polling and reconciliation against separate disposable databases.
