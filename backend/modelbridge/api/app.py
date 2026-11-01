import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from modelbridge import __version__
from modelbridge.application.workspaces import WorkspaceService
from modelbridge.domain.access import AccessDenied, Principal, ResourceConflict
from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.database import Database, create_metadata_engine, require_schema
from modelbridge.infrastructure.logging import configure_logging

logger = logging.getLogger("modelbridge.api")
bearer = HTTPBearer(auto_error=False)


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    created_at: datetime


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
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        active_settings = settings if settings is not None else Settings()
        active_engine = engine if engine is not None else create_metadata_engine(active_settings)
        try:
            await asyncio.to_thread(require_schema, active_engine)
            application.state.settings = active_settings
            application.state.database = Database(active_engine)
            application.state.workspaces = WorkspaceService(application.state.database)
            logger.info("api.started")
            yield
        finally:
            if engine is None:
                await asyncio.to_thread(active_engine.dispose)
            logger.info("api.stopped")

    application = FastAPI(
        title="ModelBridge",
        version=__version__,
        description="Development foundation; migration workflow is not implemented yet.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    origins = settings.cors_origins if settings is not None else Settings().cors_origins
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = str(uuid4())
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

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
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
            content={"error": {"code": "not_found", "message": "Resource not found"}},
        )

    @application.exception_handler(ResourceConflict)
    async def conflict_error(request: Request, error: ResourceConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "conflict", "message": "Resource already exists"}},
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        logger.error("database.operation_failed")
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "unavailable", "message": "Metadata service unavailable"}},
        )

    @application.exception_handler(ValueError)
    async def value_error(request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_request", "message": "Invalid field value"}},
        )

    def principal(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Principal:
        if credentials is None:
            raise HTTPException(
                401, "Authentication required", headers={"WWW-Authenticate": "Bearer"}
            )
        service: WorkspaceService = request.app.state.workspaces
        try:
            return service.authenticate(credentials.credentials)
        except AccessDenied as error:
            raise HTTPException(
                401, "Invalid access credential", headers={"WWW-Authenticate": "Bearer"}
            ) from error

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
    def workspaces(
        request: Request, actor: Annotated[Principal, Depends(principal)]
    ) -> list[WorkspaceResponse]:
        service: WorkspaceService = request.app.state.workspaces
        return [WorkspaceResponse.model_validate(service.get_workspace(actor, actor.workspace_id))]

    @application.patch("/api/v1/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def rename(
        workspace_id: str,
        body: RenameWorkspace,
        request: Request,
        actor: Annotated[Principal, Depends(principal)],
    ) -> WorkspaceResponse:
        service: WorkspaceService = request.app.state.workspaces
        return WorkspaceResponse.model_validate(service.rename(actor, workspace_id, body.name))

    @application.get("/api/v1/audit", response_model=list[AuditResponse])
    def audit(
        request: Request,
        actor: Annotated[Principal, Depends(principal)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[AuditResponse]:
        service: WorkspaceService = request.app.state.workspaces
        return [
            AuditResponse.model_validate(event) for event in service.audit(actor, offset, limit)
        ]

    return application
