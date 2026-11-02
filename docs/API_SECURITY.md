# API security evidence and boundaries

Implemented protections: scrypt passwords, hashed random tokens, live workspace
membership/role checks, limited scope delegation, expiration/revocation, encrypted
connection passwords, sanitized errors, streamed body limits and durable shared quotas.
Secret-bearing responses use no-store caching headers.
Account creation requires a one-hour, single-use, email-bound code delivered by a
trusted operator through a verified private channel. Upgraded unverified identities
fail closed and cannot receive memberships. Destination-changing connection edits
require a new password; TLS downgrades cannot forward retained credentials.

Outbound PostgreSQL connections fail closed without configured CIDRs. Every DNS
answer must be permitted; link-local, multicast and unspecified addresses are denied,
including IPv4-mapped IPv6 representations. A validated address is pinned using
libpq hostaddr while retaining hostname for TLS verification. Discovery/source and
dry-run destination queries run read-only; approved real jobs insert target rows.
Discovery returns schema metadata, not application rows.

Automated public-API tests exercise account sessions, key lifecycle/scope escalation,
cross-workspace access, role restrictions, membership removal, misleading body lengths,
forwarded-IP rotation, encrypted storage, real PostgreSQL testing/discovery and
archive/snapshot retention. SDK/network tests cover mixed DNS answers, mapped
metadata-service addresses, streamed chunks, TLS policy and safe retry behavior.

New migration APIs enforce scoped workspace ownership; targets are bound to
approved connections. Webhooks use separate CIDRs, pinned addresses, verified
HTTPS, no redirects and signed payloads. A bounded independent read-only review
reported no high-confidence exploitable findings in the new core surface.
Native public-API tests deny unapproved jobs, read-only execution/webhook creation
and cross-workspace plan/job/outcome/crosswalk/report access (404 by design).

This is **not full API security acceptance**. Authentication reset/MFA/session administration,
retention, trusted-proxy deployment, runtime least-privilege grants and broad
concurrent resource protection remain gaps. Dedicated review results belong in
API_ACCEPTANCE_REPORT.md; no universal compatibility claim is made.
