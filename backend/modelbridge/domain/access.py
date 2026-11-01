from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMINISTRATOR = "administrator"
    MIGRATION_ENGINEER = "migration_engineer"
    AUDITOR = "auditor"


@dataclass(frozen=True)
class Principal:
    credential_id: str
    workspace_id: str
    role: Role


class AccessDenied(Exception):
    """No access to the requested operation."""


class ResourceConflict(Exception):
    """The requested resource already exists."""
