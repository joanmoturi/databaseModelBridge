from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from modelbridge.contracts import Input

Name = str
Transform = Literal[
    "identity",
    "string",
    "integer",
    "decimal",
    "date_iso",
    "date_dmy",
    "trim",
    "lower",
    "uuid",
    "reference",
]


class FieldMapping(Input):
    source: str | None = Field(default=None, max_length=63, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    transform: Transform = "identity"
    value: str | int | float | bool | None = None
    reference_table: str | None = Field(
        default=None, max_length=63, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )


class TableMapping(Input):
    source_table: str = Field(max_length=63, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    destination_table: str = Field(max_length=63, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    source_key: str = Field(max_length=63, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    destination_key: str = Field(max_length=63, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    fields: dict[str, FieldMapping] = Field(min_length=1, max_length=100)
    ignored_source_columns: list[str] = Field(default_factory=list, max_length=100)
    compare_fields: list[str] | None = Field(default=None, max_length=100)
    duplicate_fields: list[str] = Field(default_factory=list, max_length=10)
    conflict: Literal["fail", "skip"] = "fail"


class PlanInput(Input):
    source_snapshot_id: str
    destination_snapshot_id: str
    tables: list[TableMapping] = Field(min_length=1, max_length=50)
    ignored_source_tables: list[str] = Field(default_factory=list, max_length=200)
    identifier_namespace: UUID | None = None


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    definition: PlanInput
    approved_at: datetime | None
    created_at: datetime


class CompareInput(Input):
    source_snapshot_id: str
    destination_snapshot_id: str


class JobInput(Input):
    plan_id: str
    mode: Literal["dry_run", "sample", "full"] = "dry_run"
    batch_size: int = Field(default=100, ge=1, le=500)
    sample_size: int = Field(default=10, ge=1, le=1000)


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    plan_id: str
    mode: str
    state: str
    checkpoint: dict[str, object]
    counters: dict[str, object]
    cancel_requested: bool
    retries: int
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None


class OutcomeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    table_name: str
    source_key: str
    destination_key: str | None
    status: str
    attempts: int
    error_code: str | None


class ReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    job_id: str
    result: dict[str, object]
    created_at: datetime


EventType = Literal[
    "migration.created",
    "migration.started",
    "migration.progress",
    "migration.completed",
    "migration.failed",
    "migration.cancelled",
    "reconciliation.completed",
]


class WebhookInput(Input):
    url: str = Field(min_length=8, max_length=2048)
    subscriptions: list[EventType] = Field(min_length=1, max_length=7)


class WebhookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    url: str
    subscriptions: list[str]
    enabled: bool
    created_at: datetime


class WebhookCreated(WebhookResponse):
    secret: str


class DeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    event_id: str
    state: str
    attempts: int
    status_code: int | None
    error_code: str | None


class AttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    number: int
    started_at: datetime
    finished_at: datetime | None
    status_code: int | None
    error_code: str | None
