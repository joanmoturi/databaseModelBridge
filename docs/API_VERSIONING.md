# Versioning and errors

Implemented resource prefix: `/api/v1`. Health/version/readiness and documentation
remain outside it. Package version is developmental, not a stable-contract promise.
Additive changes are preferred; future incompatible changes require a new version.
Current pagination differences are retained for foundation compatibility.

| Status | Interpretation |
|---|---|
| 401 | Missing, invalid, expired or revoked authentication |
| 404 | Missing or unauthorized workspace resource; existence is concealed |
| 409 | Duplicate, forbidden ownership change or resource conflict |
| 413 | Actual streamed request body exceeds configured limit |
| 422 | Invalid request schema; inputs are not echoed |
| 429 | Shared quota exhausted; wait for `Retry-After` |
| 503 | Metadata/external connection unavailable |

Limits are deployment-wide settings, with distinct durable IP, credential,
workspace and discovery buckets backed by metadata PostgreSQL. Default direct-IP
limit is 30/minute, credential/workspace 120/minute, discovery 5/minute;
body maximum 65,536 bytes, 10 configured connections/workspace.
Forwarded headers do not bypass limits. A reverse proxy concentrates clients in
one IP bucket: this is a known deployment limitation, not production certification.
Migration submission defaults to two active jobs/workspace and batches of at most
100; webhook registrations/delivery retries are bounded. See BACKGROUND_JOBS.md.
Per-workspace configurable overrides and global concurrent connection/discovery
budgets remain absent. Health checks are exempt. Only job creation has a mutation
idempotency-key contract; do not blindly replay other mutations.
