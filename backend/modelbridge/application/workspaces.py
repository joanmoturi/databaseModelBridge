import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from modelbridge.domain.access import AccessDenied, Principal, ResourceConflict, Role
from modelbridge.infrastructure.database import Database
from modelbridge.infrastructure.models import AccessCredential, AuditEvent, Workspace


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class WorkspaceService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def bootstrap(self, name: str) -> tuple[Workspace, str]:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 100:
            raise ValueError("Workspace name must contain 1-100 non-whitespace characters")
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        try:
            with self.database.transaction() as session:
                workspace = Workspace(name=clean_name, created_at=now)
                session.add(workspace)
                session.flush()
                credential = AccessCredential(
                    workspace_id=workspace.id,
                    token_hash=token_hash(token),
                    role=Role.OWNER,
                    created_at=now,
                )
                session.add(credential)
                session.flush()
                session.add(
                    AuditEvent(
                        workspace_id=workspace.id,
                        actor_id=credential.id,
                        action="workspace.bootstrap",
                        object_id=workspace.id,
                        created_at=now,
                    )
                )
            return workspace, token
        except IntegrityError as error:
            raise ResourceConflict("Workspace name already exists") from error

    def authenticate(self, token: str) -> Principal:
        if len(token) < 40 or len(token) > 128:
            raise AccessDenied
        with self.database.transaction() as session:
            credential = session.scalar(
                select(AccessCredential).where(
                    AccessCredential.token_hash == token_hash(token),
                    AccessCredential.revoked_at.is_(None),
                )
            )
            if credential is None:
                raise AccessDenied
            return Principal(credential.id, credential.workspace_id, Role(credential.role))

    def get_workspace(self, principal: Principal, workspace_id: str) -> Workspace:
        if workspace_id != principal.workspace_id:
            raise AccessDenied
        with self.database.transaction() as session:
            workspace = session.get(Workspace, workspace_id)
            if workspace is None:
                raise AccessDenied
            return workspace

    def rename(self, principal: Principal, workspace_id: str, name: str) -> Workspace:
        if workspace_id != principal.workspace_id or principal.role not in {
            Role.OWNER,
            Role.ADMINISTRATOR,
        }:
            raise AccessDenied
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 100:
            raise ValueError("Workspace name must contain 1-100 non-whitespace characters")
        try:
            with self.database.transaction() as session:
                workspace = session.get(Workspace, workspace_id)
                if workspace is None:
                    raise AccessDenied
                workspace.name = clean_name
                session.add(
                    AuditEvent(
                        workspace_id=workspace_id,
                        actor_id=principal.credential_id,
                        action="workspace.rename",
                        object_id=workspace_id,
                        created_at=datetime.now(UTC),
                    )
                )
            return workspace
        except IntegrityError as error:
            raise ResourceConflict("Workspace name already exists") from error

    def audit(self, principal: Principal, offset: int, limit: int) -> list[AuditEvent]:
        with self.database.transaction() as session:
            return list(
                session.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.workspace_id == principal.workspace_id)
                    .order_by(AuditEvent.created_at, AuditEvent.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
