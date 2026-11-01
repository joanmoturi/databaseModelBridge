# Known limitations

Development in progress. No data migration functionality exists yet. The worker
is heartbeat-only. Dashboard is foundation status/workspace UI, not a completed
migration workflow. No connector is certified; live Supabase is not tested.

No native database fallback: PostgreSQL is required. Docker/VM acceptance depends
on a usable runtime. Native Windows development testing is not isolated release
acceptance. No production, distributed-worker or universal compatibility claim.

Native foundation validated with Python 3.12.16, PostgreSQL 17.11, Node 26.13.0 and
Next.js 16.4.3. Container image references are version tags, not digest-pinned
release artifacts. Docker images, Linux and clean-room Compose have not been tested.
Hosted CI and public-network installation remain unverified. Requirement hashes
were checked against public PyPI; current host downloads require an approved mirror.
Push preparation removed environment-specific npm artifact URLs while preserving
the original exact versions and integrity records. That npm metadata contains
legacy SHA-1 entries; public-registry regeneration with stronger artifact hashes
and clean npm installation remain release-packaging checks. Public npm TLS access
failed on this host; certificate verification was not disabled.

Scoped opaque access credentials are the current authentication interface.
Account/session management, credential issuance/revocation/expiration UI,
per-user workspace memberships, rate limiting and connector network restrictions
are not implemented. Do not publicly expose the local development profile.
Crypto key rotation is a tested abstraction, not a completed stored-connection
rotation workflow. Worker heartbeat is not durable job execution.

The upstream FastAPI/Starlette TestClient currently warns that httpx support is
deprecated; tests pass without suppressing the warning. Metadata restore testing
does not establish application-data migration recovery or upgrade compatibility
between product releases.
