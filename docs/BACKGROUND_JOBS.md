# Durable background jobs

The PostgreSQL metadata database is the queue, checkpoint store and lifecycle
outbox. **Redis is not required.** Start `python -m modelbridge.worker` separately
from the API, or use the Compose worker. HTTP job submission returns 202 and an ID;
the API does not wait for the migration.

States implemented: queued, running, completed, completed_with_errors, failed,
cancelled. There is no separate paused or validating state. `running` includes
validation and automatic reconciliation. Completion is not set until actual
reconciliation finishes and its report is persisted.

Metadata includes request hash/idempotency key, per-record outcomes, counters,
checkpoint, retry delays, cancellation flag, timestamps and sanitized failure
codes. Reusing an idempotency key with the same body returns the original job;
a different body returns 409. This is specific to job creation, not all POSTs.

PostgreSQL session advisory locks serialize execution by approved plan across
worker processes. Process termination releases the lock; a new worker discovers
the durable running job. Per-record checkpoints plus deterministic destination
keys support replay. Source schemas/data must remain quiescent.

Connection failures get persisted exponential delays (2, 4, 8 seconds with the
default retry budget). After exhaustion the job is failed, never silently lost.
Operator `POST /jobs/{id}/retry` starts a bounded recovery round; it is not an
unlimited record retry. Invalid records remain individually visible.

`POST /jobs/{id}/cancel` sets a cooperative flag; queued jobs cancel immediately.
There is no forced rollback of committed destination records. See
MIGRATION_EXECUTION.md for retry and transaction boundaries.

## Operation

- `MODELBRIDGE_MAX_ACTIVE_JOBS_PER_WORKSPACE=2`: queued/running submission quota.
- `MODELBRIDGE_MAX_BATCH_SIZE=100`: bounded records per source fetch.
- `MODELBRIDGE_JOB_RETRY_LIMIT=3`: automatic connection retry budget.
- `MODELBRIDGE_WORKER_ID`: readiness heartbeat identity. Assign and monitor
  worker identities deliberately when extending the single-worker Compose profile.
- `/ready` checks current metadata schema and a fresh idle/running heartbeat.

The auxiliary worker thread maintains heartbeat and dispatches webhook deliveries
independently of migration success. Monitor failures, queue age, failed outcomes,
webhook attempts, database connections and metadata storage. There is no
implemented retention/compaction service. Do not delete checkpoints/crosswalks
while jobs can be resumed. Back up metadata and encryption keys together.

Native tests terminate a worker during a 1,000-row migration, start two replacement
workers, deny destination connections temporarily, and independently verify
1,000 distinct destination rows. That is meaningful recovery evidence, not a
large-scale performance or multi-host/network-partition certification.
