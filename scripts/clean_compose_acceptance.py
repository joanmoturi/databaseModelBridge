"""Verify a clean local working-tree snapshot without committing or publishing it."""

import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


def command(arguments: list[str], cwd: Path, timeout: int = 120) -> str:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("MODELBRIDGE_")
        or key in {"MODELBRIDGE_API_BIND_PORT", "MODELBRIDGE_DASHBOARD_BIND_PORT"}
    }
    environment.pop("BUILDX_BUILDER", None)
    environment.pop("BUILDKIT_HOST", None)
    result = subprocess.run(  # noqa: S603 -- fixed tools; no shell or interpolated secrets
        arguments, cwd=cwd, env=environment, capture_output=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError("Command failed; output withheld to avoid leaking deployment secrets")
    return result.stdout.decode("utf-8").strip()


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def request(url: str, token: str | None = None) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError("Acceptance requests must use explicit loopback HTTP")
    headers = {"Authorization": "Bearer " + token} if token else {}
    connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=5)
    try:
        connection.request("GET", parsed.path, headers=headers)
        response = connection.getresponse()
        if response.status != 200:
            raise OSError("Unexpected application response")
        return response.read()
    finally:
        connection.close()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    docker = shutil.which("docker")
    if docker is None and os.name == "nt":
        installed = (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Docker"
            / "Docker"
            / "resources"
            / "bin"
            / "docker.exe"
        )
        if installed.is_file():
            docker = str(installed)
    if docker is None:
        print("BLOCKED: Docker CLI is unavailable")
        return 2
    git = shutil.which("git")
    if git is None:
        print("BLOCKED: Git CLI is unavailable")
        return 2
    stage = "fresh clone"
    project = "mbaccept-" + uuid4().hex[:12]
    started = False
    with tempfile.TemporaryDirectory(prefix="modelbridge-clean-") as directory:
        clone = Path(directory) / "repository"
        compose: list[str] = []
        try:
            command(
                [
                    git,
                    "clone",
                    "--quiet",
                    "--no-hardlinks",
                    "--no-checkout",
                    str(root),
                    str(clone),
                ],
                root,
            )
            # Include unpublished fixes, excluding ignored secrets/dependencies and .git.
            files = (
                subprocess.run(  # noqa: S603 -- fixed read-only git command
                    [git, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                    cwd=root,
                    capture_output=True,
                    check=True,
                )
                .stdout.decode("utf-8")
                .split("\0")
            )
            for name in filter(None, files):
                source = root / name
                if not source.exists():
                    continue
                source.resolve().relative_to(root.resolve())
                if source.is_symlink() or source.name == ".env":
                    raise RuntimeError("Unsafe snapshot entry")
                destination = clone / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            print("PASS: fresh clone with exact unpublished, non-ignored working files")
            stage = "documented configuration"
            command([sys.executable, str(clone / "scripts" / "configure.py")], clone)
            api_port, dashboard_port = free_port(), free_port()
            compose = [
                docker,
                "compose",
                "--project-name",
                project,
                "--file",
                str(clone / "docker-compose.yml"),
            ]
            # Port settings apply to this process/its children, not the developer's .env.
            os.environ["MODELBRIDGE_API_BIND_PORT"] = str(api_port)
            os.environ["MODELBRIDGE_DASHBOARD_BIND_PORT"] = str(dashboard_port)
            command(compose + ["config", "--quiet"], clone)
            print("PASS: fresh generated configuration and Compose validation")
            stage = "local Docker daemon"
            endpoint = os.environ.get("DOCKER_HOST") or command(
                [docker, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], clone
            )
            if not endpoint.startswith(("npipe://", "unix://")):
                raise RuntimeError("Only a local named-pipe or Unix-socket daemon is allowed")
            version = command([docker, "info", "--format", "{{.ServerVersion}}"], clone)
            if not version:
                raise RuntimeError("Docker did not return a running server version")
            stage = "uncached image build"
            command(compose + ["build", "--builder", "default", "--no-cache"], clone, timeout=1800)
            print("PASS: uncached application and dashboard image builds")
            stage = "empty-volume startup"
            started = True
            command(
                compose + ["up", "--detach", "--wait", "--wait-timeout", "180"], clone, timeout=240
            )
            base = f"http://127.0.0.1:{api_port}"
            deadline = time.monotonic() + 40
            while True:
                try:
                    request(base + "/ready")
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Readiness deadline exceeded") from None
                    time.sleep(1)
            request(base + "/health")
            request(f"http://127.0.0.1:{dashboard_port}")
            request(f"http://127.0.0.1:{dashboard_port}/bridge/health")
            print("PASS: API, worker, current metadata schema, dashboard and proxy")
            stage = "metadata persistence"
            issued = command(
                compose
                + ["exec", "-T", "api", "modelbridge", "bootstrap", "--name", "Clean acceptance"],
                clone,
            )
            token = issued.splitlines()[-1]
            before = json.loads(request(base + "/api/v1/workspaces", token))
            digest = hashlib.sha256(token.encode()).hexdigest()
            count = command(
                compose
                + [
                    "exec",
                    "-T",
                    "metadata",
                    "psql",
                    "-U",
                    "modelbridge",
                    "-d",
                    "modelbridge",
                    "-tAc",
                    "SELECT token_hash FROM access_credentials",
                ],
                clone,
            )
            if count != digest:
                raise RuntimeError("Independent credential persistence check failed")
            command(compose + ["restart", "metadata", "api", "worker"], clone)
            deadline = time.monotonic() + 40
            while True:
                try:
                    request(base + "/ready")
                    after = json.loads(request(base + "/api/v1/workspaces", token))
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Restart readiness deadline exceeded") from None
                    time.sleep(1)
            if before != after:
                raise RuntimeError("Workspace changed across restart")
            print("PASS: independently queried metadata and restart persistence")
            print("PASS: clean Compose foundation; run public migration acceptance separately")
            return 0
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            print(f"BLOCKED/FAILED: {stage}; no secrets or command output printed")
            return 2
        finally:
            if started:
                try:
                    command(compose + ["down", "--volumes", "--remove-orphans"], clone)
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    print(f"WARNING: owned Compose project {project} needs cleanup")


if __name__ == "__main__":
    raise SystemExit(main())
