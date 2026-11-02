import csv
import io
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response
from sqlalchemy import select

from modelbridge.api.product import Actor, authorize
from modelbridge.application.limits import consume_rate
from modelbridge.application.migration_engine import JobService, PlanService
from modelbridge.application.reconciliation import reconcile
from modelbridge.application.webhooks import WebhookService
from modelbridge.domain.access import AccessDenied, ResourceConflict
from modelbridge.infrastructure.models import (
    IdentifierCrosswalk,
    MigrationJob,
    MigrationPlan,
    ReconciliationReport,
    RecordOutcome,
    Webhook,
    WebhookAttempt,
    WebhookDelivery,
)
from modelbridge.migration_contracts import (
    AttemptResponse,
    CompareInput,
    DeliveryResponse,
    JobInput,
    JobResponse,
    OutcomeResponse,
    PlanInput,
    PlanResponse,
    ReportResponse,
    WebhookCreated,
    WebhookInput,
    WebhookResponse,
)

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["Migrations"])
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


def plans(request: Request) -> PlanService:
    return PlanService(request.app.state.database, request.app.state.connections)


def jobs(request: Request) -> JobService:
    return JobService(request.app.state.database, plans(request))


def hooks(request: Request) -> WebhookService:
    return WebhookService(request.app.state.database, request.app.state.settings)


def expensive(request: Request, workspace_id: str, category: str) -> None:
    consume_rate(
        request.app.state.database,
        category + ":" + workspace_id,
        request.app.state.settings.schema_discoveries_per_minute,
    )


@router.post("/schemas/compare")
def compare(
    workspace_id: str, body: CompareInput, request: Request, actor: Actor
) -> dict[str, object]:
    authorize(request, actor, workspace_id, "schema:read")
    source = request.app.state.connections.snapshot(workspace_id, body.source_snapshot_id)
    target = request.app.state.connections.snapshot(workspace_id, body.destination_snapshot_id)
    source_tables, target_tables = source.objects["tables"], target.objects["tables"]
    same = sorted(set(source_tables) & set(target_tables))
    return {
        "source_snapshot_id": source.id,
        "destination_snapshot_id": target.id,
        "source_only_tables": sorted(set(source_tables) - set(target_tables)),
        "destination_only_tables": sorted(set(target_tables) - set(source_tables)),
        "tables": [
            {
                "name": name,
                "source_columns": source_tables[name]["columns"],
                "destination_columns": target_tables[name]["columns"],
                "same_name_recommendations": [
                    {
                        "source": column["name"],
                        "destination": column["name"],
                        "requires_review": True,
                    }
                    for column in source_tables[name]["columns"]
                    if column["name"] in {item["name"] for item in target_tables[name]["columns"]}
                ],
            }
            for name in same
        ],
        "recommendation_policy": "Same-name candidates only; never automatically approved",
    }


@router.post("/plans", response_model=PlanResponse, status_code=201)
def create_plan(workspace_id: str, body: PlanInput, request: Request, actor: Actor) -> PlanResponse:
    authorize(request, actor, workspace_id, "migrations:write")
    return PlanResponse.model_validate(plans(request).save(workspace_id, body))


@router.patch("/plans/{plan_id}", response_model=PlanResponse)
def update_plan(
    workspace_id: str,
    plan_id: str,
    body: PlanInput,
    request: Request,
    actor: Actor,
) -> PlanResponse:
    authorize(request, actor, workspace_id, "migrations:write")
    return PlanResponse.model_validate(plans(request).save(workspace_id, body, plan_id))


@router.get("/plans/{plan_id}", response_model=PlanResponse)
def get_plan(workspace_id: str, plan_id: str, request: Request, actor: Actor) -> PlanResponse:
    authorize(request, actor, workspace_id, "migrations:read")
    return PlanResponse.model_validate(plans(request).get(workspace_id, plan_id))


@router.get("/plans", response_model=list[PlanResponse])
def list_plans(
    workspace_id: str,
    request: Request,
    actor: Actor,
    offset: Offset = 0,
    limit: Limit = 50,
) -> list[PlanResponse]:
    authorize(request, actor, workspace_id, "migrations:read")
    with request.app.state.database.transaction() as session:
        return [
            PlanResponse.model_validate(row)
            for row in session.scalars(
                select(MigrationPlan)
                .where(MigrationPlan.workspace_id == workspace_id)
                .order_by(MigrationPlan.created_at, MigrationPlan.id)
                .offset(offset)
                .limit(limit)
            )
        ]


@router.post("/plans/{plan_id}/approve", response_model=PlanResponse)
def approve(workspace_id: str, plan_id: str, request: Request, actor: Actor) -> PlanResponse:
    authorize(request, actor, workspace_id, "migrations:write")
    expensive(request, workspace_id, "plan-validation")
    return PlanResponse.model_validate(plans(request).approve(workspace_id, plan_id))


@router.post("/plans/{plan_id}/validate")
def validate(workspace_id: str, plan_id: str, request: Request, actor: Actor) -> dict[str, object]:
    authorize(request, actor, workspace_id, "migrations:write")
    expensive(request, workspace_id, "plan-validation")
    service = plans(request)
    with service.runtime(service.get(workspace_id, plan_id)) as runtime:
        runtime.validate()
        return {"valid": True, "dependency_order": [item.source_table for item in runtime.mappings]}


@router.post("/jobs", response_model=JobResponse, status_code=202)
def create_job(
    workspace_id: str,
    body: JobInput,
    request: Request,
    actor: Actor,
    idempotency_key: Annotated[
        str, Header(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    ],
) -> JobResponse:
    authorize(request, actor, workspace_id, "migrations:write")
    return JobResponse.model_validate(jobs(request).create(workspace_id, body, idempotency_key))


@router.get("/jobs", response_model=list[JobResponse])
def list_jobs(
    workspace_id: str,
    request: Request,
    actor: Actor,
    offset: Offset = 0,
    limit: Limit = 50,
) -> list[JobResponse]:
    authorize(request, actor, workspace_id, "migrations:read")
    with request.app.state.database.transaction() as session:
        return [
            JobResponse.model_validate(row)
            for row in session.scalars(
                select(MigrationJob)
                .where(MigrationJob.workspace_id == workspace_id)
                .order_by(MigrationJob.created_at.desc(), MigrationJob.id)
                .offset(offset)
                .limit(limit)
            )
        ]


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(workspace_id: str, job_id: str, request: Request, actor: Actor) -> JobResponse:
    authorize(request, actor, workspace_id, "migrations:read")
    return JobResponse.model_validate(jobs(request).get(workspace_id, job_id))


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel(workspace_id: str, job_id: str, request: Request, actor: Actor) -> JobResponse:
    authorize(request, actor, workspace_id, "migrations:write")
    return JobResponse.model_validate(jobs(request).cancel(workspace_id, job_id))


@router.post("/jobs/{job_id}/retry", response_model=JobResponse)
def retry(workspace_id: str, job_id: str, request: Request, actor: Actor) -> JobResponse:
    authorize(request, actor, workspace_id, "migrations:write")
    return JobResponse.model_validate(jobs(request).retry(workspace_id, job_id))


@router.get("/jobs/{job_id}/records", response_model=list[OutcomeResponse])
def records(
    workspace_id: str,
    job_id: str,
    request: Request,
    actor: Actor,
    offset: Offset = 0,
    limit: Limit = 50,
) -> list[OutcomeResponse]:
    authorize(request, actor, workspace_id, "migrations:read")
    jobs(request).get(workspace_id, job_id)
    with request.app.state.database.transaction() as session:
        return [
            OutcomeResponse.model_validate(row)
            for row in session.scalars(
                select(RecordOutcome)
                .where(RecordOutcome.job_id == job_id)
                .order_by(RecordOutcome.table_name, RecordOutcome.source_hash)
                .offset(offset)
                .limit(limit)
            )
        ]


@router.get("/plans/{plan_id}/crosswalks")
def crosswalks(
    workspace_id: str,
    plan_id: str,
    request: Request,
    actor: Actor,
    offset: Offset = 0,
    limit: Limit = 50,
) -> list[dict[str, str]]:
    authorize(request, actor, workspace_id, "migrations:read")
    plans(request).get(workspace_id, plan_id)
    with request.app.state.database.transaction() as session:
        return [
            {
                "table": row.table_name,
                "source_key": row.source_key,
                "destination_key": row.destination_key,
            }
            for row in session.scalars(
                select(IdentifierCrosswalk)
                .where(IdentifierCrosswalk.plan_id == plan_id)
                .order_by(IdentifierCrosswalk.table_name, IdentifierCrosswalk.source_hash)
                .offset(offset)
                .limit(limit)
            )
        ]


@router.post("/jobs/{job_id}/reconcile", response_model=ReportResponse, status_code=201)
def run_reconciliation(
    workspace_id: str, job_id: str, request: Request, actor: Actor
) -> ReportResponse:
    authorize(request, actor, workspace_id, "migrations:write")
    expensive(request, workspace_id, "reconciliation")
    service = jobs(request)
    job = service.get(workspace_id, job_id)
    if job.state not in {"completed", "completed_with_errors", "failed", "cancelled"}:
        raise ResourceConflict
    with service.plans.runtime(service.plans.get(workspace_id, job.plan_id)) as runtime:
        runtime.validate()
        return ReportResponse.model_validate(reconcile(runtime, job))


@router.get("/jobs/{job_id}/reports", response_model=list[ReportResponse])
def reports(
    workspace_id: str,
    job_id: str,
    request: Request,
    actor: Actor,
    offset: Offset = 0,
    limit: Limit = 50,
) -> list[ReportResponse]:
    authorize(request, actor, workspace_id, "migrations:read")
    jobs(request).get(workspace_id, job_id)
    with request.app.state.database.transaction() as session:
        return [
            ReportResponse.model_validate(row)
            for row in session.scalars(
                select(ReconciliationReport)
                .where(ReconciliationReport.job_id == job_id)
                .order_by(ReconciliationReport.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ]


@router.get("/reports/{report_id}", response_model=ReportResponse)
def report(workspace_id: str, report_id: str, request: Request, actor: Actor) -> ReportResponse:
    authorize(request, actor, workspace_id, "migrations:read")
    with request.app.state.database.transaction() as session:
        row = session.get(ReconciliationReport, report_id)
        if row is None or row.workspace_id != workspace_id:
            raise AccessDenied
        return ReportResponse.model_validate(row)


@router.get("/reports/{report_id}/csv")
def report_csv(workspace_id: str, report_id: str, request: Request, actor: Actor) -> Response:
    result = report(workspace_id, report_id, request, actor).result
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["kind", "table", "key", "fields", "value"])
    for section in ("discrepancies", "record_outcomes"):
        values = result.get(section)
        if isinstance(values, dict):
            for name, value in values.items():
                writer.writerow([section, "", "", str(name), value])
    tables = result.get("tables")
    if isinstance(tables, dict):
        for table_name, statistics in tables.items():
            if isinstance(statistics, dict):
                for name in ("source_count", "destination_count", "selected_count"):
                    writer.writerow(["count", table_name, "", name, statistics.get(name)])
    details = result["details"]
    if isinstance(details, list):
        for item in details:
            if not isinstance(item, dict):
                continue
            cells = [str(item.get(name, "")) for name in ("kind", "table", "key", "fields")]
            writer.writerow(
                [
                    "'" + cell if cell.startswith(("=", "+", "-", "@", "\t", "\r")) else cell
                    for cell in cells + [""]
                ]
            )
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="modelbridge-reconciliation.csv"',
        },
    )


@router.post("/webhooks", response_model=WebhookCreated, status_code=201, tags=["Webhooks"])
def create_webhook(
    workspace_id: str,
    body: WebhookInput,
    request: Request,
    actor: Actor,
) -> WebhookCreated:
    authorize(request, actor, workspace_id, "webhooks:manage")
    hook, secret = hooks(request).create(workspace_id, body)
    return WebhookCreated(**WebhookResponse.model_validate(hook).model_dump(), secret=secret)


@router.get("/webhooks", response_model=list[WebhookResponse], tags=["Webhooks"])
def list_webhooks(
    workspace_id: str,
    request: Request,
    actor: Actor,
    offset: Offset = 0,
    limit: Limit = 50,
) -> list[WebhookResponse]:
    authorize(request, actor, workspace_id, "webhooks:read")
    with request.app.state.database.transaction() as session:
        return [
            WebhookResponse.model_validate(row)
            for row in session.scalars(
                select(Webhook)
                .where(Webhook.workspace_id == workspace_id)
                .order_by(Webhook.created_at, Webhook.id)
                .offset(offset)
                .limit(limit)
            )
        ]


@router.delete("/webhooks/{hook_id}", status_code=204, tags=["Webhooks"])
def delete_webhook(workspace_id: str, hook_id: str, request: Request, actor: Actor) -> Response:
    authorize(request, actor, workspace_id, "webhooks:manage")
    hooks(request).delete(workspace_id, hook_id)
    return Response(status_code=204)


@router.get(
    "/webhooks/{hook_id}/deliveries", response_model=list[DeliveryResponse], tags=["Webhooks"]
)
def deliveries(
    workspace_id: str,
    hook_id: str,
    request: Request,
    actor: Actor,
    offset: Offset = 0,
    limit: Limit = 50,
) -> list[DeliveryResponse]:
    authorize(request, actor, workspace_id, "webhooks:read")
    hooks(request).get(workspace_id, hook_id)
    with request.app.state.database.transaction() as session:
        return [
            DeliveryResponse.model_validate(row)
            for row in session.scalars(
                select(WebhookDelivery)
                .where(WebhookDelivery.webhook_id == hook_id)
                .order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.id)
                .offset(offset)
                .limit(limit)
            )
        ]


@router.get(
    "/webhooks/{hook_id}/deliveries/{delivery_id}/attempts",
    response_model=list[AttemptResponse],
    tags=["Webhooks"],
)
def delivery_attempts(
    workspace_id: str,
    hook_id: str,
    delivery_id: str,
    request: Request,
    actor: Actor,
) -> list[AttemptResponse]:
    authorize(request, actor, workspace_id, "webhooks:read")
    hooks(request).get(workspace_id, hook_id)
    with request.app.state.database.transaction() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        if delivery is None or delivery.webhook_id != hook_id:
            raise AccessDenied
        return [
            AttemptResponse.model_validate(row)
            for row in session.scalars(
                select(WebhookAttempt)
                .where(WebhookAttempt.delivery_id == delivery_id)
                .order_by(WebhookAttempt.number)
            )
        ]
