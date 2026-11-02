from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from modelbridge.domain.access import Role


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class AccountInput(Input):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: SecretStr = Field(min_length=12, max_length=128)


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    created_at: datetime


class RegistrationInput(AccountInput):
    registration_code: SecretStr = Field(min_length=40, max_length=128)


class SessionResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 -- protocol name, not a credential
    expires_at: datetime


class WorkspaceInput(Input):
    name: str = Field(min_length=1, max_length=100, pattern=r".*\S.*")


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    created_at: datetime


class KeyInput(Input):
    name: str = Field(min_length=1, max_length=100, pattern=r".*\S.*")
    scopes: list[str] = Field(min_length=1, max_length=12)
    expires_at: datetime


class KeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    name: str | None
    scopes: list[str] | None
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class KeyCreated(KeyResponse):
    api_key: str


class MembershipInput(Input):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    role: Role


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: str
    workspace_id: str
    role: Role


class ConnectionInput(Input):
    name: str = Field(min_length=1, max_length=100, pattern=r".*\S.*")
    host: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9.:-]+$")
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=100)
    password: SecretStr | None = Field(default=None, max_length=512)
    sslmode: Literal["verify-full", "require", "disable"] = "verify-full"


class ConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    name: str
    host: str
    port: int
    database: str
    username: str
    sslmode: str
    created_at: datetime


class ConnectionPage(BaseModel):
    items: list[ConnectionResponse]
    offset: int
    limit: int
    count: int


class ConnectionTestResponse(BaseModel):
    connected: bool
    server_version_num: int


class DiscoverInput(Input):
    schema_name: str = Field(
        default="public", min_length=1, max_length=63, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )


class SnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    connection_id: str
    schema_name: str
    fingerprint: str
    objects: dict[str, object]
    created_at: datetime


class SnapshotPage(BaseModel):
    items: list[SnapshotResponse]
    offset: int
    limit: int
    count: int
