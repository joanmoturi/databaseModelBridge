from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from modelbridge.application.authentication import AuthenticationService, Identity
from modelbridge.application.connections import ConnectionService
from modelbridge.application.limits import consume_rate
from modelbridge.contracts import (
    AccountInput,
    AccountResponse,
    ConnectionInput,
    ConnectionPage,
    ConnectionResponse,
    ConnectionTestResponse,
    DiscoverInput,
    KeyCreated,
    KeyInput,
    KeyResponse,
    MembershipInput,
    MembershipResponse,
    RegistrationInput,
    SessionResponse,
    SnapshotPage,
    SnapshotResponse,
    WorkspaceInput,
    WorkspaceResponse,
)
from modelbridge.domain.access import AccessDenied

router = APIRouter(prefix="/api/v1")
bearer = HTTPBearer(auto_error=False)


def auth_service(request: Request) -> AuthenticationService:
    service: AuthenticationService = request.app.state.authentication
    return service


def connection_service(request: Request) -> ConnectionService:
    service: ConnectionService = request.app.state.connections
    return service


def identity(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Identity:
    if credentials is None:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    try:
        actor = auth_service(request).identify(credentials.credentials)
    except AccessDenied:
        raise HTTPException(
            401, "Invalid or expired access credential", headers={"WWW-Authenticate": "Bearer"}
        ) from None
    consume_rate(
        request.app.state.database,
        "credential:" + (actor.user_id or actor.credential_id),
        request.app.state.settings.requests_per_minute,
    )
    return actor


def authorize(request: Request, actor: Identity, workspace_id: str, scope: str) -> None:
    auth_service(request).authorize(actor, workspace_id, scope)
    consume_rate(
        request.app.state.database,
        "workspace:" + workspace_id,
        request.app.state.settings.requests_per_minute,
    )


Actor = Annotated[Identity, Depends(identity)]


@router.post(
    "/auth/register", response_model=AccountResponse, status_code=201, tags=["Authentication"]
)
def register(body: RegistrationInput, request: Request) -> AccountResponse:
    return AccountResponse.model_validate(
        auth_service(request).register(
            body.email, body.password.get_secret_value(), body.registration_code.get_secret_value()
        )
    )


@router.post("/auth/login", response_model=SessionResponse, tags=["Authentication"])
def login(body: AccountInput, request: Request) -> SessionResponse:
    try:
        token, expires = auth_service(request).login(body.email, body.password.get_secret_value())
    except AccessDenied:
        raise HTTPException(401, "Invalid email or password") from None
    return SessionResponse(access_token=token, expires_at=expires)


@router.post("/auth/logout", status_code=204, tags=["Authentication"])
def logout(request: Request, actor: Actor) -> Response:
    auth_service(request).logout(actor)
    return Response(status_code=204)


@router.post("/workspaces", response_model=WorkspaceResponse, status_code=201, tags=["Workspaces"])
def create_workspace(body: WorkspaceInput, request: Request, actor: Actor) -> WorkspaceResponse:
    workspace = auth_service(request).create_workspace(actor, body.name)
    return WorkspaceResponse.model_validate(workspace)


@router.post(
    "/workspaces/{workspace_id}/keys", response_model=KeyCreated, status_code=201, tags=["API keys"]
)
def create_key(workspace_id: str, body: KeyInput, request: Request, actor: Actor) -> KeyCreated:
    authorize(request, actor, workspace_id, "keys:manage")
    key, token = auth_service(request).create_key(
        actor, workspace_id, body.name, body.scopes, body.expires_at
    )
    return KeyCreated(**KeyResponse.model_validate(key).model_dump(), api_key=token)


@router.get("/workspaces/{workspace_id}/keys", response_model=list[KeyResponse], tags=["API keys"])
def keys(
    workspace_id: str,
    request: Request,
    actor: Actor,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[KeyResponse]:
    authorize(request, actor, workspace_id, "keys:manage")
    return [
        KeyResponse.model_validate(key)
        for key in auth_service(request).keys(actor, workspace_id, offset, limit)
    ]


@router.delete("/workspaces/{workspace_id}/keys/{key_id}", status_code=204, tags=["API keys"])
def revoke_key(workspace_id: str, key_id: str, request: Request, actor: Actor) -> Response:
    authorize(request, actor, workspace_id, "keys:manage")
    auth_service(request).revoke_key(actor, workspace_id, key_id)
    return Response(status_code=204)


@router.put(
    "/workspaces/{workspace_id}/members", response_model=MembershipResponse, tags=["Membership"]
)
def member(
    workspace_id: str, body: MembershipInput, request: Request, actor: Actor
) -> MembershipResponse:
    authorize(request, actor, workspace_id, "members:manage")
    return MembershipResponse.model_validate(
        auth_service(request).set_member(actor, workspace_id, body.email, body.role)
    )


@router.get(
    "/workspaces/{workspace_id}/members",
    response_model=list[MembershipResponse],
    tags=["Membership"],
)
def members(
    workspace_id: str,
    request: Request,
    actor: Actor,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[MembershipResponse]:
    authorize(request, actor, workspace_id, "members:manage")
    return [
        MembershipResponse.model_validate(row)
        for row in auth_service(request).members(actor, workspace_id, offset, limit)
    ]


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204, tags=["Membership"])
def remove_member(workspace_id: str, user_id: str, request: Request, actor: Actor) -> Response:
    authorize(request, actor, workspace_id, "members:manage")
    auth_service(request).remove_member(actor, workspace_id, user_id)
    return Response(status_code=204)


@router.get(
    "/workspaces/{workspace_id}/connections", response_model=ConnectionPage, tags=["Connections"]
)
def connections(
    workspace_id: str,
    request: Request,
    actor: Actor,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ConnectionPage:
    authorize(request, actor, workspace_id, "connections:read")
    rows = connection_service(request).list_connections(workspace_id, offset, limit)
    return ConnectionPage(
        items=[ConnectionResponse.model_validate(row) for row in rows],
        offset=offset,
        limit=limit,
        count=len(rows),
    )


@router.post(
    "/workspaces/{workspace_id}/connections",
    response_model=ConnectionResponse,
    status_code=201,
    tags=["Connections"],
)
def create_connection(
    workspace_id: str, body: ConnectionInput, request: Request, actor: Actor
) -> ConnectionResponse:
    authorize(request, actor, workspace_id, "connections:write")
    connection = connection_service(request).save(
        workspace_id,
        body.name,
        body.host,
        body.port,
        body.database,
        body.username,
        body.password.get_secret_value() if body.password is not None else None,
        body.sslmode,
        actor_id=actor.credential_id,
    )
    return ConnectionResponse.model_validate(connection)


@router.patch(
    "/workspaces/{workspace_id}/connections/{connection_id}",
    response_model=ConnectionResponse,
    tags=["Connections"],
)
def update_connection(
    workspace_id: str,
    connection_id: str,
    body: ConnectionInput,
    request: Request,
    actor: Actor,
) -> ConnectionResponse:
    authorize(request, actor, workspace_id, "connections:write")
    connection = connection_service(request).save(
        workspace_id,
        body.name,
        body.host,
        body.port,
        body.database,
        body.username,
        body.password.get_secret_value() if body.password is not None else None,
        body.sslmode,
        connection_id,
        actor_id=actor.credential_id,
    )
    return ConnectionResponse.model_validate(connection)


@router.delete(
    "/workspaces/{workspace_id}/connections/{connection_id}", status_code=204, tags=["Connections"]
)
def delete_connection(
    workspace_id: str, connection_id: str, request: Request, actor: Actor
) -> Response:
    authorize(request, actor, workspace_id, "connections:write")
    connection_service(request).delete(workspace_id, connection_id, actor.credential_id)
    return Response(status_code=204)


@router.post(
    "/workspaces/{workspace_id}/connections/{connection_id}/test",
    response_model=ConnectionTestResponse,
    tags=["Connections"],
)
def test_connection(
    workspace_id: str, connection_id: str, request: Request, actor: Actor
) -> ConnectionTestResponse:
    authorize(request, actor, workspace_id, "connections:read")
    version = connection_service(request).test(workspace_id, connection_id)
    return ConnectionTestResponse(connected=True, server_version_num=version)


@router.post(
    "/workspaces/{workspace_id}/connections/{connection_id}/discover",
    response_model=SnapshotResponse,
    status_code=201,
    tags=["Schemas"],
)
def discover(
    workspace_id: str, connection_id: str, body: DiscoverInput, request: Request, actor: Actor
) -> SnapshotResponse:
    authorize(request, actor, workspace_id, "schema:write")
    consume_rate(
        request.app.state.database,
        "discovery:" + workspace_id,
        request.app.state.settings.schema_discoveries_per_minute,
    )
    return SnapshotResponse.model_validate(
        connection_service(request).discover(
            workspace_id, connection_id, body.schema_name, actor.credential_id
        )
    )


@router.get("/workspaces/{workspace_id}/snapshots", response_model=SnapshotPage, tags=["Schemas"])
def snapshots(
    workspace_id: str,
    request: Request,
    actor: Actor,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> SnapshotPage:
    authorize(request, actor, workspace_id, "schema:read")
    rows = connection_service(request).snapshots(workspace_id, offset, limit)
    return SnapshotPage(
        items=[SnapshotResponse.model_validate(row) for row in rows],
        offset=offset,
        limit=limit,
        count=len(rows),
    )


@router.get(
    "/workspaces/{workspace_id}/snapshots/{snapshot_id}",
    response_model=SnapshotResponse,
    tags=["Schemas"],
)
def snapshot(
    workspace_id: str, snapshot_id: str, request: Request, actor: Actor
) -> SnapshotResponse:
    authorize(request, actor, workspace_id, "schema:read")
    return SnapshotResponse.model_validate(
        connection_service(request).snapshot(workspace_id, snapshot_id)
    )
