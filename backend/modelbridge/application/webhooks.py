import hashlib
import hmac
import http.client
import json
import secrets
import socket
import ssl
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from modelbridge.application.connections import ConnectionUnavailable, allowed_address
from modelbridge.domain.access import AccessDenied, ResourceConflict
from modelbridge.infrastructure.config import Settings
from modelbridge.infrastructure.crypto import CredentialCipher
from modelbridge.infrastructure.database import Database
from modelbridge.infrastructure.models import (
    LifecycleEvent,
    MigrationJob,
    Webhook,
    WebhookAttempt,
    WebhookDelivery,
    Workspace,
)
from modelbridge.migration_contracts import WebhookInput


def emit(session: Session, job: MigrationJob, event_type: str) -> None:
    now = datetime.now(UTC)
    event = LifecycleEvent(
        workspace_id=job.workspace_id,
        job_id=job.id,
        event_type=event_type,
        payload={"job_id": job.id, "state": job.state, "counters": dict(job.counters)},
        created_at=now,
    )
    session.add(event)
    session.flush()
    hooks = session.scalars(
        select(Webhook).where(Webhook.workspace_id == job.workspace_id, Webhook.enabled.is_(True))
    )
    for hook in hooks:
        if event_type in hook.subscriptions:
            session.add(
                WebhookDelivery(
                    webhook_id=hook.id,
                    event_id=event.id,
                    next_attempt_at=now,
                )
            )


def validate_url(url: str, settings: Settings) -> tuple[str, int, str]:
    parsed = urlsplit(url)
    if (
        parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or parsed.scheme not in {"http", "https"}
        or any(char in url for char in "\r\n\t")
    ):
        raise ValueError("Use an HTTP(S) webhook URL without credentials, query or fragment")
    if parsed.scheme != "https" and not (
        settings.environment != "production"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise ValueError("Webhooks require HTTPS except explicit development loopback")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    address = allowed_address(parsed.hostname, port, settings.allowed_webhook_networks)
    return parsed.hostname, port, address


class WebhookService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database, self.settings = database, settings
        self.cipher = CredentialCipher(settings.encryption_keys.get_secret_value())

    def create(self, workspace_id: str, body: WebhookInput) -> tuple[Webhook, str]:
        validate_url(body.url, self.settings)
        secret = secrets.token_urlsafe(32)
        with self.database.transaction() as session:
            session.get(Workspace, workspace_id, with_for_update=True)
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(Webhook)
                    .where(Webhook.workspace_id == workspace_id, Webhook.enabled.is_(True))
                )
                or 0
            )
            if count >= 10:
                raise ResourceConflict
            hook = Webhook(
                workspace_id=workspace_id,
                url=body.url,
                subscriptions=list(body.subscriptions),
                secret_envelope=self.cipher.encrypt(secret),
                created_at=datetime.now(UTC),
            )
            session.add(hook)
            session.flush()
            return hook, secret

    def get(self, workspace_id: str, hook_id: str) -> Webhook:
        with self.database.transaction() as session:
            hook = session.get(Webhook, hook_id)
            if hook is None or hook.workspace_id != workspace_id:
                raise AccessDenied
            return hook

    def delete(self, workspace_id: str, hook_id: str) -> None:
        self.get(workspace_id, hook_id)
        with self.database.transaction() as session:
            hook = session.get(Webhook, hook_id, with_for_update=True)
            if hook is not None:
                hook.enabled, hook.secret_envelope = False, ""

    def send(self, hook: Webhook, event: LifecycleEvent) -> int:
        host, port, address = validate_url(hook.url, self.settings)
        parsed = urlsplit(hook.url)
        body = json.dumps(
            {
                "id": event.id,
                "type": event.event_type,
                "workspace_id": event.workspace_id,
                "created_at": event.created_at.isoformat(),
                "data": event.payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        timestamp = str(int(time.time()))
        secret = self.cipher.decrypt(hook.secret_envelope)
        signature = hmac.new(
            secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        connection = http.client.HTTPConnection(host, port, timeout=5)
        try:
            connection.sock = socket.create_connection((address, port), timeout=5)
            if parsed.scheme == "https":
                connection.sock = ssl.create_default_context().wrap_socket(
                    connection.sock, server_hostname=host
                )
            connection.request(
                "POST",
                parsed.path or "/",
                body,
                {
                    "Content-Type": "application/json",
                    "X-ModelBridge-Signature": f"t={timestamp},v1={signature}",
                    "X-ModelBridge-Event-ID": event.id,
                },
            )
            response = connection.getresponse()
            response.read(1024)
            return response.status
        finally:
            connection.close()

    def deliver_one(self) -> bool:
        with self.database.transaction() as session:
            candidates = list(
                session.scalars(
                    select(WebhookDelivery.id)
                    .where(
                        WebhookDelivery.state == "pending",
                        WebhookDelivery.next_attempt_at <= datetime.now(UTC),
                    )
                    .order_by(WebhookDelivery.next_attempt_at)
                    .limit(20)
                )
            )
        for delivery_id in candidates:
            lock_id = int.from_bytes(
                hashlib.sha256(("delivery:" + delivery_id).encode()).digest()[:8]
            ) & (2**63 - 1)
            with self.database.engine.connect() as lock:
                if not lock.execute(
                    text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}
                ).scalar_one():
                    continue
                lock.commit()
                try:
                    self.attempt(delivery_id)
                finally:
                    lock.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
                    lock.commit()
            return True
        return False

    def attempt(self, delivery_id: str) -> None:
        with self.database.transaction() as session:
            delivery = session.get(WebhookDelivery, delivery_id, with_for_update=True)
            if delivery is None or delivery.state != "pending":
                return
            hook = session.get(Webhook, delivery.webhook_id)
            event = session.get(LifecycleEvent, delivery.event_id)
            if hook is None or not hook.enabled or event is None:
                delivery.state, delivery.error_code = "failed", "webhook_disabled"
                return
            now = datetime.now(UTC)
            for unfinished in session.scalars(
                select(WebhookAttempt).where(
                    WebhookAttempt.delivery_id == delivery_id,
                    WebhookAttempt.finished_at.is_(None),
                )
            ):
                unfinished.finished_at, unfinished.error_code = now, "worker_interrupted"
            # Commit attempt before I/O so crashes cannot create unbounded redeliveries.
            if delivery.attempts >= 5:
                delivery.state, delivery.error_code = "failed", "retry_limit"
                return
            delivery.attempts += 1
            attempt = WebhookAttempt(
                delivery_id=delivery_id, number=delivery.attempts, started_at=now
            )
            session.add(attempt)
            session.flush()
            attempt_id = attempt.id
        status: int | None = None
        error: str | None = None
        try:
            status = self.send(hook, event)
            if not 200 <= status < 300:
                error = "http_failure"
        except (OSError, ValueError, ConnectionUnavailable, http.client.HTTPException):
            error = "delivery_unavailable"
        with self.database.transaction() as session:
            current = session.get(WebhookDelivery, delivery_id, with_for_update=True)
            if current is None:
                return
            recorded = session.get(WebhookAttempt, attempt_id)
            if recorded is not None:
                recorded.finished_at = datetime.now(UTC)
                recorded.status_code, recorded.error_code = status, error
            current.status_code, current.error_code = status, error
            current.state = (
                "delivered" if error is None else "failed" if current.attempts >= 5 else "pending"
            )
            current.next_attempt_at = datetime.now(UTC) + timedelta(seconds=2**current.attempts)
