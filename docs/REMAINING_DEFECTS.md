# Remaining defects and acceptance gaps

Updated November 2, 2026. Final verdict: **NOT READY**.
No known uncorrected Critical/High exploitable core-code finding was reported by
the bounded review; that is not evidence that all security/deployment risks passed.

| ID | Severity | Finding | Status / required evidence |
|---|---|---|---|
| DEPLOY-01 | Critical release gate | No successful clean Docker build/start/restart or containerized migration | Blocked: installed Linux Docker backend returns HTTP 500/stopped state. Operator must restore a functioning local Linux daemon; rerun clean helper and migration acceptance. Native tests do not close this gate. |
| ACCEPT-01 | High validation gap | New developer cannot yet be certified from public clone through first migration | Fixes unpublished; no clean public-network/human trial. Documented native API workflow passes, but not independent installation acceptance. |
| SEC-01 | High validation gap | Production TLS, real HTTPS webhook egress and runtime database grants not independently verified | Restricted local synthetic HTTP/PostgreSQL evidence only. Execute hardened deployment acceptance before exposing publicly. |
| SCALE-01 | Medium | No large-volume benchmark/global concurrent connection-discovery budgets or fairness certification | 1,000-row recovery test only; metadata/outbox retention absent. |
| REC-01 | Medium | Explicit reconciliation blocks its HTTP request | Automatic job reconciliation is durable; huge manual reports may exceed client timeouts. |
| DATA-01 | Medium | Deliberately narrow PostgreSQL schema/type profile | No CDC/composite/cyclic references/rich types, generated integer key adjustment or arbitrary trigger/check simulation in dry run. Documented rejection/boundaries, not universal support. |
| SAMPLE-01 | Medium | Sample selection does not expand relationship closure | Missing selected parents fail explicitly; choose adequate sample/full dry run. |
| TEST-01 | Medium | Cancellation-during-reconciliation lacks a dedicated acceptance case | Implemented but not separately proven; normal queued/running cancellation and conflict-skip are live tested. |
| DX-01 | Medium | No migration dashboard, automatic recommendations or standalone transformation preview | Use complete explicit public API/SDK workflow. |
| AUTH-01 | Medium | No general password reset/MFA/session admin, owner transfer or maintenance jobs | Restricted/operator-provisioned evaluation only. |
| PACK-01 | Medium | Container images not built/digest-pinned; clean public package-index install unverified | Local Python distributions build; images/public-network acceptance outstanding. |
| WARN-01 | Low | Upstream TestClient/httpx deprecation warning | Kept visible; no weakened tests or disabled security controls. |

## Fixed in this implementation

Real database transfer replaces the absent executor. PostgreSQL metadata now
persists queue, checkpoints, outcomes, crosswalks, reports, events and webhook
attempts. Read-only dry run, deterministic replay, plan-wide execution locks,
bounded retries, actual reconciliation and signed deliveries are tested natively.
Decimal canonicalization no longer rounds high-precision values through ambient
Decimal context; excessive exponents are rejected. Approved connection changes
fail closed. Docker's backend build now includes required packaged guide files.

Deployment prerequisites cannot honestly be fixed by fabricating test results,
disabling safeguards, provisioning paid infrastructure or changing global
virtualization settings without authorization. These gates remain explicit.
