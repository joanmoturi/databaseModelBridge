import hashlib
import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid5

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Engine,
    Enum,
    Float,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from modelbridge.application.connections import ConnectionService, ConnectionUnavailable
from modelbridge.domain.access import AccessDenied, ResourceConflict
from modelbridge.infrastructure.database import Database
from modelbridge.infrastructure.models import (
    IdentifierCrosswalk,
    MigrationJob,
    MigrationPlan,
    RecordOutcome,
    SchemaSnapshot,
    Workspace,
)
from modelbridge.migration_contracts import FieldMapping, JobInput, PlanInput, TableMapping

ACCEPTED = {"inserted", "existing_equal", "dry_valid"}


class RecordError(Exception):
    pass


def canonical(value: object) -> str:
    def scalar(item: object) -> object:
        if isinstance(item, Decimal):
            rendered = format(item, "f")
            if "." in rendered:
                rendered = rendered.rstrip("0").rstrip(".")
            return "0" if item.is_zero() else rendered
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, UUID):
            return str(item)
        raise ValueError("Unsupported value")

    return json.dumps(value, default=scalar, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def transform(rule: FieldMapping, value: object, plan_id: str, table: str) -> object:
    if value is None:
        return None
    try:
        match rule.transform:
            case "identity":
                return value
            case "uuid":
                return uuid5(UUID(plan_id), table + ":" + canonical(value))
            case "string":
                return str(value)
            case "trim":
                return str(value).strip()
            case "lower":
                return str(value).lower()
            case "integer":
                if isinstance(value, bool) or not isinstance(value, (int, str)):
                    raise ValueError
                return int(value)
            case "decimal":
                result = Decimal(str(value))
                if not result.is_finite():
                    raise ValueError
                exponent = result.as_tuple().exponent
                if not isinstance(exponent, int) or result.adjusted() >= 1000 or exponent < -1000:
                    raise ValueError
                return result
            case "date_iso":
                return date.fromisoformat(str(value))
            case "date_dmy":
                return datetime.strptime(str(value), "%d/%m/%Y").date()
            case _:
                raise ValueError
    except (ValueError, TypeError, InvalidOperation):
        raise RecordError("transformation_failed") from None


def key_value(table: Table, column: str, encoded: str) -> object:
    value: object = json.loads(encoded)
    kind = table.c[column].type.python_type
    if kind is UUID:
        return UUID(str(value))
    if kind is int:
        return int(str(value))
    if kind is str:
        return str(value)
    raise ValueError("Only integer, text and UUID primary keys are supported")


def ordered_tables(spec: PlanInput) -> list[TableMapping]:
    by_name = {item.source_table: item for item in spec.tables}
    if len(by_name) != len(spec.tables):
        raise ValueError("Duplicate source tables")
    if len({item.destination_table for item in spec.tables}) != len(spec.tables):
        raise ValueError("Duplicate destination tables")
    result: list[TableMapping] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting or name not in by_name:
            raise ValueError("References must form a supported acyclic dependency graph")
        if name in visited:
            return
        visiting.add(name)
        for rule in by_name[name].fields.values():
            if rule.transform == "reference":
                if rule.reference_table is None:
                    raise ValueError("Reference mapping requires reference_table")
                visit(rule.reference_table)
        visiting.remove(name)
        visited.add(name)
        result.append(by_name[name])

    for name in sorted(by_name):
        visit(name)
    return result


class PlanService:
    def __init__(self, database: Database, connections: ConnectionService) -> None:
        self.database, self.connections = database, connections

    def get(self, workspace_id: str, plan_id: str) -> MigrationPlan:
        with self.database.transaction() as session:
            plan = session.get(MigrationPlan, plan_id)
            if plan is None or plan.workspace_id != workspace_id:
                raise AccessDenied
            return plan

    def save(self, workspace_id: str, spec: PlanInput, plan_id: str | None = None) -> MigrationPlan:
        self.connections.snapshot(workspace_id, spec.source_snapshot_id)
        self.connections.snapshot(workspace_id, spec.destination_snapshot_id)
        ordered_tables(spec)
        with self.database.transaction() as session:
            if plan_id is None:
                plan = MigrationPlan(
                    workspace_id=workspace_id,
                    definition=spec.model_dump(mode="json"),
                    created_at=datetime.now(UTC),
                )
                session.add(plan)
            else:
                found = session.get(MigrationPlan, plan_id, with_for_update=True)
                if found is None or found.workspace_id != workspace_id:
                    raise AccessDenied
                if found.approved_at is not None:
                    raise ResourceConflict
                plan = found
                plan.definition = spec.model_dump(mode="json")
            session.flush()
            return plan

    def approve(self, workspace_id: str, plan_id: str) -> MigrationPlan:
        plan = self.get(workspace_id, plan_id)
        with self.runtime(plan) as runtime:
            runtime.validate()
            bindings = runtime.bindings
        with self.database.transaction() as session:
            current = session.get(MigrationPlan, plan_id, with_for_update=True)
            if current is None or current.definition != plan.definition:
                raise ResourceConflict
            current.approved_at = datetime.now(UTC)
            current.connection_bindings = bindings
            return current

    @contextmanager
    def runtime(self, plan: MigrationPlan, *, writable: bool = False) -> Iterator["Runtime"]:
        spec = PlanInput.model_validate(plan.definition)
        source = self.connections.snapshot(plan.workspace_id, spec.source_snapshot_id)
        destination = self.connections.snapshot(plan.workspace_id, spec.destination_snapshot_id)
        source_connection = self.connections.get(plan.workspace_id, source.connection_id)
        destination_connection = self.connections.get(plan.workspace_id, destination.connection_id)
        bindings: dict[str, object] = {}
        for name, connection in (
            ("source", source_connection),
            ("destination", destination_connection),
        ):
            bindings[name] = digest(
                [
                    connection.host,
                    connection.port,
                    connection.database,
                    connection.username,
                    connection.sslmode,
                    connection.password_envelope,
                ]
            )
        if plan.approved_at is not None and plan.connection_bindings != bindings:
            raise ValueError("Approved connection configuration changed; approve a new plan")
        source_engine = self.connections.target_engine(source_connection)
        destination_engine = self.connections.target_engine(
            destination_connection, writable=writable
        )
        try:
            runtime = Runtime(
                self.database, plan, spec, source, destination, source_engine, destination_engine
            )
            runtime.bindings = bindings
            yield runtime
        finally:
            source_engine.dispose()
            destination_engine.dispose()


class Runtime:
    def __init__(
        self,
        database: Database,
        plan: MigrationPlan,
        spec: PlanInput,
        source: SchemaSnapshot,
        destination: SchemaSnapshot,
        source_engine: Engine,
        destination_engine: Engine,
    ) -> None:
        self.database, self.plan, self.spec = database, plan, spec
        self.source_engine, self.destination_engine = source_engine, destination_engine
        self.mappings = ordered_tables(spec)
        self.sources = {
            item.source_table: Table(
                item.source_table,
                MetaData(),
                schema=source.schema_name,
                autoload_with=source_engine,
            )
            for item in self.mappings
        }
        self.destinations = {
            item.source_table: Table(
                item.destination_table,
                MetaData(),
                schema=destination.schema_name,
                autoload_with=destination_engine,
            )
            for item in self.mappings
        }
        self.source_snapshot, self.destination_snapshot = source, destination
        self.namespace = str(
            spec.identifier_namespace
            or uuid5(
                UUID(plan.workspace_id), source.connection_id + ":" + destination.connection_id
            )
        )
        self.bindings: dict[str, object] = {}

    def validate(self) -> None:
        if self.plan.approved_at is not None and self.plan.connection_bindings != self.bindings:
            raise ValueError("Approved connection configuration changed; approve a new plan")
        identities: list[tuple[object, ...]] = []
        for engine in (self.source_engine, self.destination_engine):
            with engine.connect() as connection:
                identities.append(
                    tuple(
                        connection.execute(
                            text(
                                "SELECT current_database(), inet_server_addr(), inet_server_port()"
                            )
                        ).one()
                    )
                )
        if identities[0] == identities[1]:
            raise ValueError("Source and destination must be independent databases")
        source_objects = self.source_snapshot.objects["tables"]
        if not isinstance(source_objects, dict):
            raise ValueError("Invalid snapshot")
        selected = {item.source_table for item in self.mappings}
        ignored = set(self.spec.ignored_source_tables)
        if selected & ignored or selected | ignored != set(source_objects):
            raise ValueError("Every source table must be mapped or explicitly ignored")
        for mapping in self.mappings:
            source, target = (
                self.sources[mapping.source_table],
                self.destinations[mapping.source_table],
            )
            for snapshot, table, name in (
                (self.source_snapshot, source, mapping.source_table),
                (self.destination_snapshot, target, mapping.destination_table),
            ):
                tables = snapshot.objects["tables"]
                if not isinstance(tables, dict) or name not in tables:
                    raise ValueError("Table is absent from snapshot")
                columns = tables[name]["columns"]
                expected = [(col["name"], col["type"], col["nullable"]) for col in columns]
                actual = [(col.name, str(col.type), col.nullable) for col in table.c]
                if expected != actual:
                    raise ValueError("Schema drift detected; rediscover and approve a new plan")
            if [col.name for col in source.primary_key] != [mapping.source_key]:
                raise ValueError("Source requires one stable primary key")
            if [col.name for col in target.primary_key] != [mapping.destination_key]:
                raise ValueError("Destination requires one primary key")
            for table, key in ((source, mapping.source_key), (target, mapping.destination_key)):
                try:
                    key_type = table.c[key].type.python_type
                except NotImplementedError:
                    raise ValueError("Unsupported primary key type") from None
                if key_type not in {int, str, UUID}:
                    raise ValueError("Unsupported primary key type")
            key_rule = mapping.fields.get(mapping.destination_key)
            if (
                key_rule is None
                or key_rule.source != mapping.source_key
                or key_rule.transform
                not in {
                    "identity",
                    "uuid",
                    "string",
                    "integer",
                }
            ):
                raise ValueError(
                    "Destination key must map deterministically from source primary key"
                )
            used = {rule.source for rule in mapping.fields.values() if rule.source is not None}
            ignored_columns = set(mapping.ignored_source_columns)
            if used & ignored_columns or used | ignored_columns != set(source.c.keys()):
                raise ValueError("Every source column must be mapped or explicitly ignored")
            for name in used:
                kind = source.c[name].type
                try:
                    python_type = kind.python_type
                except NotImplementedError:
                    raise ValueError("Unsupported mapped source type") from None
                if isinstance(kind, Float) or python_type not in {
                    int,
                    str,
                    bool,
                    Decimal,
                    date,
                    datetime,
                    UUID,
                }:
                    raise ValueError("Unsupported mapped source type")
            if not set(mapping.fields) <= set(target.c.keys()):
                raise ValueError("Unknown destination columns")
            supported = (Boolean, Integer, Numeric, String, Date, DateTime)
            for name in mapping.fields:
                column = target.c[name]
                if column.computed is not None or (
                    column.identity is not None and column.identity.always
                ):
                    raise ValueError("Writing generated columns is unsupported")
                try:
                    destination_type = column.type.python_type
                except NotImplementedError:
                    raise ValueError("Unsupported mapped destination type") from None
                if isinstance(column.type, Float) or not (
                    isinstance(column.type, supported) or destination_type is UUID
                ):
                    raise ValueError("Unsupported mapped destination type")
            key_column = target.c[mapping.destination_key]
            if isinstance(key_column.type, Integer) and (
                key_column.identity is not None or key_column.server_default is not None
            ):
                raise ValueError(
                    "Use manually assigned or UUID keys; sequence adjustment is unsupported"
                )
            for index in target.indexes:
                if index.unique and (
                    index.dialect_options["postgresql"].get("where") is not None
                    or any(not isinstance(expression, Column) for expression in index.expressions)
                ):
                    raise ValueError("Partial/expression unique indexes are unsupported")
            if mapping.compare_fields is not None and not set(mapping.compare_fields) <= set(
                mapping.fields
            ):
                raise ValueError("Comparison fields must be mapped destination fields")
            if not set(mapping.duplicate_fields) <= set(target.c.keys()):
                raise ValueError("Unknown duplicate comparison fields")
            for col in target.c:
                if (
                    col.name not in mapping.fields
                    and not col.nullable
                    and col.server_default is None
                ):
                    raise ValueError("Unmapped required destination column")
            for foreign in target.foreign_key_constraints:
                if len(foreign.elements) != 1:
                    raise ValueError("Composite foreign keys are unsupported")
                element = foreign.elements[0]
                rule = mapping.fields.get(element.parent.name)
                if rule is None:
                    if not element.parent.nullable:
                        raise ValueError("Required foreign key must be mapped")
                    continue
                if rule.source is None and rule.value is None:
                    continue
                parent = next(
                    (item for item in self.mappings if item.source_table == rule.reference_table),
                    None,
                )
                if rule.transform != "reference" or parent is None:
                    raise ValueError("Foreign keys require explicit reference mappings")
                if element.target_fullname != (
                    f"{target.schema}.{parent.destination_table}.{parent.destination_key}"
                ):
                    raise ValueError("Reference mapping disagrees with destination foreign key")

    def batches(
        self,
        mapping: TableMapping,
        last_key: str | None,
        size: int,
        remaining: int | None,
    ) -> Iterator[list[Mapping[str, object]]]:
        table = self.sources[mapping.source_table]
        key = table.c[mapping.source_key]
        while remaining is None or remaining > 0:
            needed = {rule.source for rule in mapping.fields.values() if rule.source is not None}
            needed.add(mapping.source_key)
            statement = (
                select(*(table.c[name] for name in sorted(needed)))
                .order_by(key)
                .limit(size if remaining is None else min(size, remaining))
            )
            if last_key is not None:
                statement = statement.where(key > key_value(table, mapping.source_key, last_key))
            with self.source_engine.connect() as connection:
                rows: list[Mapping[str, object]] = [
                    {str(name): value for name, value in row.items()}
                    for row in connection.execute(statement).mappings()
                ]
            if not rows:
                return
            yield rows
            last_key = canonical(rows[-1][mapping.source_key])
            if remaining is not None:
                remaining -= len(rows)

    def values(
        self,
        mapping: TableMapping,
        row: Mapping[str, object],
        job: MigrationJob,
    ) -> dict[str, object]:
        values: dict[str, object] = {}
        for name, rule in mapping.fields.items():
            value = row[rule.source] if rule.source is not None else rule.value
            if rule.transform == "reference" and value is not None:
                with self.database.transaction() as session:
                    prior = session.get(
                        RecordOutcome, (job.id, rule.reference_table, digest(value))
                    )
                    crosswalk = session.get(
                        IdentifierCrosswalk, (self.plan.id, rule.reference_table, digest(value))
                    )
                    encoded = (
                        prior.destination_key
                        if prior is not None and prior.status in ACCEPTED
                        else crosswalk.destination_key
                        if prior is None and crosswalk is not None
                        else None
                    )
                if encoded is None:
                    raise RecordError("missing_reference")
                parent = next(
                    item for item in self.mappings if item.source_table == rule.reference_table
                )
                parent_table = self.destinations[parent.source_table]
                parent_key = key_value(parent_table, parent.destination_key, encoded)
                if not (
                    job.mode == "dry_run" and prior is not None and prior.status == "dry_valid"
                ):
                    with self.destination_engine.connect() as connection:
                        present = connection.execute(
                            select(parent_table.c[parent.destination_key]).where(
                                parent_table.c[parent.destination_key] == parent_key
                            )
                        ).first()
                    if present is None:
                        raise RecordError("missing_reference")
                values[name] = parent_key
            else:
                values[name] = transform(rule, value, self.namespace, mapping.source_table)
        self.check_types(mapping, values)
        return values

    def check_types(self, mapping: TableMapping, values: dict[str, object]) -> None:
        for name, value in values.items():
            column = self.destinations[mapping.source_table].c[name]
            kind = column.type
            if value is None:
                if not column.nullable:
                    raise RecordError("required_value_missing")
                continue
            try:
                if isinstance(kind, Boolean):
                    if not isinstance(value, bool):
                        raise ValueError
                elif isinstance(kind, Integer):
                    bits = (
                        16
                        if isinstance(kind, SmallInteger)
                        else 64
                        if isinstance(kind, BigInteger)
                        else 32
                    )
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, int)
                        or not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1)
                    ):
                        raise ValueError
                elif isinstance(kind, Numeric):
                    decimal = Decimal(str(value))
                    if not decimal.is_finite():
                        raise ValueError
                    parts = decimal.as_tuple()
                    exponent = parts.exponent
                    if not isinstance(exponent, int):
                        raise ValueError
                    digits = parts.digits
                    while exponent < 0 and digits and digits[-1] == 0:
                        digits, exponent = digits[:-1], exponent + 1
                    integer_digits = 0 if decimal.is_zero() else max(0, decimal.adjusted() + 1)
                    if integer_digits > 1000 or -exponent > 1000:
                        raise ValueError
                    if kind.scale is not None and -exponent > kind.scale:
                        raise ValueError
                    if kind.precision is not None and integer_digits > kind.precision - (
                        kind.scale or 0
                    ):
                        raise ValueError
                    values[name] = decimal
                elif isinstance(kind, String):
                    if not isinstance(value, str) or (
                        kind.length is not None and len(value) > kind.length
                    ):
                        raise ValueError
                    if isinstance(kind, Enum) and value not in kind.enums:
                        raise ValueError
                elif isinstance(kind, DateTime):
                    if not isinstance(value, datetime) or (kind.timezone and value.tzinfo is None):
                        raise ValueError
                elif isinstance(kind, Date):
                    if not isinstance(value, date) or isinstance(value, datetime):
                        raise ValueError
                elif kind.python_type is UUID:
                    values[name] = value if isinstance(value, UUID) else UUID(str(value))
                else:
                    raise ValueError
            except (ValueError, TypeError, InvalidOperation):
                raise RecordError("incompatible_value") from None

    def transfer(
        self,
        mapping: TableMapping,
        row: Mapping[str, object],
        job: MigrationJob,
    ) -> tuple[str, str | None, str | None, dict[str, object]]:
        values = self.values(mapping, row, job)
        target = self.destinations[mapping.source_table]
        destination_key = canonical(values[mapping.destination_key])
        source_hash = digest(row[mapping.source_key])
        unique_hashes: dict[str, object] = {}
        unique_columns = [
            list(constraint.columns)
            for constraint in target.constraints
            if isinstance(constraint, UniqueConstraint)
        ]
        unique_columns.extend(list(index.columns) for index in target.indexes if index.unique)
        for columns in unique_columns:
            if all(col.name in values and values[col.name] is not None for col in columns):
                tag = "|".join(col.name for col in columns)
                unique_hashes[tag] = digest([values[col.name] for col in columns])
        with self.database.transaction() as session:
            other = session.scalar(
                select(IdentifierCrosswalk).where(
                    IdentifierCrosswalk.plan_id == self.plan.id,
                    IdentifierCrosswalk.table_name == mapping.source_table,
                    IdentifierCrosswalk.destination_hash == digest(values[mapping.destination_key]),
                    IdentifierCrosswalk.source_hash != source_hash,
                )
            )
            if other is not None:
                raise RecordError("identifier_collision")
            collision = session.scalar(
                select(RecordOutcome).where(
                    RecordOutcome.job_id == job.id,
                    RecordOutcome.table_name == mapping.source_table,
                    RecordOutcome.destination_key == destination_key,
                    RecordOutcome.source_hash != source_hash,
                    RecordOutcome.status.in_(ACCEPTED),
                )
            )
            if collision is not None:
                raise RecordError("identifier_collision")
            for tag, unique_digest in unique_hashes.items():
                collision = session.scalar(
                    select(RecordOutcome).where(
                        RecordOutcome.job_id == job.id,
                        RecordOutcome.table_name == mapping.source_table,
                        RecordOutcome.source_hash != source_hash,
                        RecordOutcome.status.in_(ACCEPTED),
                        RecordOutcome.unique_hashes[tag].as_string() == unique_digest,
                    )
                )
                if collision is not None:
                    raise RecordError("unique_conflict")
        with self.destination_engine.begin() as connection:
            existing = (
                connection.execute(
                    select(target).where(
                        target.c[mapping.destination_key] == values[mapping.destination_key]
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if all(
                    canonical(existing[name]) == canonical(value) for name, value in values.items()
                ):
                    return "existing_equal", destination_key, None, unique_hashes
                if mapping.conflict == "skip":
                    return "skipped", destination_key, "conflict_skipped", unique_hashes
                raise RecordError("destination_conflict")
            if job.mode == "dry_run":
                for constraint in target.constraints:
                    if not isinstance(constraint, UniqueConstraint):
                        continue
                    columns = list(constraint.columns)
                    if not all(
                        col.name in values and values[col.name] is not None for col in columns
                    ):
                        continue
                    check = select(func.count()).select_from(target)
                    for col in columns:
                        check = check.where(col == values[col.name])
                    if connection.execute(check).scalar_one():
                        raise RecordError("unique_conflict")
                return "dry_valid", destination_key, None, unique_hashes
            connection.execute(target.insert().values(values))
        return "inserted", destination_key, None, unique_hashes


class JobService:
    def __init__(self, database: Database, plans: PlanService) -> None:
        self.database, self.plans = database, plans

    def get(self, workspace_id: str, job_id: str) -> MigrationJob:
        with self.database.transaction() as session:
            job = session.get(MigrationJob, job_id)
            if job is None or job.workspace_id != workspace_id:
                raise AccessDenied
            return job

    def create(self, workspace_id: str, body: JobInput, idempotency_key: str) -> MigrationJob:
        from modelbridge.application.webhooks import emit

        plan = self.plans.get(workspace_id, body.plan_id)
        if plan.approved_at is None:
            raise ResourceConflict
        if body.batch_size > self.plans.connections.settings.max_batch_size:
            raise ValueError("Batch size exceeds configured limit")
        request_hash = digest(body.model_dump(mode="json"))
        with self.database.transaction() as session:
            session.get(Workspace, workspace_id, with_for_update=True)
            existing = session.scalar(
                select(MigrationJob).where(
                    MigrationJob.workspace_id == workspace_id,
                    MigrationJob.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise ResourceConflict
                return existing
            active = (
                session.scalar(
                    select(func.count())
                    .select_from(MigrationJob)
                    .where(
                        MigrationJob.workspace_id == workspace_id,
                        MigrationJob.state.in_(["queued", "running"]),
                    )
                )
                or 0
            )
            if active >= self.plans.connections.settings.max_active_jobs_per_workspace:
                raise ResourceConflict
            now = datetime.now(UTC)
            job = MigrationJob(
                workspace_id=workspace_id,
                plan_id=plan.id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                mode=body.mode,
                batch_size=body.batch_size,
                sample_size=body.sample_size,
                next_attempt_at=now,
                created_at=now,
            )
            session.add(job)
            session.flush()
            emit(session, job, "migration.created")
            return job

    def cancel(self, workspace_id: str, job_id: str) -> MigrationJob:
        from modelbridge.application.webhooks import emit

        self.get(workspace_id, job_id)
        with self.database.transaction() as session:
            job = session.get(MigrationJob, job_id, with_for_update=True)
            if job is None:
                raise AccessDenied
            if job.state in {"queued", "running"}:
                job.cancel_requested = True
                if job.state == "queued":
                    job.state, job.finished_at = "cancelled", datetime.now(UTC)
                    emit(session, job, "migration.cancelled")
            return job

    def retry(self, workspace_id: str, job_id: str) -> MigrationJob:
        self.get(workspace_id, job_id)
        with self.database.transaction() as session:
            session.get(Workspace, workspace_id, with_for_update=True)
            active = (
                session.scalar(
                    select(func.count())
                    .select_from(MigrationJob)
                    .where(
                        MigrationJob.workspace_id == workspace_id,
                        MigrationJob.state.in_(["queued", "running"]),
                    )
                )
                or 0
            )
            if active >= self.plans.connections.settings.max_active_jobs_per_workspace:
                raise ResourceConflict
            job = session.get(MigrationJob, job_id, with_for_update=True)
            if job is None:
                raise AccessDenied
            if (
                job.state not in {"failed", "completed_with_errors", "cancelled"}
                or job.retry_rounds >= 3
            ):
                raise ResourceConflict
            job.state, job.cancel_requested = "queued", False
            job.next_attempt_at, job.finished_at = datetime.now(UTC), None
            job.checkpoint, job.retries, job.error_code = {}, 0, None
            job.retry_rounds += 1
            return job

    def process_one(self, stop: Callable[[], bool] = lambda: False) -> bool:
        with self.database.transaction() as session:
            candidates = list(
                session.execute(
                    select(MigrationJob.id, MigrationJob.plan_id)
                    .where(
                        MigrationJob.state.in_(["queued", "running"]),
                        MigrationJob.next_attempt_at <= datetime.now(UTC),
                    )
                    .order_by(MigrationJob.created_at)
                    .limit(20)
                )
            )
        for job_id, plan_id in candidates:
            lock_id = int.from_bytes(hashlib.sha256(("plan:" + plan_id).encode()).digest()[:8]) & (
                2**63 - 1
            )
            with self.database.engine.connect() as lock:
                if not lock.execute(
                    text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}
                ).scalar_one():
                    continue
                lock.commit()
                try:
                    self.execute(job_id, stop)
                finally:
                    lock.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
                    lock.commit()
            return True
        return False

    def execute(self, job_id: str, stop: Callable[[], bool]) -> None:
        from modelbridge.application.reconciliation import ReconciliationInterrupted, reconcile
        from modelbridge.application.webhooks import emit

        with self.database.transaction() as session:
            job = session.get(MigrationJob, job_id, with_for_update=True)
            if job is None or job.state not in {"queued", "running"}:
                return
            job.state = "running"
            emit(session, job, "migration.started")
        plan = self.plans.get(job.workspace_id, job.plan_id)
        try:
            with self.plans.runtime(plan, writable=job.mode != "dry_run") as runtime:
                runtime.validate()
                start = int(str(job.checkpoint.get("table_index", 0)))
                for index, mapping in enumerate(runtime.mappings):
                    if index < start:
                        continue
                    last = job.checkpoint.get("last_key") if index == start else None
                    if last is not None and not isinstance(last, str):
                        raise ValueError("Invalid checkpoint")
                    seen = int(str(job.checkpoint.get("table_rows", 0))) if index == start else 0
                    remaining = max(job.sample_size - seen, 0) if job.mode == "sample" else None
                    for batch in runtime.batches(mapping, last, job.batch_size, remaining):
                        for row in batch:
                            if stop():
                                return
                            with self.database.transaction() as session:
                                current = session.get(MigrationJob, job_id)
                                if current is None:
                                    return
                                if current.cancel_requested:
                                    current.state, current.finished_at = (
                                        "cancelled",
                                        datetime.now(UTC),
                                    )
                                    emit(session, current, "migration.cancelled")
                                    return
                                prior = session.get(
                                    RecordOutcome,
                                    (job_id, mapping.source_table, digest(row[mapping.source_key])),
                                )
                            if prior is not None and prior.status in ACCEPTED:
                                seen += 1
                                continue
                            exhausted = prior is not None and prior.attempts >= 3
                            attempts = min(3, 1 if prior is None else prior.attempts + 1)
                            encoded: str | None = None
                            error: str | None = None
                            status = "failed"
                            unique_hashes: dict[str, object] = {}
                            try:
                                if exhausted:
                                    raise RecordError(
                                        (prior.error_code if prior is not None else None)
                                        or "record_retry_limit"
                                    )
                                if len(canonical(dict(row)).encode()) > 1048576:
                                    raise RecordError("record_too_large")
                                status, encoded, error, unique_hashes = runtime.transfer(
                                    mapping, row, job
                                )
                            except RecordError as failure:
                                error = str(failure)
                            except IntegrityError:
                                error = "destination_constraint"
                            seen += 1
                            with self.database.transaction() as session:
                                outcome = RecordOutcome(
                                    job_id=job_id,
                                    table_name=mapping.source_table,
                                    source_hash=digest(row[mapping.source_key]),
                                    source_key=canonical(row[mapping.source_key]),
                                    destination_key=encoded,
                                    source_digest=digest(dict(row)),
                                    status=status,
                                    attempts=attempts,
                                    error_code=error,
                                    unique_hashes=unique_hashes,
                                )
                                session.merge(outcome)
                                if status in {"inserted", "existing_equal"} and encoded is not None:
                                    session.merge(
                                        IdentifierCrosswalk(
                                            plan_id=plan.id,
                                            table_name=mapping.source_table,
                                            source_hash=outcome.source_hash,
                                            source_key=outcome.source_key,
                                            destination_key=encoded,
                                            destination_hash=digest(json.loads(encoded)),
                                        )
                                    )
                                current = session.get(MigrationJob, job_id, with_for_update=True)
                                if current is None:
                                    return
                                current.checkpoint = {
                                    "table_index": index,
                                    "last_key": outcome.source_key,
                                    "table_rows": seen,
                                }
                                counters = dict(current.counters)
                                if prior is not None:
                                    counters[prior.status] = (
                                        int(str(counters.get(prior.status, 0))) - 1
                                    )
                                counters[status] = int(str(counters.get(status, 0))) + 1
                                current.counters = counters
                        with self.database.transaction() as session:
                            current = session.get(MigrationJob, job_id)
                            if current is not None:
                                current.checkpoint = {
                                    "table_index": index,
                                    "last_key": canonical(batch[-1][mapping.source_key]),
                                    "table_rows": seen,
                                }
                                emit(session, current, "migration.progress")
                    with self.database.transaction() as session:
                        current = session.get(MigrationJob, job_id)
                        if current is not None:
                            current.checkpoint = {"table_index": index + 1}
                report = reconcile(runtime, job, lambda: stop() or self.cancel_if_requested(job_id))
                with self.database.transaction() as session:
                    current = session.get(MigrationJob, job_id, with_for_update=True)
                    if current is None:
                        return
                    if current.cancel_requested:
                        current.state, current.finished_at = "cancelled", datetime.now(UTC)
                        emit(session, current, "migration.cancelled")
                        return
                    current.counters = self.counts(session, job_id)
                    current.state = (
                        "completed"
                        if report.result["consistent"]
                        and not (
                            current.counters.get("failed", 0) or current.counters.get("skipped", 0)
                        )
                        else "completed_with_errors"
                    )
                    current.finished_at, current.error_code = datetime.now(UTC), None
                    emit(session, current, "migration.completed")
        except ReconciliationInterrupted:
            return
        except (OperationalError, ConnectionUnavailable):
            self.fail(job_id, "connection_unavailable", retry=True)
        except (SQLAlchemyError, ValueError, RecordError):
            self.fail(job_id, "execution_failed", retry=False)

    @staticmethod
    def counts(session: "Session", job_id: str) -> dict[str, object]:
        return dict(
            session.execute(
                select(RecordOutcome.status, func.count())
                .where(RecordOutcome.job_id == job_id)
                .group_by(RecordOutcome.status)
            ).all()
        )

    def cancel_if_requested(self, job_id: str) -> bool:
        from modelbridge.application.webhooks import emit

        with self.database.transaction() as session:
            job = session.get(MigrationJob, job_id, with_for_update=True)
            if job is None:
                return True
            if not job.cancel_requested:
                return False
            if job.state != "cancelled":
                job.state, job.finished_at = "cancelled", datetime.now(UTC)
                emit(session, job, "migration.cancelled")
            return True

    def fail(self, job_id: str, code: str, *, retry: bool) -> None:
        from modelbridge.application.webhooks import emit

        with self.database.transaction() as session:
            job = session.get(MigrationJob, job_id, with_for_update=True)
            if job is None:
                return
            job.error_code = code
            if retry and job.retries < self.plans.connections.settings.job_retry_limit:
                job.retries += 1
                job.state, job.next_attempt_at = (
                    "queued",
                    datetime.now(UTC) + timedelta(seconds=2**job.retries),
                )
            else:
                job.state, job.finished_at = "failed", datetime.now(UTC)
                emit(session, job, "migration.failed")
