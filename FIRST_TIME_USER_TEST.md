# First-time user test

Date: October 31, 2026.
Evidence: a fresh clone of the supplied repository contained only `.git`.

## Observed experience

A new developer can clone the repository but has no README, executable source,
dependency manifest, configuration example, or deployment instructions. The
developer must ask the author where the actual product is before taking the first
installation step. No first migration was possible.

| Required documentation | Observed result |
|---|---|
| What ModelBridge does and does not do | Absent |
| System requirements, operating systems, deployment profiles | Absent |
| Installation and configuration | Absent |
| Supported database connection setup and permissions | Absent |
| First migration and mapping/transform approval | Absent |
| Validation and report interpretation | Absent |
| Failure recovery and safe retries | Absent |
| Upgrade and database migrations | Absent |
| Metadata backup and restore | Absent |
| Environment variables and secret-free examples | Absent |
| Self-hosting, CLI, REST/OpenAPI, dashboard | Absent |
| License and contribution process | Absent |

## Resolution status

Unresolved: the supplied repository does not contain the intended product.
These assessment documents do not replace installation or operational guidance.
Invented commands or configuration would mislead unfamiliar developers and cannot
resolve the missing implementation.

Publish the intended source locally into the assessment scope first, then write
guidance against verified behavior and repeat the test in an isolated environment.
No author assistance, undocumented internal edits, or developer-specific
configuration may be counted as a successful first-time workflow.
