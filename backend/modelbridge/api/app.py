import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from modelbridge import __version__
from modelbridge.api.guides import router as guides_router
from modelbridge.api.migrations import router as migrations_router
from modelbridge.api.product import Actor, auth_service, authorize, router
from modelbridge.api.protection import ResourceProtection
from modelbridge.application.authentication import AuthenticationService
from modelbridge.application.connections import ConnectionService, ConnectionUnavailable
from modelbridge.application.limits import RateExceeded
from modelbridge.application.workspaces import WorkspaceService
from modelbridge.contracts import ErrorResponse, WorkspaceResponse
from modelbridge.domain.access import AccessDenied, ResourceConflict
from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.database import Database, create_metadata_engine, require_schema
from modelbridge.infrastructure.logging import configure_logging

logger = logging.getLogger("modelbridge.api")


class RenameWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100, pattern=r".*\S.*")


class AuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    action: str
    object_id: str
    created_at: datetime


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        active_settings = config
        active_engine = engine if engine is not None else create_metadata_engine(active_settings)
        try:
            await asyncio.to_thread(require_schema, active_engine)
            application.state.settings = active_settings
            application.state.database = Database(active_engine)
            application.state.workspaces = WorkspaceService(application.state.database)
            application.state.authentication = AuthenticationService(
                application.state.database, config
            )
            application.state.connections = ConnectionService(application.state.database, config)
            logger.info("api.started")
            yield
        finally:
            if engine is None:
                await asyncio.to_thread(active_engine.dispose)
            logger.info("api.stopped")

    application = FastAPI(
        title="ModelBridge",
        version=__version__,
        description=(
            "Accounts, scoped keys, approved PostgreSQL migration plans, durable jobs, "
            "reconciliation and signed lifecycle webhooks. Experimental deployment profile."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        responses={
            status: {"model": ErrorResponse} for status in (401, 404, 409, 413, 422, 429, 503)
        },
    )
    origins = config.cors_origins
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    application.add_middleware(
        ResourceProtection,
        database=lambda: getattr(application.state, "database", None),
        max_bytes=config.max_request_bytes,
        anonymous_limit=config.anonymous_requests_per_minute,
    )
    application.include_router(guides_router)
    application.include_router(migrations_router)

    @application.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = getattr(request.state, "request_id", str(uuid4()))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Request did not match the documented schema",
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": f"http_{error.status_code}",
                    "message": str(error.detail),
                    "request_id": request.state.request_id,
                }
            },
            headers=error.headers,
        )

    @application.exception_handler(AccessDenied)
    async def access_error(request: Request, error: AccessDenied) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "not_found",
                    "message": "Resource not found",
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.exception_handler(ResourceConflict)
    async def conflict_error(request: Request, error: ResourceConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "conflict",
                    "message": "Resource already exists",
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        logger.error("database.operation_failed")
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "unavailable",
                    "message": "Metadata service unavailable",
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.exception_handler(ValueError)
    async def value_error(request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Invalid field value",
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.exception_handler(RateExceeded)
    async def rate_error(request: Request, error: RateExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": "60"},
            content={
                "error": {
                    "code": "rate_limited",
                    "message": "Request quota exceeded",
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.exception_handler(ConnectionUnavailable)
    async def connection_error(request: Request, error: ConnectionUnavailable) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "connection_unavailable",
                    "message": "Check connection credentials, TLS and outbound policy",
                    "request_id": request.state.request_id,
                }
            },
        )

    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "alive", "version": __version__}

    @application.get("/version")
    def version() -> dict[str, str]:
        return {"version": __version__, "release_status": "development"}

    @application.get("/ready")
    def readiness(request: Request) -> JSONResponse:
        config: Settings = request.app.state.settings
        database: Database = request.app.state.database
        try:
            checks = database.readiness(config.worker_id, config.worker_stale_seconds)
        except SQLAlchemyError:
            checks = {"metadata_database": False, "worker": False}
        ready = all(checks.values())
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", "checks": checks},
        )

    @application.get("/api/v1/workspaces", response_model=list[WorkspaceResponse])
    def workspaces(request: Request, actor: Actor) -> list[WorkspaceResponse]:
        return [
            WorkspaceResponse.model_validate(workspace)
            for workspace in auth_service(request).list_workspaces(actor)
        ]

    @application.patch("/api/v1/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def rename(
        workspace_id: str,
        body: RenameWorkspace,
        request: Request,
        actor: Actor,
    ) -> WorkspaceResponse:
        authorize(request, actor, workspace_id, "workspace:manage")
        service: WorkspaceService = request.app.state.workspaces
        principal = auth_service(request).authorize(actor, workspace_id, "workspace:manage")
        return WorkspaceResponse.model_validate(service.rename(principal, workspace_id, body.name))

    @application.get("/api/v1/audit", response_model=list[AuditResponse])
    def audit(
        request: Request,
        actor: Actor,
        workspace_id: str | None = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[AuditResponse]:
        service: WorkspaceService = request.app.state.workspaces
        selected = workspace_id or actor.workspace_id
        if selected is None:
            raise HTTPException(400, "Supply workspace_id when using an account session")
        authorize(request, actor, selected, "workspace:read")
        principal = auth_service(request).authorize(actor, selected, "workspace:read")
        return [
            AuditResponse.model_validate(event) for event in service.audit(principal, offset, limit)
        ]

    return application
