from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text

from modelbridge.api.app import create_app
from modelbridge.application.workspaces import WorkspaceService, token_hash
from modelbridge.domain.access import AccessDenied, Principal, ResourceConflict, Role
from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.database import Database, migration_config, schema_current
from modelbridge.infrastructure.models import AccessCredential, AuditEvent, Base, WorkerHeartbeat
from modelbridge.worker import heartbeat

pytestmark = pytest.mark.integration


def test_migration_matches_declared_metadata(postgres: tuple[Settings, Engine]) -> None:
    _, engine = postgres
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []


def test_upgrade_idempotent_and_destructive_downgrade_rejected(
    postgres: tuple[Settings, Engine],
) -> None:
    _, engine = postgres
    assert schema_current(engine)
    command.upgrade(migration_config(), "head")
    assert schema_current(engine)
    with pytest.raises(RuntimeError, match="Destructive downgrade"):
        command.downgrade(migration_config(), "base")
    assert schema_current(engine)


def test_api_rejects_uninitialized_schema(postgres: tuple[Settings, Engine]) -> None:
    settings, engine = postgres
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='stale'"))
    with pytest.raises(RuntimeError, match="schema is not current"):
        with TestClient(create_app(settings, engine)):
            pytest.fail("Uninitialized API must not start")


def test_bootstrap_hashes_and_audits_credentials(postgres: tuple[Settings, Engine]) -> None:
    _, engine = postgres
    database = Database(engine)
    service = WorkspaceService(database)
    workspace, token = service.bootstrap("Synthetic students")
    actor = service.authenticate(token)
    assert actor.workspace_id == workspace.id
    with database.transaction() as session:
        credential = session.scalar(select(AccessCredential))
        assert credential is not None
        assert credential.token_hash == token_hash(token)
        assert token not in credential.token_hash
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
    with pytest.raises(ResourceConflict):
        service.bootstrap("Synthetic students")
    with pytest.raises(ValueError):
        service.bootstrap("   ")


def test_role_scope_revocation_and_audit_isolation(postgres: tuple[Settings, Engine]) -> None:
    _, engine = postgres
    database = Database(engine)
    service = WorkspaceService(database)
    first, first_token = service.bootstrap("First")
    second, second_token = service.bootstrap("Second")
    actor = service.authenticate(first_token)
    with pytest.raises(AccessDenied):
        service.get_workspace(actor, second.id)
    with pytest.raises(AccessDenied):
        service.rename(actor, second.id, "Forbidden")
    auditor = Principal(actor.credential_id, actor.workspace_id, Role.AUDITOR)
    with pytest.raises(AccessDenied):
        service.rename(auditor, first.id, "Forbidden")
    assert all(event.workspace_id == first.id for event in service.audit(actor, 0, 100))
    with database.transaction() as session:
        credential = session.scalar(
            select(AccessCredential).where(AccessCredential.token_hash == token_hash(second_token))
        )
        assert credential is not None
        credential.revoked_at = datetime.now(UTC)
    with pytest.raises(AccessDenied):
        service.authenticate(second_token)


def test_readiness_requires_fresh_idle_worker(postgres: tuple[Settings, Engine]) -> None:
    settings, engine = postgres
    database = Database(engine)
    with TestClient(create_app(settings, engine)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/version").json()["release_status"] == "development"
        assert client.get("/ready").status_code == 503
        heartbeat(database, settings.worker_id)
        assert client.get("/ready").status_code == 200
        heartbeat(database, settings.worker_id, "stopped")
        assert client.get("/ready").status_code == 503
        heartbeat(database, settings.worker_id)
        with database.transaction() as session:
            worker = session.get(WorkerHeartbeat, settings.worker_id)
            assert worker is not None
            worker.seen_at = datetime.now(UTC) - timedelta(minutes=5)
        assert client.get("/ready").status_code == 503


def test_authenticated_api_redaction_cors_and_pagination(
    postgres: tuple[Settings, Engine],
) -> None:
    settings, engine = postgres
    database = Database(engine)
    service = WorkspaceService(database)
    first, token = service.bootstrap("First")
    second, _ = service.bootstrap("Second")
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(settings, engine)) as client:
        assert client.get("/api/v1/workspaces").status_code == 401
        assert (
            client.get("/api/v1/workspaces", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        result = client.get("/api/v1/workspaces", headers=headers)
        assert [workspace["id"] for workspace in result.json()] == [first.id]
        assert token not in result.text
        assert "token_hash" not in result.text
        assert result.headers["X-Request-ID"]
        assert result.headers["Cache-Control"] == "no-store"
        assert (
            client.patch(
                f"/api/v1/workspaces/{second.id}", json={"name": "Forbidden"}, headers=headers
            ).status_code
            == 404
        )
        assert (
            client.patch(
                f"/api/v1/workspaces/{first.id}", json={"name": "Renamed"}, headers=headers
            ).status_code
            == 200
        )
        invalid = client.patch(
            f"/api/v1/workspaces/{first.id}",
            json={"name": "Renamed", "password": "DO-NOT-ECHO"},
            headers=headers,
        )
        assert invalid.status_code == 422
        assert "DO-NOT-ECHO" not in invalid.text
        assert len(client.get("/api/v1/audit?limit=1", headers=headers).json()) == 1
        assert client.get("/api/v1/audit?limit=1000", headers=headers).status_code == 422
        assert client.get("/api/openapi.json").status_code == 200
        cors = client.options(
            "/api/v1/workspaces",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert cors.status_code == 200
        rejected_cors = client.options(
            "/api/v1/workspaces",
            headers={"Origin": "http://untrusted.example", "Access-Control-Request-Method": "GET"},
        )
        assert rejected_cors.status_code == 400


def test_metadata_persists_across_application_restart(postgres: tuple[Settings, Engine]) -> None:
    settings, engine = postgres
    service = WorkspaceService(Database(engine))
    workspace, token = service.bootstrap("Persistent")
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(2):
        with TestClient(create_app(settings, engine)) as client:
            assert client.get("/api/v1/workspaces", headers=headers).json()[0]["id"] == workspace.id
            assert len(client.get("/api/v1/audit", headers=headers).json()) == 1
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM workspaces")).scalar_one() == 1
