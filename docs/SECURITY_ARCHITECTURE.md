# Security architecture

Foundation: required configuration; strong generated workspace credentials stored
as SHA-256 hashes; role checks; fail-closed startup; bounded names; required Fernet
key ring; authenticated audit access; redacted validation/error logging; no logging
of credential inputs; constant-time secret comparisons where needed.

Bootstrap is a local administrative CLI operation. Its newly generated token is
displayed exactly once for the operator to put in a secret manager; normal API
responses and logs never return it. TLS termination is required for remote access.
Default published ports bind to loopback.

Credential key rotation is an explicit operator action. Fernet key rotation keeps
old keys for decrypting old envelopes; removal requires re-encrypting stored
credentials first. No external secret manager is claimed.

Connection SSRF protections, session/account management, per-user membership,
rate limiting, encrypted connector storage and migration safety still need
implementation and adversarial tests. This document does not certify security.
