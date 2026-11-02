# Migration lifecycle webhooks

Under `/api/v1/workspaces/{workspace_id}`:
`POST /webhooks` registers `{ "url": "...", "subscriptions": [...] }`;
`GET /webhooks` lists metadata; `DELETE /webhooks/{id}` disables and clears its
stored secret. Creation requires `webhooks:manage` and returns the secret once.
The metadata database stores an encrypted secret, not plaintext.

Events: migration.created, migration.started, migration.progress,
migration.completed, migration.failed, migration.cancelled,
reconciliation.completed. Completion payload includes the actual job state:
completed_with_errors is not error-free success.

Lifecycle events and delivery rows commit transactionally with metadata state.
A worker's auxiliary loop delivers independently; receiver failure cannot
reverse a migration. Delivery is retryable/at-least-once, **not exactly-once**.
Five bounded attempts use exponential delay (2, 4, 8, 16, 32 seconds).
An attempt is persisted before I/O, so a crash can leave an ambiguous attempt
marked worker_interrupted on recovery. There is no manual redelivery endpoint.

`GET /webhooks/{id}/deliveries` shows status, attempt count and sanitized last
failure. `GET /webhooks/{id}/deliveries/{delivery_id}/attempts` shows individual
start/finish times, HTTP status and error code. These require `webhooks:read`.
Response bodies are not retained; delivery reads are bounded. Disablement does
not revoke an already in-flight network request.

## Egress safety

`MODELBRIDGE_ALLOWED_WEBHOOK_NETWORKS` is a separate explicit CIDR allowlist,
empty by default. Every DNS answer must be permitted. The connection is pinned
to a validated address; HTTPS verifies hostname/certificate. Credentials,
query strings, fragments and redirects are forbidden. HTTP is allowed only for
explicit loopback in development/test. Do not allow metadata/internal service
networks broadly. Add OS/container egress filtering as defense in depth.

## Receiver verification

Header: `X-ModelBridge-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256>`.
MAC input is the timestamp ASCII bytes, a dot, then **unchanged raw request body**.
`X-ModelBridge-Event-ID` and JSON `id` identify the event.

```python
import hashlib
import hmac
import time


def verify(raw_body: bytes, signature: str, secret: str) -> bool:
    try:
        parts = dict(part.split("=", 1) for part in signature.split(","))
        timestamp = int(parts["t"])
        if abs(time.time() - timestamp) > 300:
            return False
        expected = hmac.new(
            secret.encode(), parts["t"].encode() + b"." + raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, parts["v1"])
    except (ValueError, KeyError):
        return False
```

Verify before parsing/trusting JSON. Persistently deduplicate event IDs, handle
reordering and respond quickly. Never log the secret or raw application data.
Native tests verify actual loopback HTTP signatures, a transient 503 followed
by successful delivery, attempt history and permanent receiver failure.
Production HTTPS delivery and real DNS/egress infrastructure remain unverified.
