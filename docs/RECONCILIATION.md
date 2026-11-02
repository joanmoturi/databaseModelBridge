# Reconciliation and reports

Reconciliation independently queries current source and destination databases.
It does not equate migration success counters with destination correctness.
Source/destination schemas and data must be quiescent; there is no shared
cross-database snapshot.

Every job automatically reconciles before completion. To run it again, call
`POST /api/v1/workspaces/{workspace_id}/jobs/{job_id}/reconcile`.
This explicit endpoint is **synchronous**, unlike migration execution. Allow a
suitable client timeout for the selected dataset; huge reconciliations are not
certified. Read-only API keys cannot initiate this database-intensive operation.

Reports persist in metadata. Retrieve:

- `GET /jobs/{job_id}/reports`: report history.
- `GET /reports/{report_id}`: JSON.
- `GET /reports/{report_id}/csv`: CSV download.
- `GET /plans/{plan_id}/crosswalks`: persisted source/destination identifiers.
- `GET /jobs/{job_id}/records`: individual failures/skips/accepted outcomes.

All paths above are under the workspace prefix. Use pagination on list routes.
Reports/crosswalks can contain synthetic or application record identifiers;
protect them as sensitive data, not public health information.

Reports include actual source/destination counts, selected counts, outcome totals
and discrepancy counters: missing, unexpected, mismatched, source_changed,
duplicates and reference violations. Comparisons apply the approved transforms
and stable identifiers. `compare_fields` selects mapped destination fields;
`duplicate_fields` configures duplicate-value groups. Exact decimal/date/UUID
canonicalization avoids incidental formatting differences.

Destination foreign keys are checked against actual referenced tables.
Detailed discrepancy records are capped at 1,000; aggregate counters continue
beyond that cap and `details_truncated` makes truncation explicit. CSV includes
counts, summaries and bounded details. There is no field-value dump in error
messages, but identifiers themselves can be sensitive.

Dry-run reports describe validation, not missing rows from an unexecuted write.
Sample reports compare selected keys, so preexisting non-selected destination
rows can be unexpected. A full migration into a shared destination may correctly
report unrelated preexisting rows; investigate rather than deleting them.

The public-API student integration case deliberately changes a destination
field, removes a payment and inserts an extra student with a duplicate configured
name. It asserts mismatched/missing/unexpected/duplicate discrepancies using
actual destination queries and downloads JSON/CSV.
