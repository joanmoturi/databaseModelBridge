# Authentication

Provision accounts by temporarily setting `MODELBRIDGE_REGISTRATION_ENABLED=true`
and restarting the API. Registration defaults disabled and **always requires
email-bound registration proof**, even when enabled. A trusted deployment operator runs:

```powershell
modelbridge registration-code --email person@example.org
# Compose: docker compose exec api modelbridge registration-code --email person@example.org
```

The command displays a random code once. Deliver it privately to the verified email
recipient (mailbox or an independently verified channel), never to someone merely
claiming that address. No SMTP delivery is built in. POST `/api/v1/auth/register`
with `email`, a 12-128-character `password`, and `registration_code`, then POST
`/api/v1/auth/login` with only email/password. Codes expire in one hour, are hashed,
email-bound and single-use. Open registration without proof is unsupported.

Upgrade revision `0003_verified_registration` marks existing account identities
unverified; their account sessions/issued keys fail closed. The verified recipient
can redeem a new operator code through registration to reclaim an unverified account
and set a password; all old sessions/issued keys are revoked. Existing verified
accounts cannot be reset through registration. Legacy operator-bootstrap workspace
credentials are unaffected. Stop services during upgrade as OPERATIONS.md requires.
Use the returned `access_token` as `Authorization: Bearer <token>`.
Sessions expire after `MODELBRIDGE_SESSION_SECONDS` (default one hour);
POST `/api/v1/auth/logout` revokes the presented session.
There is no general password reset, MFA, account-disable or session-management API yet.

An account session creates a workspace using POST `/api/v1/workspaces`.
Then POST `/api/v1/workspaces/{id}/keys` with `name`, `scopes`, and a future,
timezone-aware `expires_at` no more than 365 days ahead.
The `api_key` is displayed only in this response. Store it in a secret manager.
GET keys returns metadata and last-used/revoked times, never the original key.
DELETE the key's resource to revoke it. Never log issuance responses.

Scopes: `workspace:read`, `workspace:manage`, `members:manage`, `keys:manage`,
`connections:read`, `connections:write`, `schema:read`, `schema:write`,
`migrations:read`, `migrations:write`, `webhooks:read`, `webhooks:manage`.
Owners have all scopes; administrators cannot manage membership;
`migration_engineer` members can execute migrations and discover schemas but cannot manage connections;
auditors are read-only. Keys cannot delegate permissions they do not hold.
Issuer membership and role are checked live; removal invalidates workspace access.
Owner transfer/removal is deliberately unsupported.

Passwords use salted scrypt; opaque random sessions/keys are stored as SHA-256
hashes. Encryption keys protect database passwords, not account passwords.
Legacy CLI-bootstrap tokens remain supported; prefer expiring account-issued keys.
