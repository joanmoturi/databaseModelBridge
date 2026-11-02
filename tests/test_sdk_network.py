import asyncio
from unittest.mock import patch

import httpx
import pytest
from starlette.types import Message

from modelbridge.api.guides import GUIDES, read_guide
from modelbridge.api.protection import ResourceProtection
from modelbridge.application.connections import ConnectionUnavailable, allowed_address
from modelbridge.sdk import ApiError, ContractError, ModelBridge


def test_all_packaged_guide_sources_exist_and_paths_are_allowlisted() -> None:
    from fastapi import HTTPException

    for guide in GUIDES:
        assert read_guide(guide).startswith("#")
    with pytest.raises(HTTPException) as caught:
        read_guide("../../../.env")
    assert caught.value.status_code == 404


def test_network_policy_pins_allowed_addresses_and_rejects_rebinding() -> None:
    answer = [(2, 1, 6, "", ("127.0.0.1", 5432))]
    with patch("socket.getaddrinfo", return_value=answer):
        assert allowed_address("synthetic.example", 5432, ["127.0.0.1/32"]) == "127.0.0.1"
    mixed = answer + [(2, 1, 6, "", ("169.254.169.254", 5432))]
    with patch("socket.getaddrinfo", return_value=mixed):
        with pytest.raises(ConnectionUnavailable):
            allowed_address("synthetic.example", 5432, ["0.0.0.0/0"])
    with patch(
        "socket.getaddrinfo", return_value=[(10, 1, 6, "", ("::ffff:169.254.169.254", 5432))]
    ):
        with pytest.raises(ConnectionUnavailable):
            allowed_address("synthetic.example", 5432, ["::/0"])


def test_request_limit_counts_streamed_chunks_before_app_execution() -> None:
    async def scenario() -> None:
        called = False
        sent: list[Message] = []
        chunks = iter(
            [
                {"type": "http.request", "body": b"a" * 600, "more_body": True},
                {"type": "http.request", "body": b"b" * 600, "more_body": False},
            ]
        )

        async def receive() -> Message:
            return next(chunks)

        async def send(message: Message) -> None:
            sent.append(message)

        async def app(scope: object, receive: object, send: object) -> None:
            nonlocal called
            called = True

        middleware = ResourceProtection(app, lambda: None, 1024, 30)
        await middleware(
            {
                "type": "http",
                "path": "/api/v1/auth/login",
                "headers": [(b"content-length", b"1")],
            },
            receive,
            send,
        )
        assert not called
        assert sent[0]["status"] == 413

    asyncio.run(scenario())


def test_sdk_retries_only_get_and_masks_api_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(503, json={"error": {"code": "unavailable"}})

    monkeypatch.setattr("modelbridge.sdk.time.sleep", lambda delay: None)
    with ModelBridge("http://127.0.0.1", transport=httpx.MockTransport(handler)) as sdk:
        with pytest.raises(ApiError):
            sdk.workspaces()
        assert calls == ["GET", "GET", "GET"]
        calls.clear()
        with pytest.raises(ApiError):
            sdk.create_workspace("Synthetic")
        assert calls == ["POST"]


def test_sdk_requires_remote_tls_and_valid_contracts() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        ModelBridge("http://api.example.org")
    with pytest.raises(ValueError, match="credentials"):
        ModelBridge("https://user:password@api.example.org")
    with ModelBridge(
        "http://127.0.0.1",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    ) as sdk:
        with pytest.raises(ContractError):
            sdk.workspaces()
