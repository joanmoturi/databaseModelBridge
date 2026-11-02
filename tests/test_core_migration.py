import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from modelbridge.infrastructure.config import Settings
from modelbridge.migration_contracts import JobInput, PlanInput
from modelbridge.sdk import ModelBridge

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]
FINAL = {"completed", "completed_with_errors", "failed", "cancelled"}


@dataclass
class Environment:
    settings: Settings
    metadata: Engine
    source: Engine
    destination: Engine
    names: list[str]
    api: httpx.Client
    environment: dict[str, str]
    processes: list[subprocess.Popen[bytes]]
    logs: object

    def worker(self) -> subprocess.Popen[bytes]:
        environment = dict(self.environment)
        process = subprocess.Popen(
            [sys.executable, "-m", "modelbridge.worker"],
            cwd=ROOT,
            env=environment,
            stdout=self.logs,
            stderr=self.logs,
        )
        self.processes.append(process)
        return process

    def poll(self, path: str, headers: dict[str, str], timeout: float = 120) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.api.get(path, headers=headers)
            assert response.status_code == 200
            result = response.json()
            if result["state"] in FINAL:
                return result
            time.sleep(0.15)
        pytest.fail("Owned migration job did not reach a terminal state")


@pytest.fixture
def core(postgres: tuple[Settings, Engine]) -> Iterator[Environment]:
    settings, metadata = postgres
    base = make_url(settings.database_url.get_secret_value()).difference_update_query(["options"])
    names = ["modelbridge_test_core_" + uuid4().hex for _ in range(2)]
    admin_url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    created: list[str] = []
    engines: list[Engine] = []
    processes: list[subprocess.Popen[bytes]] = []
    try:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            for name in names:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                created.append(name)
        engines = [create_engine(base.set(database=name), hide_parameters=True) for name in names]
        environment = dict(
            os.environ,
            **{
                "MODELBRIDGE_DATABASE_URL": settings.database_url.get_secret_value(),
                "MODELBRIDGE_ENCRYPTION_KEYS": settings.encryption_keys.get_secret_value(),
                "MODELBRIDGE_ENVIRONMENT": "test",
                "MODELBRIDGE_REGISTRATION_ENABLED": "true",
                "MODELBRIDGE_ALLOWED_DATABASE_NETWORKS": '["127.0.0.1/32"]',
                "MODELBRIDGE_ALLOWED_WEBHOOK_NETWORKS": '["127.0.0.1/32"]',
                "MODELBRIDGE_ANONYMOUS_REQUESTS_PER_MINUTE": "1000",
                "MODELBRIDGE_REQUESTS_PER_MINUTE": "10000",
                "MODELBRIDGE_SCHEMA_DISCOVERIES_PER_MINUTE": "60",
                "MODELBRIDGE_WORKER_ID": "core-" + uuid4().hex[:16],
            },
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with tempfile.TemporaryFile() as logs:
            server = subprocess.Popen(  # noqa: S603 -- fixed interpreter/module, owned ephemeral port
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "modelbridge.api.app:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
                cwd=ROOT,
                env=environment,
                stdout=logs,
                stderr=logs,
            )
            processes.append(server)
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as api:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        pytest.fail("Owned API exited at startup")
                    try:
                        if api.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("Owned API did not start")
                yield Environment(
                    settings,
                    metadata,
                    engines[0],
                    engines[1],
                    names,
                    api,
                    environment,
                    processes,
                    logs,
                )
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        for engine in engines:
            engine.dispose()
        with psycopg.connect(admin_url, autocommit=True) as admin:
            for name in created:
                for attempt in range(20):
                    try:
                        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                        break
                    except psycopg.errors.ObjectInUse:
                        if attempt == 19:
                            raise
                        time.sleep(0.1)


def bootstrap(env: Environment) -> tuple[str, dict[str, str]]:
    email = uuid4().hex + "@synthetic.example"
    cli = Path(sys.executable).parent / ("modelbridge.exe" if os.name == "nt" else "modelbridge")
    issued = subprocess.run(  # noqa: S603 -- installed CLI, synthetic email, owned metadata
        [str(cli), "registration-code", "--email", email],
        cwd=ROOT,
        env=env.environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if issued.returncode:
        pytest.fail("Documented operator registration-code command failed")
    code = issued.stdout.decode().splitlines()[-1]
    body = {"email": email, "password": "synthetic-core-password"}
    assert (
        env.api.post(
            "/api/v1/auth/register",
            json={
                **body,
                "registration_code": code,
            },
        ).status_code
        == 201
    )
    login = env.api.post("/api/v1/auth/login", json=body)
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    workspace = env.api.post(
        "/api/v1/workspaces",
        headers=headers,
        json={
            "name": "Core acceptance " + uuid4().hex,
        },
    )
    assert workspace.status_code == 201
    return "/api/v1/workspaces/" + workspace.json()["id"], headers


def snapshots(env: Environment, path: str, headers: dict[str, str]) -> list[str]:
    url = make_url(env.settings.database_url.get_secret_value())
    result: list[str] = []
    for index, name in enumerate(env.names):
        connection = env.api.post(
            path + "/connections",
            headers=headers,
            json={
                "name": "source" if index == 0 else "destination",
                "host": "127.0.0.1",
                "port": url.port,
                "database": name,
                "username": url.username,
                "password": url.password,
                "sslmode": "disable",
            },
        )
        assert connection.status_code == 201
        cid = connection.json()["id"]
        assert (
            env.api.post(path + "/connections/" + cid + "/test", headers=headers).status_code == 200
        )
        discovered = env.api.post(
            path + "/connections/" + cid + "/discover", headers=headers, json={}
        )
        assert discovered.status_code == 201
        result.append(discovered.json()["id"])
    return result


def plan(
    env: Environment,
    path: str,
    headers: dict[str, str],
    ids: list[str],
    mappings: list[dict[str, object]],
) -> str:
    comparison = env.api.post(
        path + "/schemas/compare",
        headers=headers,
        json={
            "source_snapshot_id": ids[0],
            "destination_snapshot_id": ids[1],
        },
    )
    assert comparison.status_code == 200
    created = env.api.post(
        path + "/plans",
        headers=headers,
        json={
            "source_snapshot_id": ids[0],
            "destination_snapshot_id": ids[1],
            "tables": mappings,
        },
    )
    assert created.status_code == 201
    pid = created.json()["id"]
    assert env.api.post(path + "/plans/" + pid + "/validate", headers=headers).status_code == 200
    approval = env.api.post(path + "/plans/" + pid + "/approve", headers=headers)
    assert approval.status_code == 200
    return pid


def submit(env: Environment, path: str, headers: dict[str, str], pid: str, mode: str) -> str:
    created = env.api.post(
        path + "/jobs",
        headers={**headers, "Idempotency-Key": uuid4().hex},
        json={
            "plan_id": pid,
            "mode": mode,
            "batch_size": 2,
            "sample_size": 1,
        },
    )
    assert created.status_code == 202
    return path + "/jobs/" + created.json()["id"]


def field(
    source: str, transform: str = "identity", reference: str | None = None
) -> dict[str, object]:
    result: dict[str, object] = {"source": source, "transform": transform}
    if reference is not None:
        result["reference_table"] = reference
    return result


def student_schema(env: Environment) -> list[dict[str, object]]:
    with env.source.begin() as connection:
        connection.execute(
            text("CREATE TABLE teachers(tid integer PRIMARY KEY, teacher_name text NOT NULL)")
        )
        connection.execute(
            text(
                "CREATE TABLE courses(cid integer PRIMARY KEY, course_title text NOT NULL, "
                "tid integer NOT NULL REFERENCES teachers(tid))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE students(sid integer PRIMARY KEY, "
                "student_name text NOT NULL, email text, "
                "joined text, tid integer REFERENCES teachers(tid))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE enrollments(eid integer PRIMARY KEY, sid integer NOT NULL, "
                "cid integer NOT NULL REFERENCES courses(cid))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE payments(pid integer PRIMARY KEY, eid integer NOT NULL, amount text)"
            )
        )
        connection.execute(text("INSERT INTO teachers VALUES(1,'Synthetic T1'),(2,'Synthetic T2')"))
        connection.execute(
            text("INSERT INTO courses VALUES(1,'Synthetic C1',1),(2,'Synthetic C2',2)")
        )
        connection.execute(
            text(
                "INSERT INTO students VALUES"
                "(1,' Student One ','one@synthetic.example','31/10/2026',1),"
                "(2,'Student Two',NULL,'01/11/2026',2),"
                "(3,'Bad Date','three@synthetic.example','not-a-date',NULL),"
                "(4,'Duplicate Email','one@synthetic.example','01/11/2026',1)"
            )
        )
        connection.execute(text("INSERT INTO enrollments VALUES(1,1,1),(2,2,2),(3,999,1)"))
        connection.execute(
            text("INSERT INTO payments VALUES(1,1,'100.50'),(2,2,'200'),(3,3,'bad-amount')")
        )
    with env.destination.begin() as connection:
        connection.execute(text("CREATE TABLE teachers(id uuid PRIMARY KEY, name text NOT NULL)"))
        connection.execute(
            text(
                "CREATE TABLE courses(id uuid PRIMARY KEY, title text NOT NULL, "
                "teacher_id uuid NOT NULL REFERENCES teachers(id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE students(id uuid PRIMARY KEY, "
                "given_name text NOT NULL, email text UNIQUE, "
                "joined_on date NOT NULL, teacher_id uuid REFERENCES teachers(id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE enrollments(id uuid PRIMARY KEY, "
                "student_id uuid NOT NULL REFERENCES students(id), "
                "course_id uuid NOT NULL REFERENCES courses(id))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE payments(id uuid PRIMARY KEY, "
                "enrollment_id uuid NOT NULL REFERENCES enrollments(id), "
                "total numeric(12,2) NOT NULL)"
            )
        )
    return [
        {
            "source_table": "payments",
            "destination_table": "payments",
            "source_key": "pid",
            "destination_key": "id",
            "fields": {
                "id": field("pid", "uuid"),
                "enrollment_id": field("eid", "reference", "enrollments"),
                "total": field("amount", "decimal"),
            },
        },
        {
            "source_table": "enrollments",
            "destination_table": "enrollments",
            "source_key": "eid",
            "destination_key": "id",
            "fields": {
                "id": field("eid", "uuid"),
                "student_id": field("sid", "reference", "students"),
                "course_id": field("cid", "reference", "courses"),
            },
        },
        {
            "source_table": "students",
            "destination_table": "students",
            "source_key": "sid",
            "destination_key": "id",
            "duplicate_fields": ["given_name"],
            "fields": {
                "id": field("sid", "uuid"),
                "given_name": field("student_name", "trim"),
                "email": field("email"),
                "joined_on": field("joined", "date_dmy"),
                "teacher_id": field("tid", "reference", "teachers"),
            },
        },
        {
            "source_table": "courses",
            "destination_table": "courses",
            "source_key": "cid",
            "destination_key": "id",
            "fields": {
                "id": field("cid", "uuid"),
                "title": field("course_title"),
                "teacher_id": field("tid", "reference", "teachers"),
            },
        },
        {
            "source_table": "teachers",
            "destination_table": "teachers",
            "source_key": "tid",
            "destination_key": "id",
            "fields": {"id": field("tid", "uuid"), "name": field("teacher_name")},
        },
    ]


def test_complete_public_api_student_migration_reconciliation_and_signed_webhooks(
    core: Environment,
) -> None:
    mappings = student_schema(core)
    path, headers = bootstrap(core)
    ids = snapshots(core, path, headers)
    pid = plan(core, path, headers, ids, mappings)
    received: list[tuple[str, bool]] = []
    webhook_secret = ""
    reject_secret = ""
    first = True

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal first
            body = self.rfile.read(int(self.headers["Content-Length"]))
            signature = self.headers["X-ModelBridge-Signature"]
            timestamp, signature_value = signature.split(",")
            calculated = hmac.new(
                (reject_secret if self.path == "/reject" else webhook_secret).encode(),
                timestamp[2:].encode() + b"." + body,
                hashlib.sha256,
            ).hexdigest()
            received.append(
                (json.loads(body)["type"], hmac.compare_digest(calculated, signature_value[3:]))
            )
            self.send_response(503 if first or self.path == "/reject" else 204)
            first = False
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    try:
        webhook = core.api.post(
            path + "/webhooks",
            headers=headers,
            json={
                "url": f"http://127.0.0.1:{receiver.server_port}/events",
                "subscriptions": [
                    "migration.created",
                    "migration.started",
                    "migration.progress",
                    "migration.completed",
                    "reconciliation.completed",
                ],
            },
        )
        assert webhook.status_code == 201
        webhook_secret = webhook.json()["secret"]
        hook_id = webhook.json()["id"]
        listing = core.api.get(path + "/webhooks", headers=headers)
        leaked = webhook_secret in listing.text
        assert not leaked
        bad = core.api.post(
            path + "/webhooks",
            headers=headers,
            json={
                "url": f"http://127.0.0.1:{receiver.server_port}/reject",
                "subscriptions": ["migration.completed"],
            },
        )
        assert bad.status_code == 201
        failed_id = bad.json()["id"]
        reject_secret = bad.json()["secret"]
        core.worker()
        dry = submit(core, path, headers, pid, "dry_run")
        assert core.poll(dry, headers)["state"] == "completed_with_errors"
        with core.destination.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM students)+(SELECT count(*) FROM teachers)+"
                        "(SELECT count(*) FROM courses)+(SELECT count(*) FROM enrollments)+"
                        "(SELECT count(*) FROM payments)"
                    )
                ).scalar_one()
                == 0
            )
        sample = submit(core, path, headers, pid, "sample")
        assert core.poll(sample, headers)["state"] == "completed"
        with core.destination.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM students")).scalar_one() == 1
        full = submit(core, path, headers, pid, "full")
        result = core.poll(full, headers)
        assert result["state"] == "completed_with_errors"
        records = core.api.get(full + "/records", headers=headers).json()
        assert len(records) == 14
        assert sum(item["status"] == "failed" for item in records) == 4
        with core.destination.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM students")).scalar_one() == 2
            assert connection.execute(
                text("SELECT sum(total) FROM payments")
            ).scalar_one() == Decimal("300.50")
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM enrollments e JOIN students s ON s.id=e.student_id "
                        "JOIN courses c ON c.id=e.course_id"
                    )
                ).scalar_one()
                == 2
            )
            assert (
                connection.execute(
                    text("SELECT given_name FROM students WHERE email='one@synthetic.example'")
                ).scalar_one()
                == "Student One"
            )
            assert isinstance(
                connection.execute(text("SELECT id FROM students LIMIT 1")).scalar_one(), UUID
            )
            assert (
                connection.execute(
                    text("SELECT joined_on FROM students WHERE email='one@synthetic.example'")
                )
                .scalar_one()
                .isoformat()
                == "2026-10-31"
            )
        with core.source.begin() as connection:
            connection.execute(text("UPDATE students SET joined='01/11/2026' WHERE sid=3"))
            connection.execute(
                text("UPDATE students SET email='four@synthetic.example' WHERE sid=4")
            )
            connection.execute(text("UPDATE enrollments SET sid=3 WHERE eid=3"))
            connection.execute(text("UPDATE payments SET amount='49.50' WHERE pid=3"))
        assert core.api.post(full + "/retry", headers=headers).status_code == 200
        assert core.poll(full, headers)["state"] == "completed"
        with core.destination.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM students")).scalar_one() == 4
            assert connection.execute(
                text("SELECT sum(total) FROM payments")
            ).scalar_one() == Decimal("350.00")
        verified = core.api.post(full + "/reconcile", headers=headers)
        assert verified.status_code == 201
        assert verified.json()["result"]["consistent"]
        with core.destination.begin() as connection:
            connection.execute(
                text(
                    "UPDATE students SET given_name='Deliberate discrepancy' "
                    "WHERE email='one@synthetic.example'"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO students(id,given_name,email,joined_on) "
                    "VALUES(:id,'Student Two','unexpected@synthetic.example','2026-11-01')"
                ),
                {"id": uuid4()},
            )
            connection.execute(text("DELETE FROM payments WHERE total=49.50"))
        discrepancy = core.api.post(full + "/reconcile", headers=headers)
        assert discrepancy.status_code == 201
        assert discrepancy.json()["result"]["discrepancies"]["mismatched"] == 1
        assert discrepancy.json()["result"]["discrepancies"]["missing"] == 1
        assert discrepancy.json()["result"]["discrepancies"]["unexpected"] == 1
        assert discrepancy.json()["result"]["discrepancies"]["duplicates"] == 1
        rid = discrepancy.json()["id"]
        assert core.api.get(path + "/reports/" + rid, headers=headers).status_code == 200
        csv_report = core.api.get(path + "/reports/" + rid + "/csv", headers=headers)
        assert csv_report.status_code == 200
        assert "mismatched" in csv_report.text
        assert (
            len(core.api.get(path + "/plans/" + pid + "/crosswalks", headers=headers).json()) == 14
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            deliveries = core.api.get(
                path + "/webhooks/" + hook_id + "/deliveries", headers=headers
            ).json()
            failures = core.api.get(
                path + "/webhooks/" + failed_id + "/deliveries", headers=headers
            ).json()
            if (
                deliveries
                and all(item["state"] == "delivered" for item in deliveries)
                and any(item["state"] == "failed" for item in failures)
            ):
                break
            time.sleep(0.5)
        else:
            pytest.fail("Webhook outbox did not deliver/retry within bounded deadline")
        assert any(item["attempts"] > 1 for item in deliveries)
        retried = next(item for item in deliveries if item["attempts"] > 1)
        attempts = core.api.get(
            path + "/webhooks/" + hook_id + "/deliveries/" + retried["id"] + "/attempts",
            headers=headers,
        )
        assert attempts.status_code == 200
        assert len(attempts.json()) == retried["attempts"]
        assert attempts.json()[0]["status_code"] == 503
        assert attempts.json()[-1]["status_code"] == 204
        assert all(item["finished_at"] for item in attempts.json())
        assert received and all(valid for event, valid in received)
        assert {event for event, valid in received if valid} >= {
            "migration.created",
            "migration.started",
            "migration.progress",
            "migration.completed",
            "reconciliation.completed",
        }
        assert core.api.get(full, headers=headers).json()["state"] == "completed"
        assert core.api.get("/ready").status_code == 200
    finally:
        receiver.shutdown()
        receiver.server_close()
        thread.join(timeout=5)


def simple_schema(env: Environment, count: int) -> list[dict[str, object]]:
    with env.source.begin() as connection:
        connection.execute(
            text("CREATE TABLE students(sid integer PRIMARY KEY, legacy_name text, unused text)")
        )
        connection.execute(
            text(
                "INSERT INTO students SELECT i, 'Synthetic '||i, 'explicitly ignored' "
                "FROM generate_series(1,:n) i"
            ),
            {"n": count},
        )
    with env.destination.begin() as connection:
        connection.execute(text("CREATE TABLE students(id uuid PRIMARY KEY, name text)"))
        connection.execute(
            text(
                "CREATE FUNCTION slow_owned_insert() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN PERFORM pg_sleep(0.005); RETURN NEW; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER slow_insert BEFORE INSERT ON students "
                "FOR EACH ROW EXECUTE FUNCTION slow_owned_insert()"
            )
        )
    return [
        {
            "source_table": "students",
            "destination_table": "students",
            "source_key": "sid",
            "destination_key": "id",
            "ignored_source_columns": ["unused"],
            "fields": {"id": field("sid", "uuid"), "name": field("legacy_name")},
        }
    ]


def test_worker_termination_recovery_connection_loss_idempotency_and_concurrency(
    core: Environment,
) -> None:
    mappings = simple_schema(core, 1000)
    path, headers = bootstrap(core)
    pid = plan(core, path, headers, snapshots(core, path, headers), mappings)
    token = uuid4().hex
    body = {"plan_id": pid, "mode": "full", "batch_size": 17}
    request_headers = {**headers, "Idempotency-Key": token}
    first = core.api.post(path + "/jobs", headers=request_headers, json=body)
    assert first.status_code == 202
    repeated = core.api.post(path + "/jobs", headers=request_headers, json=body)
    assert repeated.status_code == 202
    assert repeated.json()["id"] == first.json()["id"]
    assert (
        core.api.post(
            path + "/jobs",
            headers=request_headers,
            json={
                **body,
                "mode": "dry_run",
            },
        ).status_code
        == 409
    )
    job_path = path + "/jobs/" + first.json()["id"]
    worker = core.worker()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = core.api.get(job_path, headers=headers).json()
        if current["counters"].get("inserted", 0) >= 10:
            break
        time.sleep(0.1)
    else:
        pytest.fail("Worker made no durable progress")
    worker.terminate()
    worker.wait(timeout=10)
    checkpoint = core.api.get(job_path, headers=headers).json()["checkpoint"]
    assert checkpoint.get("last_key") is not None
    core.worker()
    core.worker()
    # Refuse new target connections and terminate only sessions in this owned database.
    base = make_url(core.settings.database_url.get_secret_value()).difference_update_query(
        ["options"]
    )
    with psycopg.connect(
        base.set(drivername="postgresql").render_as_string(hide_password=False), autocommit=True
    ) as admin:
        admin.execute(
            sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS false").format(
                sql.Identifier(core.names[1])
            )
        )
        pids = admin.execute(
            "SELECT pid FROM pg_stat_activity WHERE datname=%s", (core.names[1],)
        ).fetchall()
        for (pid_value,) in pids:
            admin.execute("SELECT pg_terminate_backend(%s)", (pid_value,))
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                current = core.api.get(job_path, headers=headers).json()
                if current["error_code"] == "connection_unavailable":
                    break
                time.sleep(0.1)
            else:
                pytest.fail("Destination loss was not persisted")
        finally:
            admin.execute(
                sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS true").format(
                    sql.Identifier(core.names[1])
                )
            )
    core.destination.dispose()
    result = core.poll(job_path, headers)
    assert result["state"] == "completed"
    assert result["retries"] >= 1
    assert sum(result["counters"].values()) == 1000
    with core.destination.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM students")).scalar_one() == 1000
        assert (
            connection.execute(text("SELECT count(DISTINCT id) FROM students")).scalar_one() == 1000
        )
    replay = submit(core, path, headers, pid, "full")
    assert core.poll(replay, headers)["state"] == "completed"
    with core.destination.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM students")).scalar_one() == 1000


@pytest.mark.parametrize("count", [0, 100])
def test_empty_dataset_or_cancellation_resume(core: Environment, count: int) -> None:
    mappings = simple_schema(core, count)
    path, headers = bootstrap(core)
    pid = plan(core, path, headers, snapshots(core, path, headers), mappings)
    queued = submit(core, path, headers, pid, "full")
    assert core.api.post(queued + "/cancel", headers=headers).json()["state"] == "cancelled"
    assert core.api.post(queued + "/retry", headers=headers).status_code == 200
    core.worker()
    if count:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            current = core.api.get(queued, headers=headers).json()
            if current["counters"].get("inserted", 0) >= 1:
                break
            time.sleep(0.05)
        assert core.api.post(queued + "/cancel", headers=headers).status_code == 200
        assert core.poll(queued, headers)["state"] == "cancelled"
        assert core.api.post(queued + "/retry", headers=headers).status_code == 200
    assert core.poll(queued, headers)["state"] == "completed"
    with core.destination.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM students")).scalar_one() == count


def test_sdk_migration_and_public_authorization_guards(core: Environment) -> None:
    mappings = simple_schema(core, 2)
    path, headers = bootstrap(core)
    ids = snapshots(core, path, headers)
    with ModelBridge(
        str(core.api.base_url),
        token=headers["Authorization"].removeprefix("Bearer "),
        workspace_id=path.rsplit("/", 1)[1],
    ) as sdk:
        assert sdk.compare(ids[0], ids[1])["source_snapshot_id"] == ids[0]
        created = sdk.create_plan(
            PlanInput.model_validate(
                {
                    "source_snapshot_id": ids[0],
                    "destination_snapshot_id": ids[1],
                    "tables": mappings,
                }
            )
        )
        assert (
            core.api.post(
                path + "/jobs",
                headers={**headers, "Idempotency-Key": uuid4().hex},
                json={"plan_id": created.id},
            ).status_code
            == 409
        )
        sdk.validate_plan(created.id)
        sdk.approve_plan(created.id)
        readonly = core.api.post(
            path + "/keys",
            headers=headers,
            json={
                "name": "Read only",
                "scopes": ["workspace:read", "migrations:read", "webhooks:read"],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        assert readonly.status_code == 201
        read_headers = {"Authorization": "Bearer " + readonly.json()["api_key"]}
        assert (
            core.api.post(
                path + "/jobs",
                headers={**read_headers, "Idempotency-Key": uuid4().hex},
                json={"plan_id": created.id},
            ).status_code
            == 404
        )
        assert (
            core.api.post(
                path + "/webhooks",
                headers=read_headers,
                json={"url": "http://127.0.0.1:1", "subscriptions": ["migration.created"]},
            ).status_code
            == 404
        )
        core.worker()
        job = sdk.create_job(JobInput(plan_id=created.id, mode="full"), uuid4().hex)
        assert sdk.wait(job.id, timeout=30, interval=0.15).state == "completed"
        report = sdk.reconcile(job.id)
        assert report.result["consistent"]
        other_path, _ = bootstrap(core)
        for resource in (
            "/plans/" + created.id,
            "/jobs/" + job.id,
            "/jobs/" + job.id + "/records",
            "/plans/" + created.id + "/crosswalks",
            "/reports/" + report.id,
        ):
            assert core.api.get(other_path + resource, headers=headers).status_code == 404
        with core.destination.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM students")).scalar_one() == 2
        with core.destination.begin() as connection:
            connection.execute(
                text("UPDATE students SET name='Conflict preserved' WHERE name='Synthetic 1'")
            )
        skip_specification = created.definition.model_copy(deep=True)
        skip_specification.tables[0].conflict = "skip"
        skip_plan = sdk.create_plan(skip_specification)
        sdk.validate_plan(skip_plan.id)
        sdk.approve_plan(skip_plan.id)
        skipped = sdk.create_job(JobInput(plan_id=skip_plan.id, mode="full"), uuid4().hex)
        assert sdk.wait(skipped.id, timeout=30, interval=0.15).state == "completed_with_errors"
        assert sum(item.status == "skipped" for item in sdk.outcomes(skipped.id)) == 1
        with core.destination.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM students WHERE name='Conflict preserved'")
                ).scalar_one()
                == 1
            )
        connections = core.api.get(path + "/connections", headers=headers).json()["items"]
        destination = next(item for item in connections if item["name"] == "destination")
        metadata_url = make_url(core.settings.database_url.get_secret_value())
        changed = core.api.patch(
            path + "/connections/" + destination["id"],
            headers=headers,
            json={
                "name": "destination",
                "host": "127.0.0.1",
                "port": metadata_url.port,
                "database": core.names[1],
                "username": metadata_url.username,
                "password": secrets.token_urlsafe(32),
                "sslmode": "disable",
            },
        )
        assert changed.status_code == 200
        rejected = sdk.create_job(JobInput(plan_id=created.id, mode="full"), uuid4().hex)
        refused = sdk.wait(rejected.id, timeout=30, interval=0.15)
        assert refused.state == "failed"
        # Binding validation, not a connection attempt with the changed password.
        assert refused.error_code == "execution_failed"
        with core.destination.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM students")).scalar_one() == 2
