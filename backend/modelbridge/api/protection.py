import asyncio
import time
from collections.abc import Callable
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from modelbridge.application.limits import RateExceeded, consume_rate
from modelbridge.infrastructure.database import Database


class ResourceProtection:
    def __init__(
        self,
        app: ASGIApp,
        database: Callable[[], Database | None],
        max_bytes: int,
        anonymous_limit: int,
    ) -> None:
        self.app = app
        self.database = database
        self.max_bytes = max_bytes
        self.anonymous_limit = anonymous_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def reject(status: int, code: str, retry: bool = False) -> None:
            headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
            if retry:
                headers["Retry-After"] = str(60 - int(time.time()) % 60)
            response = JSONResponse(
                {
                    "error": {
                        "code": code,
                        "message": code.replace("_", " "),
                        "request_id": request_id,
                    }
                },
                status_code=status,
                headers=headers,
            )
            await response(scope, receive, send)

        if scope["path"] not in {"/health", "/ready", "/version"}:
            database = self.database()
            if database is not None:
                address = scope.get("client")
                ip = address[0] if address is not None else "unknown"
                try:
                    await asyncio.to_thread(
                        consume_rate, database, "ip:" + ip, self.anonymous_limit
                    )
                except RateExceeded:
                    await reject(429, "rate_limited", True)
                    return
                except SQLAlchemyError:
                    await reject(503, "metadata_unavailable")
                    return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                await reject(413, "payload_too_large")
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
