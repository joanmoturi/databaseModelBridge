# API acceptance report

Assessment date: **November 2, 2026**. Verdict: **NOT READY** for public/production
release. Core implementation is no longer missing; clean deployment acceptance
and production-profile security verification remain incomplete.

All work is local and unpublished. No production/customer data or credentials
were used. The thirteen historical root reports remain unchanged.

## Answers to the seven acceptance questions

| Question | Answer and evidence |
|---|---|
| Can records actually migrate between two real databases? | **Yes, native E2E and independently verified.** Separate physical PostgreSQL 17.11 source/destination databases; students, teachers, courses, enrollments, payments; direct SQL verifies destination counts, UUIDs, names, dates, money and joins. |
| Does dry run avoid destination writes? | **Yes, native E2E.** Destination connections are read-only; independently queried all five destination tables remain empty. No insert-and-rollback implementation. |
| Can interrupted jobs recover? | **Yes, native E2E for the tested profile.** Terminated a worker mid-1,000-row job, checked persisted checkpoint, started two replacements, forced destination connection loss, recovered to exactly 1,000 distinct destination rows. No distributed-atomicity claim. |
| Does reconciliation inspect actual destination records? | **Yes, E2E and independently verified.** Altered a value, deleted a payment and inserted an unexpected duplicate-name row; actual discrepancy counters and JSON/CSV assertions detect these changes. |
| Do webhooks work? | **Yes, native HTTP integration.** Actual loopback receiver verifies HMAC; transient 503 retries, permanent failure, individual attempt history and migration independence are tested. Production HTTPS/DNS/egress delivery is not verified. |
| Does clean Docker Compose installation work? | **Blocked, not passed.** Fresh local clone/configuration and `compose config --quiet` succeed. Installed Docker Linux backend returns HTTP 500/stopped status; no accepted image build/start/restart or containerized migration. |
| Can an unfamiliar developer complete a public-API migration? | **Native contract is E2E tested and documented; independent clean developer acceptance is not established.** Operator CLI provisions registration proof; account, connections, schemas, mappings, jobs, reports use public TCP API/SDK. No new human/VM/public-network install was certified. |

## Executed evidence

Commands from repository root with an explicitly authorized disposable metadata
database, `MODELBRIDGE_TEST_DATABASE_URL` and
`MODELBRIDGE_TEST_ALLOW_DATABASE_WRITES=true`:

```powershell
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
Set-Location frontend
npm test
npm run lint
npm run typecheck
npm run build
```

Backend: **75 passed**, zero skipped with PostgreSQL enabled: 48 non-integration
tests and 27 integration-marked tests. One unsuppressed upstream
Starlette/TestClient/httpx deprecation warning remains.
Core public TCP acceptance contributes five cases, including empty/nonempty
cancellation parameterizations; transformation units contribute 19 cases.
Frontend: **7 unit tests passed**, lint/typecheck/production build passed.
Live `scripts/smoke.py --browser --api-product`: **2 Playwright tests passed**,
two real API restarts, actual worker readiness and independently queried
persistent credential hash. This smoke is not a migration or clean-install proxy.

The initial added authorization test incorrectly expected 403; the implemented,
documented concealment contract returns 404. Assertions now require that exact
denial contract; no permission checks were removed or weakened.

Student acceptance initially reports four explicit failed records: malformed
date, duplicate email, invalid reference and invalid monetary input. It then
corrects synthetic source data, retries failed outcomes and independently
verifies four students and payment total 350.00 with preserved relationships.
Sample execution reuses deterministic identifiers safely during full execution.
Repeated idempotency key returns the original job; changed request returns 409.
Dry-run, sample, full, cancellation and repeated execution are actual database
operations, not mocked status transitions.

Public API guards cover unapproved plans, read-only job/webhook mutation denials,
cross-workspace plans/jobs/outcomes/crosswalks/reports and changed approved
connection configuration. SDK plan creation/approval/validation, polling and
reconciliation are exercised over actual HTTP.

## Requirement classification

| Requirement | Classification | Qualification |
|---|---|---|
| Approved mapping/registered transformations | Implemented and unit tested; integration/E2E tested | Explicit specifications; no automatic recommendation engine |
| Real bounded migration pipeline | End-to-end tested; independently verified | PostgreSQL scalar/single-key/DAG profile only |
| Stable crosswalks/dependency ordering | Integration/E2E tested | UUIDv5 and persisted crosswalks; composite/cyclic references rejected |
| Dry/sample/full | End-to-end tested; independently verified | Sample does not expand parent closure; dry run does not simulate arbitrary server checks/triggers |
| Durable jobs/checkpoints/retries | Integration/E2E tested | Metadata PostgreSQL queue, plan advisory locks; no Redis |
| Worker restart/destination outage | End-to-end tested; independently verified | 1,000 rows, two replacement processes; no multi-host/partition certification |
| Cancellation/resumption/empty source | End-to-end tested | Committed external rows are retained, never silently rolled back |
| Individual failures/skips/conflicts | Integration/E2E tested | Deliberate conflicting target value is preserved and recorded as skipped, never silently overwritten |
| Reconciliation and JSON/CSV | End-to-end tested; independently verified | Actual reads; explicit reconciliation endpoint remains synchronous |
| Signed lifecycle webhooks/retry history | Integration/E2E tested | Actual development HTTP; HTTPS deployment unverified |
| Database migrations | Integration tested | Alembic head `0004_migration_core`; schema/model parity checks included |
| Dashboard | Unit and browser tested | Status/access/audit only, not a migration wizard |
| Clean Compose install/images | Blocked | Clone/configuration validate; Linux engine unavailable |
| Very large datasets/resource fairness | Not executed | 1,000 rows is recovery coverage, not a scalability benchmark |
| Public-network install/new human developer trial | Not executed | Restricted host dependency mirror; unpublished working tree |
| Other connectors | Not implemented/tested | No universal database compatibility claim |

## Clean deployment investigation

Executed `python scripts\clean_compose_acceptance.py`. Actual output:

```text
PASS: fresh clone with exact unpublished, non-ignored working files
PASS: fresh generated configuration and Compose validation
BLOCKED/FAILED: local Docker daemon; no secrets or command output printed
```

The installed client is Docker 29.9.2/API 1.56, Compose 5.6.1; Docker is outside
PATH on this host. Direct Linux named-pipe `/info` returns 500; API version 1.45
also failed earlier. Desktop status reports stopped while start reported already
running. WSL reports virtualization/platform prerequisites unavailable.
These observations do not establish which BIOS/administrator setting is wrong.
Global Desktop services were not indiscriminately killed/restarted.

Fixed a real packaging defect discovered during review: backend Dockerfile now
copies `docs` before building the wheel, whose manifest requires those resources.
Added core settings, configurable loopback port bindings and the clean-snapshot
helper. Those changes do not constitute a passed image build.

## Security review

Previous HIGH identity-claim and retained-password-forwarding findings were fixed
and independently rechecked. A bounded independent review of the new core
reported **no high-confidence exploitable findings**. It executed 19 transform
tests and synthetic/mocked authorization/network checks, but did not independently
rerun native PostgreSQL crash tests or production HTTPS deployment.

| # | Severity | File | Lines | Vulnerability | Confidence |
|---|----------|------|-------|---------------|------------|
| - | - | Reviewed core surface | - | No high-confidence finding reported | - |

This is not full production security acceptance. Runtime least-privilege roles,
real TLS/egress, trusted-proxy rate limiting, retention and large-scale resource
protection remain incomplete/unverified.

## Artifacts and boundaries

`uv build --index-url <approved package index>` built local wheel and source
distribution with declared isolated Hatchling backend. Two unchanged-input
builds produced identical SHA-256 for both artifacts; packaged core migration and
guides were checked. A fresh virtual environment installed hashed dependencies
then the wheel and imported the SDK/read a packaged guide outside the checkout.
An initial check installed the wheel before `uv pip sync`; sync correctly removed
that unlisted package. Repeating the documented dependency-first order passed.
This is package installation evidence, not clean Compose/public-network acceptance.
No registry publication is authorized.
Image reproducibility and public-network installation remain blocked/unverified.

See REMAINING_DEFECTS.md and IMPLEMENTATION_STATUS.md. Readiness is withheld:
a passing native suite cannot substitute for clean deployment acceptance.
