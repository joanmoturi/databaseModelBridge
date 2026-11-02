import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from modelbridge.application.workspaces import token_hash
from modelbridge.domain.access import AccessDenied, Principal, ResourceConflict, Role
from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.database import Database
from modelbridge.infrastructure.models import (
    AccessCredential,
    Account,
    AuditEvent,
    Membership,
    RegistrationPermit,
    UserSession,
    Workspace,
)

ALL_SCOPES = frozenset(
    {
        "workspace:read",
        "workspace:manage",
        "members:manage",
        "keys:manage",
        "connections:read",
        "connections:write",
        "schema:read",
        "schema:write",
        "migrations:read",
        "migrations:write",
        "webhooks:read",
        "webhooks:manage",
    }
)
ROLE_SCOPES = {
    Role.OWNER: ALL_SCOPES,
    Role.ADMINISTRATOR: ALL_SCOPES - {"members:manage"},
    Role.MIGRATION_ENGINEER: frozenset(
        {
            "workspace:read",
            "connections:read",
            "schema:read",
            "schema:write",
            "migrations:read",
            "migrations:write",
        }
    ),
    Role.AUDITOR: frozenset(
        {"workspace:read", "connections:read", "schema:read", "migrations:read", "webhooks:read"}
    ),
}


def password_digest(password: str, salt: bytes | None = None) -> str:
    selected_salt = secrets.token_bytes(16) if salt is None else salt
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=selected_salt, n=16384, r=8, p=1, maxmem=33554432
    )
    return base64.b64encode(selected_salt).decode() + ":" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    salt, _ = stored.split(":", 1)
    return hmac.compare_digest(password_digest(password, base64.b64decode(salt)), stored)


@dataclass(frozen=True)
class Identity:
    credential_id: str
    user_id: str | None
    workspace_id: str | None
    role: Role | None
    scopes: frozenset[str]
    is_session: bool


class AuthenticationService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self._dummy_password = password_digest("timing-only-dummy", salt=bytes(16))

    def issue_registration_code(self, email: str) -> str:
        normalized = email.lower().strip()
        if len(normalized) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("Invalid email")
        token = "mbr_" + secrets.token_urlsafe(32)
        with self.database.transaction() as session:
            session.add(
                RegistrationPermit(
                    token_hash=token_hash(token),
                    email=normalized,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        return token

    def register(self, email: str, password: str, registration_code: str) -> Account:
        if not self.settings.registration_enabled:
            raise AccessDenied
        try:
            with self.database.transaction() as session:
                now = datetime.now(UTC)
                normalized = email.lower().strip()
                permit = session.scalar(
                    select(RegistrationPermit)
                    .where(
                        RegistrationPermit.token_hash == token_hash(registration_code),
                        RegistrationPermit.email == normalized,
                        RegistrationPermit.expires_at > now,
                        RegistrationPermit.consumed_at.is_(None),
                    )
                    .with_for_update()
                )
                if permit is None:
                    raise AccessDenied
                account = session.scalar(select(Account).where(Account.email == normalized))
                if account is not None and account.email_verified:
                    raise ResourceConflict
                if account is None:
                    account = Account(email=normalized, created_at=now)
                    session.add(account)
                else:
                    for model in (UserSession, AccessCredential):
                        session.execute(
                            update(model).where(model.user_id == account.id).values(revoked_at=now)
                        )
                account.password_hash = password_digest(password)
                account.email_verified = True
                permit.consumed_at = now
                session.flush()
            return account
        except IntegrityError as error:
            raise ResourceConflict from error

    def login(self, email: str, password: str) -> tuple[str, datetime]:
        with self.database.transaction() as session:
            account = session.scalar(select(Account).where(Account.email == email.lower().strip()))
            stored = account.password_hash if account is not None else self._dummy_password
            valid = verify_password(password, stored)
            if account is None or not valid or not account.email_verified:
                raise AccessDenied
            token = "mbs_" + secrets.token_urlsafe(32)
            expires = datetime.now(UTC) + timedelta(seconds=self.settings.session_seconds)
            session.add(
                UserSession(user_id=account.id, token_hash=token_hash(token), expires_at=expires)
            )
        return token, expires

    def identify(self, token: str) -> Identity:
        if not 40 <= len(token) <= 128:
            raise AccessDenied
        now = datetime.now(UTC)
        with self.database.transaction() as session:
            if token.startswith("mbs_"):
                account_session = session.scalar(
                    select(UserSession).where(
                        UserSession.token_hash == token_hash(token),
                        UserSession.revoked_at.is_(None),
                        UserSession.expires_at > now,
                    )
                )
                if account_session is None:
                    raise AccessDenied
                account = session.get(Account, account_session.user_id)
                if account is None or not account.email_verified:
                    raise AccessDenied
                return Identity(
                    account_session.id, account_session.user_id, None, None, ALL_SCOPES, True
                )
            credential = session.scalar(
                select(AccessCredential).where(
                    AccessCredential.token_hash == token_hash(token),
                    AccessCredential.revoked_at.is_(None),
                )
            )
            if credential is None or (
                credential.expires_at is not None and credential.expires_at <= now
            ):
                raise AccessDenied
            if credential.user_id is not None:
                account = session.get(Account, credential.user_id)
                if account is None or not account.email_verified:
                    raise AccessDenied
            credential.last_used_at = now
            role = Role(credential.role)
            return Identity(
                credential.id,
                credential.user_id,
                credential.workspace_id,
                role,
                frozenset(credential.scopes)
                if credential.scopes is not None
                else ROLE_SCOPES[role],
                False,
            )

    def authorize(self, identity: Identity, workspace_id: str, scope: str) -> Principal:
        if identity.workspace_id is not None and identity.workspace_id != workspace_id:
            raise AccessDenied
        if scope not in identity.scopes:
            raise AccessDenied
        role = identity.role
        if identity.user_id is not None:
            with self.database.transaction() as session:
                member = session.get(Membership, (workspace_id, identity.user_id))
                if member is None:
                    raise AccessDenied
                role = Role(member.role)
        if role is None or scope not in ROLE_SCOPES[role]:
            raise AccessDenied
        return Principal(identity.credential_id, workspace_id, role)

    def list_workspaces(self, identity: Identity) -> list[Workspace]:
        if not identity.is_session:
            if identity.workspace_id is None:
                raise AccessDenied
            self.authorize(identity, identity.workspace_id, "workspace:read")
        with self.database.transaction() as session:
            statement = select(Workspace).order_by(Workspace.created_at, Workspace.id)
            if identity.is_session:
                statement = statement.join(Membership).where(Membership.user_id == identity.user_id)
            else:
                statement = statement.where(Workspace.id == identity.workspace_id)
            return list(session.scalars(statement))

    def create_workspace(self, identity: Identity, name: str) -> Workspace:
        if not identity.is_session or identity.user_id is None:
            raise AccessDenied
        try:
            with self.database.transaction() as session:
                session.get(Account, identity.user_id, with_for_update=True)
                count = session.scalar(
                    select(func.count())
                    .select_from(Membership)
                    .where(
                        Membership.user_id == identity.user_id,
                    )
                )
                if count is not None and count >= 100:
                    raise ResourceConflict("Workspace quota exceeded")
                workspace = Workspace(name=name.strip(), created_at=datetime.now(UTC))
                session.add(workspace)
                session.flush()
                session.add(
                    Membership(workspace_id=workspace.id, user_id=identity.user_id, role=Role.OWNER)
                )
                session.add(
                    AuditEvent(
                        workspace_id=workspace.id,
                        actor_id=identity.user_id,
                        action="workspace.create",
                        object_id=workspace.id,
                        created_at=datetime.now(UTC),
                    )
                )
            return workspace
        except IntegrityError as error:
            raise ResourceConflict from error

    def create_key(
        self, identity: Identity, workspace_id: str, name: str, scopes: list[str], expires: datetime
    ) -> tuple[AccessCredential, str]:
        actor = self.authorize(identity, workspace_id, "keys:manage")
        if not scopes or not set(scopes) <= ROLE_SCOPES[actor.role] & identity.scopes:
            raise AccessDenied
        now = datetime.now(UTC)
        if expires.tzinfo is None or not now < expires <= now + timedelta(days=365):
            raise ValueError("Expiration must be timezone-aware and within one year")
        token = "mbk_" + secrets.token_urlsafe(32)
        with self.database.transaction() as session:
            session.get(Workspace, workspace_id, with_for_update=True)
            count = session.scalar(
                select(func.count())
                .select_from(AccessCredential)
                .where(
                    AccessCredential.workspace_id == workspace_id,
                    AccessCredential.revoked_at.is_(None),
                )
            )
            if count is not None and count >= 100:
                raise ResourceConflict("API key quota exceeded")
            key = AccessCredential(
                workspace_id=workspace_id,
                user_id=identity.user_id,
                name=name,
                token_hash=token_hash(token),
                role=actor.role,
                scopes=sorted(set(scopes)),
                expires_at=expires,
                created_at=now,
            )
            session.add(key)
            session.flush()
            session.add(
                AuditEvent(
                    workspace_id=workspace_id,
                    actor_id=identity.credential_id,
                    action="api_key.create",
                    object_id=key.id,
                    created_at=now,
                )
            )
        return key, token

    def keys(
        self, identity: Identity, workspace_id: str, offset: int, limit: int
    ) -> list[AccessCredential]:
        self.authorize(identity, workspace_id, "keys:manage")
        with self.database.transaction() as session:
            return list(
                session.scalars(
                    select(AccessCredential)
                    .where(AccessCredential.workspace_id == workspace_id)
                    .order_by(AccessCredential.created_at, AccessCredential.id)
                    .offset(offset)
                    .limit(limit)
                )
            )

    def revoke_key(self, identity: Identity, workspace_id: str, key_id: str) -> None:
        self.authorize(identity, workspace_id, "keys:manage")
        with self.database.transaction() as session:
            key = session.scalar(
                select(AccessCredential).where(
                    AccessCredential.id == key_id,
                    AccessCredential.workspace_id == workspace_id,
                )
            )
            if key is None:
                raise AccessDenied
            key.revoked_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    workspace_id=workspace_id,
                    actor_id=identity.credential_id,
                    action="api_key.revoke",
                    object_id=key.id,
                    created_at=datetime.now(UTC),
                )
            )

    def logout(self, identity: Identity) -> None:
        if not identity.is_session:
            raise AccessDenied
        with self.database.transaction() as session:
            account_session = session.get(UserSession, identity.credential_id)
            if account_session is None:
                raise AccessDenied
            account_session.revoked_at = datetime.now(UTC)

    def set_member(
        self, identity: Identity, workspace_id: str, email: str, role: Role
    ) -> Membership:
        self.authorize(identity, workspace_id, "members:manage")
        with self.database.transaction() as session:
            session.get(Workspace, workspace_id, with_for_update=True)
            account = session.scalar(select(Account).where(Account.email == email.lower().strip()))
            if account is None or not account.email_verified:
                raise AccessDenied
            member = session.get(Membership, (workspace_id, account.id))
            if member is not None and member.role == Role.OWNER and role != Role.OWNER:
                raise ResourceConflict("Owner demotion is not supported")
            if member is None:
                member = Membership(workspace_id=workspace_id, user_id=account.id, role=role)
                session.add(member)
            else:
                member.role = role
            session.add(
                AuditEvent(
                    workspace_id=workspace_id,
                    actor_id=identity.credential_id,
                    action="membership.set",
                    object_id=account.id,
                    created_at=datetime.now(UTC),
                )
            )
        return member

    def members(
        self, identity: Identity, workspace_id: str, offset: int, limit: int
    ) -> list[Membership]:
        self.authorize(identity, workspace_id, "members:manage")
        with self.database.transaction() as session:
            return list(
                session.scalars(
                    select(Membership)
                    .where(
                        Membership.workspace_id == workspace_id,
                    )
                    .order_by(Membership.user_id)
                    .offset(offset)
                    .limit(limit)
                )
            )

    def remove_member(self, identity: Identity, workspace_id: str, user_id: str) -> None:
        self.authorize(identity, workspace_id, "members:manage")
        with self.database.transaction() as session:
            session.get(Workspace, workspace_id, with_for_update=True)
            member = session.get(Membership, (workspace_id, user_id))
            if member is None:
                raise AccessDenied
            if member.role == Role.OWNER:
                raise ResourceConflict("Owner removal is not supported")
            for credential in session.scalars(
                select(AccessCredential).where(
                    AccessCredential.workspace_id == workspace_id,
                    AccessCredential.user_id == user_id,
                )
            ):
                credential.revoked_at = datetime.now(UTC)
            session.delete(member)
            session.add(
                AuditEvent(
                    workspace_id=workspace_id,
                    actor_id=identity.credential_id,
                    action="membership.remove",
                    object_id=user_id,
                    created_at=datetime.now(UTC),
                )
            )
