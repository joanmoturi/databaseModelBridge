"""Generate local development secrets without printing or overwriting them."""

import base64
import os
import secrets
from pathlib import Path


def write_configuration(target: Path) -> None:
    password = secrets.token_urlsafe(32)
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    content = (
        f"MODELBRIDGE_DB_PASSWORD={password}\n"
        f"MODELBRIDGE_DATABASE_URL=postgresql+psycopg://modelbridge:{password}"
        "@127.0.0.1:5432/modelbridge\n"
        f"MODELBRIDGE_ENCRYPTION_KEYS={key}\n"
        "MODELBRIDGE_ENVIRONMENT=development\n"
        'MODELBRIDGE_CORS_ORIGINS=["http://localhost:3000"]\n'
        "MODELBRIDGE_WORKER_ID=worker-1\n"
        "MODELBRIDGE_HEARTBEAT_SECONDS=5\n"
        "MODELBRIDGE_WORKER_STALE_SECONDS=30\n"
        "MODELBRIDGE_REGISTRATION_ENABLED=false\n"
        "MODELBRIDGE_ALLOWED_DATABASE_NETWORKS=[]\n"
        "MODELBRIDGE_ALLOWED_WEBHOOK_NETWORKS=[]\n"
        "MODELBRIDGE_MAX_BATCH_SIZE=100\n"
        "MODELBRIDGE_MAX_ACTIVE_JOBS_PER_WORKSPACE=2\n"
        "MODELBRIDGE_JOB_RETRY_LIMIT=3\n"
        "MODELBRIDGE_API_URL=http://127.0.0.1:8000\n"
    )
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit("Refusing to overwrite existing .env") from None
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
        file.write(content)
    print("Created .env with new local secrets. Keep it private and out of Git.")


def main() -> None:
    write_configuration(Path(__file__).resolve().parents[1] / ".env")


if __name__ == "__main__":
    main()
