"""Owned-process TCP smoke against configured disposable metadata, no secrets printed."""

import argparse
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url

from modelbridge.application.authentication import AuthenticationService
from modelbridge.application.workspaces import WorkspaceService, token_hash
from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.database import Database, create_metadata_engine, require_schema
from modelbridge.sdk import ModelBridge


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--browser", action="store_true", help="Run live Playwright dashboard checks"
    )
    parser.add_argument(
        "--api-product", action="store_true", help="Verify account SDK contract over real TCP"
    )
    args = parser.parse_args()
    settings = Settings()
    if args.api_product and not settings.registration_enabled:
        raise SystemExit("API product smoke requires explicit registration opt-in")
    if settings.environment != "test":
        raise SystemExit("Smoke requires MODELBRIDGE_ENVIRONMENT=test and disposable metadata")
    url = make_url(settings.database_url.get_secret_value())
    if (
        url.host not in {"127.0.0.1", "localhost"}
        or not url.database
        or not url.database.startswith("modelbridge_test_")
    ):
        raise SystemExit("Smoke requires a loopback modelbridge_test_* database")
    engine = create_metadata_engine(settings)
    require_schema(engine)
    workspace, token = WorkspaceService(Database(engine)).bootstrap("Smoke " + uuid4().hex)
    environment = dict(os.environ)
    environment["MODELBRIDGE_WORKER_ID"] = "smoke-" + uuid4().hex[:16]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    headers = {"Authorization": f"Bearer {token}"}
    root = Path(__file__).resolve().parents[1]
    processes: list[subprocess.Popen[bytes]] = []
    product_token: SecretStr | None = None
    product_workspace: str | None = None
    try:
        with tempfile.TemporaryFile() as logs:
            worker = subprocess.Popen(
                [sys.executable, "-m", "modelbridge.worker"],
                cwd=root,
                env=environment,
                stdout=logs,
                stderr=logs,
            )
            processes.append(worker)
            for iteration in range(2):
                api = subprocess.Popen(  # noqa: S603 -- fixed interpreter/module; port is locally allocated
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
                    cwd=root,
                    env=environment,
                    stdout=logs,
                    stderr=logs,
                )
                processes.append(api)
                deadline = time.monotonic() + 30
                with httpx.Client(base_url=base_url, timeout=3) as client:
                    while time.monotonic() < deadline:
                        if api.poll() is not None or worker.poll() is not None:
                            raise RuntimeError("Owned test process exited during startup")
                        try:
                            if client.get("/ready").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.25)
                    else:
                        raise RuntimeError("Readiness deadline exceeded")
                    assert client.get("/health").status_code == 200
                    assert client.get("/api/v1/workspaces").status_code == 401
                    actual = client.get("/api/v1/workspaces", headers=headers)
                    assert actual.status_code == 200
                    assert actual.json()[0]["id"] == workspace.id
                    assert token not in actual.text
                    assert len(client.get("/api/v1/audit", headers=headers).json()) == 1
                    if args.api_product:
                        with ModelBridge(base_url) as sdk:
                            if iteration == 0:
                                password = SecretStr(secrets.token_urlsafe(32))
                                email = uuid4().hex + "@synthetic.example"
                                code = AuthenticationService(
                                    Database(engine), settings
                                ).issue_registration_code(email)
                                sdk.register(email, password, SecretStr(code))
                                sdk.login(email, password)
                                selected = sdk.create_workspace("TCP SDK " + uuid4().hex)
                                sdk.workspace_id = selected.id
                                issued = sdk.create_key(
                                    "TCP SDK reader",
                                    ["workspace:read"],
                                    datetime.now(UTC) + timedelta(hours=1),
                                )
                                product_token = issued.secret
                                product_workspace = selected.id
                                sdk.logout()
                        with ModelBridge(base_url, token=product_token) as reader:
                            assert reader.workspaces()[0].id == product_workspace
                        assert client.get("/api/guides/API_QUICKSTART.md").status_code == 200
                        print(f"Account/scoped-key SDK TCP lifecycle {iteration + 1}: passed")
                    if args.browser and iteration == 1:
                        environment["MODELBRIDGE_API_URL"] = base_url
                        environment["MODELBRIDGE_E2E_TOKEN"] = token
                        environment["MODELBRIDGE_E2E_WORKSPACE_NAME"] = workspace.name
                        npm = "npm.cmd" if os.name == "nt" else "npm"
                        subprocess.run(  # noqa: S603 -- fixed package script and owned environment
                            [npm, "run", "test:e2e"],
                            cwd=root / "frontend",
                            env=environment,
                            check=True,
                            timeout=120,
                        )
                api.terminate()
                api.wait(timeout=10)
                print(f"TCP API lifecycle {iteration + 1}: passed")
            with engine.connect() as connection:
                row = connection.execute(
                    text("SELECT token_hash FROM access_credentials WHERE workspace_id=:id"),
                    {"id": workspace.id},
                ).scalar_one()
                assert row == token_hash(token)
            print("Independent SQL: persistent workspace credential hash verified")
            print("Foundation TCP smoke passed; no data migration exercised")
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        engine.dispose()


if __name__ == "__main__":
    main()
