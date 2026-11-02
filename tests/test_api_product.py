import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.engine import make_url

from modelbridge.api.app import create_app
from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.database import Database
from modelbridge.infrastructure.models import (
    AccessCredential,
    Account,
    DatabaseConnection,
    RegistrationPermit,
    UserSession,
)
from modelbridge.sdk import ModelBridge

pytestmark = pytest.mark.integration


def permit(client: TestClient, email: str) -> str:
    # Fixture provisioning represents trusted CLI issuance/private email delivery.
    code = "mbr_" + secrets.token_urlsafe(32)
    with client.app.state.database.transaction() as session:
        session.add(
            RegistrationPermit(
                token_hash=hashlib.sha256(code.encode()).hexdigest(),
                email=email,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    return code


def test_sdk_uses_public_account_workspace_key_contract(postgres: tuple[Settings, Engine]) -> None:
    settings, engine = postgres
    with TestClient(
        create_app(settings.model_copy(update={"registration_enabled": True}), engine)
    ) as client:

        def relay(request: httpx.Request) -> httpx.Response:
            response = client.request(
                request.method, str(request.url), headers=request.headers, content=request.content
            )
            return httpx.Response(
                response.status_code, headers=response.headers, content=response.content
            )

        with ModelBridge("http://127.0.0.1", transport=httpx.MockTransport(relay)) as sdk:
            password = SecretStr("synthetic-sdk-password")
            sdk.register(
                "sdk@synthetic.example",
                password,
                SecretStr(permit(client, "sdk@synthetic.example")),
            )
            session = sdk.login("sdk@synthetic.example", password)
            session_leaked = session.token.get_secret_value() in repr(session)
            assert not session_leaked
            selected = sdk.create_workspace("SDK public contract")
            sdk.workspace_id = selected.id
            assert sdk.workspaces()[0].id == selected.id
            key = sdk.create_key(
                "SDK reader", ["workspace:read"], datetime.now(UTC) + timedelta(days=1)
            )
            key_leaked = key.secret.get_secret_value() in repr(key)
            assert not key_leaked
            assert sdk.keys()[0].id == key.metadata.id
            sdk.revoke_key(key.metadata.id)
            sdk.logout()
        assert client.get("/api/guides").status_code == 200
        assert client.get("/api/guides/API_QUICKSTART.md").status_code == 200
        spec = client.get("/api/openapi.json").json()
        assert "/api/v1/auth/register" in spec["paths"]
        error_schema = spec["paths"]["/api/v1/auth/register"]["post"]["responses"]["422"]
        assert error_schema["content"]["application/json"]["schema"]["$ref"].endswith(
            "ErrorResponse"
        )


def account(client: TestClient, name: str) -> dict[str, str]:
    body = {"email": name + "@synthetic.example", "password": "synthetic-password-only"}
    registration = {**body, "registration_code": permit(client, body["email"])}
    assert client.post("/api/v1/auth/register", json=registration).status_code == 201
    result = client.post("/api/v1/auth/login", json=body)
    assert result.status_code == 200
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def workspace(client: TestClient, headers: dict[str, str], name: str) -> str:
    response = client.post("/api/v1/workspaces", json={"name": name}, headers=headers)
    assert response.status_code == 201
    return str(response.json()["id"])


def test_registration_proof_prevents_email_impersonation_and_replay(
    postgres: tuple[Settings, Engine],
) -> None:
    settings, engine = postgres
    with TestClient(
        create_app(settings.model_copy(update={"registration_enabled": True}), engine)
    ) as client:
        body = {"email": "recipient@synthetic.example", "password": "synthetic-password-only"}
        assert client.post("/api/v1/auth/register", json=body).status_code == 422
        assert (
            client.post(
                "/api/v1/auth/register",
                json={
                    **body,
                    "registration_code": "mbr_" + "A" * 43,
                },
            ).status_code
            == 404
        )
        code = permit(client, body["email"])
        assert (
            client.post(
                "/api/v1/auth/register",
                json={
                    **body,
                    "email": "impostor@synthetic.example",
                    "registration_code": code,
                },
            ).status_code
            == 404
        )
        response = client.post("/api/v1/auth/register", json={**body, "registration_code": code})
        assert response.status_code == 201
        assert (
            client.post(
                "/api/v1/auth/register",
                json={
                    **body,
                    "registration_code": code,
                },
            ).status_code
            == 404
        )
        with Database(engine).transaction() as session:
            stored = session.get(RegistrationPermit, hashlib.sha256(code.encode()).hexdigest())
            assert stored is not None and stored.consumed_at is not None
            assert session.get(Account, response.json()["id"]).email_verified
        expired = permit(client, "expired@synthetic.example")
        with Database(engine).transaction() as session:
            stored = session.get(RegistrationPermit, hashlib.sha256(expired.encode()).hexdigest())
            stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert (
            client.post(
                "/api/v1/auth/register",
                json={
                    **body,
                    "email": "expired@synthetic.example",
                    "registration_code": expired,
                },
            ).status_code
            == 404
        )


def test_unverified_legacy_identity_cannot_receive_membership(
    postgres: tuple[Settings, Engine],
) -> None:
    settings, engine = postgres
    with TestClient(
        create_app(settings.model_copy(update={"registration_enabled": True}), engine)
    ) as client:
        owner = account(client, "verified-owner")
        wid = workspace(client, owner, "Verified membership")
        old_token = "mbs_" + secrets.token_urlsafe(32)
        with Database(engine).transaction() as session:
            legacy = Account(
                email="legacy@synthetic.example",
                password_hash="irrelevant-unverified",
                created_at=datetime.now(UTC),
                email_verified=False,
            )
            session.add(legacy)
            session.flush()
            session.add(
                UserSession(
                    user_id=legacy.id,
                    token_hash=hashlib.sha256(old_token.encode()).hexdigest(),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        old_headers = {"Authorization": "Bearer " + old_token}
        assert client.get("/api/v1/workspaces", headers=old_headers).status_code == 401
        assert (
            client.put(
                f"/api/v1/workspaces/{wid}/members",
                headers=owner,
                json={
                    "email": "legacy@synthetic.example",
                    "role": "administrator",
                },
            ).status_code
            == 404
        )
        recovered = client.post(
            "/api/v1/auth/register",
            json={
                "email": "legacy@synthetic.example",
                "password": "new-synthetic-password",
                "registration_code": permit(client, "legacy@synthetic.example"),
            },
        )
        assert recovered.status_code == 201
        assert client.get("/api/v1/workspaces", headers=old_headers).status_code == 401
        with Database(engine).transaction() as session:
            old = session.scalar(
                select(UserSession).where(
                    UserSession.token_hash == hashlib.sha256(old_token.encode()).hexdigest()
                )
            )
            assert old.revoked_at is not None


def test_account_keys_scopes_expiry_revocation_and_sessions(
    postgres: tuple[Settings, Engine],
) -> None:
    settings, engine = postgres
    with TestClient(
        create_app(settings.model_copy(update={"registration_enabled": True}), engine)
    ) as client:
        owner = account(client, "owner")
        wid = workspace(client, owner, "Account workspace")
        path = f"/api/v1/workspaces/{wid}/keys"
        key_response = client.post(
            path,
            headers=owner,
            json={
                "name": "Reader",
                "scopes": ["workspace:read"],
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )
        assert key_response.status_code == 201
        issued = key_response.json()
        key_headers = {"Authorization": "Bearer " + issued["api_key"]}
        assert client.get("/api/v1/workspaces", headers=key_headers).status_code == 200
        assert (
            client.patch(
                f"/api/v1/workspaces/{wid}", headers=key_headers, json={"name": "Forbidden"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                path,
                headers=key_headers,
                json={
                    "name": "Escalation",
                    "scopes": ["workspace:manage"],
                    "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                },
            ).status_code
            == 404
        )
        listing = client.get(path, headers=owner)
        assert listing.status_code == 200
        leaked = issued["api_key"] in listing.text
        assert not leaked
        assert listing.json()[0]["last_used_at"] is not None
        with Database(engine).transaction() as session:
            key = session.get(AccessCredential, issued["id"])
            assert key is not None
            key.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert client.get("/api/v1/workspaces", headers=key_headers).status_code == 401
        assert client.delete(path + "/" + issued["id"], headers=owner).status_code == 204
        assert client.post("/api/v1/auth/logout", headers=owner).status_code == 204
        assert client.get("/api/v1/workspaces", headers=owner).status_code == 401


def test_workspace_bola_membership_and_scope_delegation(postgres: tuple[Settings, Engine]) -> None:
    settings, engine = postgres
    with TestClient(
        create_app(settings.model_copy(update={"registration_enabled": True}), engine)
    ) as client:
        first = account(client, "first")
        second = account(client, "second")
        wid = workspace(client, first, "First account workspace")
        other = workspace(client, second, "Second account workspace")
        member_path = f"/api/v1/workspaces/{wid}/members"
        assert (
            client.put(
                member_path,
                headers=first,
                json={
                    "email": "second@synthetic.example",
                    "role": "auditor",
                },
            ).status_code
            == 200
        )
        assert (
            client.patch(
                f"/api/v1/workspaces/{wid}", headers=second, json={"name": "Forbidden"}
            ).status_code
            == 404
        )
        assert client.get(f"/api/v1/workspaces/{other}/keys", headers=first).status_code == 404
        assert (
            client.put(
                member_path,
                headers=second,
                json={
                    "email": "second@synthetic.example",
                    "role": "owner",
                },
            ).status_code
            == 404
        )
        assert (
            client.put(
                member_path,
                headers=first,
                json={
                    "email": "first@synthetic.example",
                    "role": "auditor",
                },
            ).status_code
            == 409
        )
        key_path = f"/api/v1/workspaces/{wid}/keys"
        expires = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        limited = client.post(
            key_path,
            headers=first,
            json={
                "name": "Key administrator",
                "scopes": ["keys:manage"],
                "expires_at": expires,
            },
        )
        assert limited.status_code == 201
        limited_header = {"Authorization": "Bearer " + limited.json()["api_key"]}
        assert (
            client.post(
                key_path,
                headers=limited_header,
                json={
                    "name": "Broader key",
                    "scopes": ["connections:write"],
                    "expires_at": expires,
                },
            ).status_code
            == 404
        )
        members = client.get(member_path, headers=first).json()
        second_id = next(row["user_id"] for row in members if row["role"] == "auditor")
        assert client.delete(member_path + "/" + second_id, headers=first).status_code == 204
        assert (
            client.get(f"/api/v1/workspaces/{wid}/connections", headers=second).status_code == 404
        )


def test_request_limits_ignore_spoofed_forwarded_address(postgres: tuple[Settings, Engine]) -> None:
    settings, engine = postgres
    configuration = settings.model_copy(
        update={
            "registration_enabled": True,
            "max_request_bytes": 1024,
            "anonymous_requests_per_minute": 5,
        }
    )
    with TestClient(create_app(configuration, engine)) as client:
        large = client.post(
            "/api/v1/auth/login", content=b"x" * 2048, headers={"Content-Length": "1"}
        )
        assert large.status_code == 413
        assert large.json()["error"]["request_id"] == large.headers["X-Request-ID"]
        for index in range(4):
            assert (
                client.get(
                    "/api/v1/workspaces",
                    headers={
                        "X-Forwarded-For": "192.0.2." + str(index),
                    },
                ).status_code
                == 401
            )
        limited = client.get("/api/v1/workspaces", headers={"X-Forwarded-For": "192.0.2.99"})
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) > 0
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("host", "localhost"),
        ("port", 5433),
        ("database", "different_database"),
        ("username", "different_user"),
        ("sslmode", "disable"),
    ],
)
def test_connection_destination_changes_require_new_secret_and_tls_cannot_downgrade(
    postgres: tuple[Settings, Engine],
    field: str,
    replacement: object,
) -> None:
    settings, engine = postgres
    configuration = settings.model_copy(
        update={
            "registration_enabled": True,
            "allowed_database_networks": ["127.0.0.1/32", "::1/128"],
        }
    )
    with TestClient(create_app(configuration, engine)) as client:
        owner = account(client, "connection-owner")
        wid = workspace(client, owner, "Credential destination")
        path = f"/api/v1/workspaces/{wid}/connections"
        body = {
            "name": "Synthetic connection",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "synthetic_db",
            "username": "synthetic_user",
            "password": "synthetic-original-password",
            "sslmode": "verify-full",
        }
        response = client.post(path, headers=owner, json=body)
        assert response.status_code == 201
        cid = response.json()["id"]
        changed = {key: value for key, value in body.items() if key != "password"}
        changed[field] = replacement
        if field == "sslmode":
            changed["password"] = "synthetic-replacement-password"
        assert client.patch(path + "/" + cid, headers=owner, json=changed).status_code == 422
        with Database(engine).transaction() as session:
            record = session.get(DatabaseConnection, cid)
            assert record.host == body["host"]
            assert record.port == body["port"]
            assert record.database == body["database"]
            assert record.username == body["username"]
            assert record.sslmode == body["sslmode"]
        renamed = {key: value for key, value in body.items() if key != "password"}
        renamed["name"] = "Renamed safely"
        assert client.patch(path + "/" + cid, headers=owner, json=renamed).status_code == 200
        if field != "sslmode":
            changed["password"] = "synthetic-replacement-password"
            assert client.patch(path + "/" + cid, headers=owner, json=changed).status_code == 200


def test_public_api_connection_discovery_with_independent_databases(
    postgres: tuple[Settings, Engine],
) -> None:
    settings, metadata_engine = postgres
    base = make_url(settings.database_url.get_secret_value()).difference_update_query(["options"])
    names = ["modelbridge_test_source_" + uuid4().hex, "modelbridge_test_dest_" + uuid4().hex]
    admin_url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    created: list[str] = []
    engines: list[Engine] = []
    try:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            for name in names:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                created.append(name)
        for name in names:
            engines.append(create_engine(base.set(database=name), hide_parameters=True))
        with engines[0].begin() as database:
            database.execute(
                text("CREATE TABLE teachers(id integer PRIMARY KEY, name text NOT NULL)")
            )
            database.execute(
                text(
                    "CREATE TABLE students(student_id integer PRIMARY KEY, "
                    "first_name text NOT NULL, "
                    "registration_date text, teacher_id integer REFERENCES teachers(id))"
                )
            )
            database.execute(text("INSERT INTO teachers VALUES(1,'Synthetic teacher')"))
            database.execute(
                text(
                    "INSERT INTO students VALUES(1,'Synthetic student','31/10/2026',1),"
                    "(2,'Malformed student','not-a-date',NULL)"
                )
            )
        with engines[1].begin() as database:
            database.execute(
                text(
                    "CREATE TABLE students(id uuid PRIMARY KEY, given_name text NOT NULL, "
                    "created_at date)"
                )
            )
        configuration = settings.model_copy(
            update={
                "registration_enabled": True,
                "allowed_database_networks": ["127.0.0.1/32"],
            }
        )
        with TestClient(create_app(configuration, metadata_engine)) as client:
            owner = account(client, "database-owner")
            wid = workspace(client, owner, "Independent database workspace")
            path = f"/api/v1/workspaces/{wid}/connections"
            connection_ids: list[str] = []
            for index, name in enumerate(names):
                created_connection = client.post(
                    path,
                    headers=owner,
                    json={
                        "name": "source" if index == 0 else "destination",
                        "host": "127.0.0.1",
                        "port": base.port,
                        "database": name,
                        "username": base.username,
                        "password": base.password,
                        "sslmode": "disable",
                    },
                )
                assert created_connection.status_code == 201
                assert "password" not in created_connection.json()
                connection_id = created_connection.json()["id"]
                connection_ids.append(connection_id)
                assert (
                    client.post(path + "/" + connection_id + "/test", headers=owner).status_code
                    == 200
                )
            changes = {
                "name": "source",
                "host": "127.0.0.1",
                "port": base.port,
                "database": names[1],
                "username": base.username,
                "sslmode": "disable",
            }
            assert (
                client.patch(
                    path + "/" + connection_ids[0],
                    headers=owner,
                    json=changes,
                ).status_code
                == 422
            )
            with Database(metadata_engine).transaction() as session:
                record = session.get(DatabaseConnection, connection_ids[0])
                assert record.database == names[0]
            discovered = client.post(
                path + "/" + connection_ids[0] + "/discover", headers=owner, json={}
            )
            assert discovered.status_code == 201
            schema = discovered.json()
            assert schema["objects"]["tables"]["students"]["foreign_keys"]
            repeated = client.post(
                path + "/" + connection_ids[0] + "/discover", headers=owner, json={}
            )
            assert repeated.json()["fingerprint"] == schema["fingerprint"]
            destination = client.post(
                path + "/" + connection_ids[1] + "/discover", headers=owner, json={}
            )
            assert destination.status_code == 201
            assert destination.json()["fingerprint"] != schema["fingerprint"]
            snap_path = f"/api/v1/workspaces/{wid}/snapshots/{schema['id']}"
            assert client.get(snap_path, headers=owner).status_code == 200
            with Database(metadata_engine).transaction() as session:
                record = session.get(DatabaseConnection, connection_ids[0])
                assert record is not None
                contains_password = (
                    base.password in record.password_envelope if base.password else False
                )
                assert not contains_password
            assert client.delete(path + "/" + connection_ids[0], headers=owner).status_code == 204
            assert (
                client.post(path + "/" + connection_ids[0] + "/test", headers=owner).status_code
                == 404
            )
            assert client.get(snap_path, headers=owner).status_code == 200
        with engines[0].connect() as database:
            assert database.execute(text("SELECT count(*) FROM students")).scalar_one() == 2
        with engines[1].connect() as database:
            assert database.execute(text("SELECT count(*) FROM students")).scalar_one() == 0
    finally:
        for engine in engines:
            engine.dispose()
        with psycopg.connect(admin_url, autocommit=True) as admin:
            for name in created:
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
