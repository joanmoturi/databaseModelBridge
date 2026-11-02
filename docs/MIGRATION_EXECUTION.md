# Migration execution

The implemented evaluation profile is **PostgreSQL to PostgreSQL**, tested with
PostgreSQL 17.11 on native Windows. Other products/versions are not certified.
Use disposable synthetic databases until clean deployment acceptance passes.

## Contract

Connections and schema snapshots precede a plan. Under
`/api/v1/workspaces/{workspace_id}`:

1. `POST /schemas/compare` with source/destination snapshot IDs.
2. `POST /plans` with those IDs, explicit table/field mappings and omissions.
3. `POST /plans/{id}/validate`; `POST /plans/{id}/approve`.
4. `POST /jobs` with `plan_id`, `mode`, `batch_size` and optional `sample_size`.
   Supply a unique `Idempotency-Key` (at most 64 characters). Response: 202.
5. Poll `GET /jobs/{id}`. Inspect `/records`, `/reports` and plan `/crosswalks`.

All migration mutations require `migrations:write`; reads require
`migrations:read`. Approval makes a plan immutable and binds it to the approved
connection configuration. Changing credentials, endpoint or TLS requires a new
plan/approval. Every source table/column must be mapped or explicitly ignored.
Nothing automatically drops unknown columns or overwrites conflicting rows.
Mappings are explicit, not AI-generated recommendations.

`fields` keys are destination column names. Rules specify `source`, `transform`,
optional constant `value`, and `reference_table` for relationships. Registered
transforms: identity, string, integer, decimal, date_iso (`YYYY-MM-DD`),
date_dmy (`DD/MM/YYYY`), trim, lower, uuid and reference. Null stays null and
required destination fields reject it. No arbitrary Python or SQL is executed.
See API_QUICKSTART.md for a complete executable example.

## Supported shape and safeguards

- Independent physical databases; quiescent source data and schemas throughout
  execution/reconciliation. This is not CDC or a consistent cross-database snapshot.
- Single-column integer/text/UUID primary keys; scalar integer, text, boolean,
  Decimal, date, datetime and UUID fields. Binary floats and rich/custom types
  are not supported. Decimal inputs have bounded precision/exponents.
- Acyclic, single-column references with explicit relationship mappings.
  Composite/self/cyclic foreign keys are rejected.
- Manually assigned or UUID destination keys. Integer sequence/identity key
  adjustment, generated-always/computed writes and partial/expression unique
  indexes are unsupported.
- Topological parent-first ordering. `uuid` creates deterministic UUIDv5 values;
  persisted crosswalks preserve references. Plans for the same connection pair
  share a default workspace-specific namespace; optionally supply
  `identifier_namespace`. Do not reuse a namespace for unrelated key spaces.
- Destination schemas must already exist. ModelBridge does not issue application
  DDL, UPDATE, DELETE, TRUNCATE or automatic schema conversion.

`dry_run` uses a read-only destination connection and no destination INSERT,
including insert-and-rollback. It checks mapped types, required values, existing
keys, ordinary uniqueness and virtual parent references. It does **not** execute
destination triggers/default expressions or simulate arbitrary SQL CHECK
constraints. Server-side constraints can therefore reject an actual write after
a successful dry run. Test application-specific constraints separately.

`sample` selects the first N source keys per table, not a relationship-closed
sample. Parents outside that selection cause explicit failures; use a larger
sample or full dry run. `full` processes all selected tables.

## Conflicts, outcomes and recovery

Each record is inserted in its own destination transaction and gets a persistent
outcome: inserted, existing_equal, dry_valid, failed or skipped. An exactly equal
existing mapped row is accepted; a different row fails by default.
`conflict: "skip"` explicitly preserves the existing row and records skipped;
it never counts as an error-free completion. The source remains intact.

Batches are bounded (default maximum 100; configurable up to the API's 500
ceiling). Outcomes/checkpoints are durable. A record over 1 MiB is rejected,
although database drivers may materialize its source value before that check.

Cancellation stops between records/batches and during reconciliation; already
committed rows remain visible. `POST /jobs/{id}/retry` resumes eligible failed,
cancelled or completed-with-errors jobs. Correct rejected source records first;
accepted records are not rewritten. Source changes are independently reported.
Per-record attempts and manual retry rounds are each bounded at three.

Destination commit and metadata checkpoint are **not distributed atomic**.
Recovery replays deterministic keys and compares existing values to handle an
ambiguous commit safely; no exactly-once or multi-database rollback guarantee.
Avoid destination triggers with non-idempotent external side effects.

Execution is tested by `tests/test_core_migration.py`, including real student,
teacher, course, enrollment and payment records, malformed values, duplicates,
foreign keys, restart recovery, cancellation and independent destination SQL.
