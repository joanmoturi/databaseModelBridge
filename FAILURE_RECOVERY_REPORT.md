# Failure and recovery report

Date: October 31, 2026.
No application, worker, migration execution, or checkpoint store is available.

| Manual fault-injection scenario | Status | Why |
|---|---|---|
| Source/destination database disconnection | Blocked | No connected databases or migration |
| Worker restart during execution | Blocked | No worker |
| Retry after interruption | Blocked | No execution or retry mechanism |
| Duplicate execution | Blocked | No public execution interface |
| Partial write failure | Blocked | No destination writes |
| Invalid-record handling | Blocked | No validation pipeline |
| Cancellation | Blocked | No cancellable job |
| Concurrent migrations | Blocked | No service or concurrency implementation |
| Checkpoint/resume correctness | Blocked | No metadata schema or checkpoints |

Counts: 9 manual scenarios blocked, 0 executed, 0 passed, 0 failed.
Automated recovery tests: not executed; no tests were supplied.

## Required evidence after prerequisites exist

Create a disposable baseline and expected row-level reconciliation. Inject faults
only into owned test services. Record timestamps, job states, sanitized logs,
checkpoint contents, and independent destination queries before/after recovery.
Check retries and duplicate submissions do not duplicate or silently overwrite
rows. Check cancellation and concurrent migrations preserve consistency.
Account explicitly for invalid and failed records.

No fault injection, restart, cancellation, retry, or state-restoration command was
executed. Recovery behavior remains unknown, not successful.
