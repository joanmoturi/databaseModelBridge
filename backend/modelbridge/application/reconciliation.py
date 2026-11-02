from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, select

from modelbridge.application.migration_engine import (
    ACCEPTED,
    RecordError,
    Runtime,
    canonical,
    digest,
    key_value,
)
from modelbridge.infrastructure.models import (
    IdentifierCrosswalk,
    MigrationJob,
    ReconciliationReport,
    RecordOutcome,
)


class ReconciliationInterrupted(Exception):
    pass


def reconcile(
    runtime: Runtime,
    job: MigrationJob,
    should_stop: Callable[[], bool] = lambda: False,
) -> ReconciliationReport:
    from modelbridge.application.webhooks import emit

    details: list[dict[str, object]] = []
    totals = {
        "missing": 0,
        "unexpected": 0,
        "mismatched": 0,
        "duplicates": 0,
        "invalid_references": 0,
        "source_changed": 0,
        "transformation_failed": 0,
    }
    tables: dict[str, object] = {}
    detailed_count = 0

    def discrepancy(
        kind: str, table: str, key: str | None, fields: list[str] | None = None
    ) -> None:
        nonlocal detailed_count
        detailed_count += 1
        totals[kind] += 1
        if len(details) < 1000:
            details.append({"kind": kind, "table": table, "key": key, "fields": fields or []})

    for mapping in runtime.mappings:
        source, target = (
            runtime.sources[mapping.source_table],
            runtime.destinations[mapping.source_table],
        )
        with runtime.source_engine.connect() as connection:
            source_count = int(
                connection.execute(select(func.count()).select_from(source)).scalar_one()
            )
        with runtime.destination_engine.connect() as connection:
            destination_count = int(
                connection.execute(select(func.count()).select_from(target)).scalar_one()
            )
        with runtime.database.transaction() as session:
            outcomes = dict(
                session.execute(
                    select(RecordOutcome.status, func.count())
                    .where(
                        RecordOutcome.job_id == job.id,
                        RecordOutcome.table_name == mapping.source_table,
                    )
                    .group_by(RecordOutcome.status)
                ).all()
            )
        tables[mapping.source_table] = {
            "source_count": source_count,
            "destination_count": destination_count,
            "record_outcomes": outcomes,
            "selected_count": sum(outcomes.values()),
        }
        for batch in runtime.batches(mapping, None, job.batch_size, None):
            if should_stop():
                raise ReconciliationInterrupted
            for row in batch:
                source_key = canonical(row[mapping.source_key])
                with runtime.database.transaction() as session:
                    outcome = session.get(
                        RecordOutcome,
                        (job.id, mapping.source_table, digest(row[mapping.source_key])),
                    )
                if job.mode == "sample" and outcome is None:
                    continue
                if outcome is not None and outcome.source_digest != digest(dict(row)):
                    discrepancy("source_changed", mapping.source_table, source_key)
                try:
                    values = runtime.values(mapping, row, job)
                except RecordError:
                    discrepancy("transformation_failed", mapping.source_table, source_key)
                    continue
                with runtime.destination_engine.connect() as connection:
                    actual = (
                        connection.execute(
                            select(target).where(
                                target.c[mapping.destination_key] == values[mapping.destination_key]
                            )
                        )
                        .mappings()
                        .first()
                    )
                if actual is None:
                    if job.mode != "dry_run":
                        discrepancy("missing", mapping.source_table, source_key)
                else:
                    differing = [
                        name
                        for name in (mapping.compare_fields or list(mapping.fields))
                        if canonical(actual[name]) != canonical(values[name])
                    ]
                    if differing:
                        discrepancy("mismatched", mapping.source_table, source_key, differing)
        last: str | None = None
        while True:
            if should_stop():
                raise ReconciliationInterrupted
            statement = (
                select(target.c[mapping.destination_key])
                .order_by(target.c[mapping.destination_key])
                .limit(job.batch_size)
            )
            if last is not None:
                statement = statement.where(
                    target.c[mapping.destination_key]
                    > key_value(target, mapping.destination_key, last)
                )
            with runtime.destination_engine.connect() as connection:
                keys = list(connection.execute(statement).scalars())
            if not keys:
                break
            for key in keys:
                with runtime.database.transaction() as session:
                    crosswalk = session.scalar(
                        select(IdentifierCrosswalk).where(
                            IdentifierCrosswalk.plan_id == runtime.plan.id,
                            IdentifierCrosswalk.table_name == mapping.source_table,
                            IdentifierCrosswalk.destination_hash == digest(key),
                        )
                    )
                if crosswalk is None:
                    discrepancy("unexpected", mapping.source_table, canonical(key))
                else:
                    with runtime.source_engine.connect() as connection:
                        present = connection.execute(
                            select(source.c[mapping.source_key]).where(
                                source.c[mapping.source_key]
                                == key_value(source, mapping.source_key, crosswalk.source_key)
                            )
                        ).first()
                    if present is None:
                        discrepancy("unexpected", mapping.source_table, canonical(key))
            last = canonical(keys[-1])
        if mapping.duplicate_fields:
            columns = [target.c[name] for name in mapping.duplicate_fields]
            grouped = (
                select(func.count().label("n"))
                .select_from(target)
                .group_by(*columns)
                .having(func.count() > 1)
                .subquery()
            )
            with runtime.destination_engine.connect() as connection:
                duplicates = int(
                    connection.execute(
                        select(func.coalesce(func.sum(grouped.c.n - 1), 0))
                    ).scalar_one()
                )
            totals["duplicates"] += duplicates
        for rule in mapping.fields.values():
            if rule.transform != "reference":
                continue
            parent = next(
                item for item in runtime.mappings if item.source_table == rule.reference_table
            )
            parent_table = runtime.destinations[parent.source_table]
            name = next(name for name, candidate in mapping.fields.items() if candidate is rule)
            column = target.c[name]
            with runtime.destination_engine.connect() as connection:
                invalid = int(
                    connection.execute(
                        select(func.count())
                        .select_from(
                            target.outerjoin(
                                parent_table, column == parent_table.c[parent.destination_key]
                            )
                        )
                        .where(
                            column.is_not(None), parent_table.c[parent.destination_key].is_(None)
                        )
                    ).scalar_one()
                )
            totals["invalid_references"] += invalid
    with runtime.database.transaction() as session:
        all_outcomes = dict(
            session.execute(
                select(RecordOutcome.status, func.count())
                .where(RecordOutcome.job_id == job.id)
                .group_by(RecordOutcome.status)
            ).all()
        )
        result: dict[str, object] = {
            "mode": job.mode,
            "tables": tables,
            "discrepancies": totals,
            "record_outcomes": all_outcomes,
            "migrated_records": sum(
                all_outcomes.get(status, 0) for status in ACCEPTED - {"dry_valid"}
            ),
            "failed_records": all_outcomes.get("failed", 0),
            "skipped_records": all_outcomes.get("skipped", 0),
            "consistent": not any(totals.values())
            and not (all_outcomes.get("failed", 0) or all_outcomes.get("skipped", 0)),
            "details": details,
            "details_truncated": detailed_count > len(details),
            "aggregate_only": ["duplicates", "invalid_references"],
            "crosswalk_scope": runtime.plan.id,
        }
        report = ReconciliationReport(
            workspace_id=job.workspace_id,
            job_id=job.id,
            result=result,
            created_at=datetime.now(UTC),
        )
        session.add(report)
        session.flush()
        emit(session, job, "reconciliation.completed")
        return report
