"""Typed client for implemented public APIs; no automatic retries of mutations."""

import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Self

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from modelbridge.contracts import (
    AccountResponse,
    ConnectionInput,
    ConnectionPage,
    ConnectionResponse,
    ConnectionTestResponse,
    KeyCreated,
    KeyResponse,
    SessionResponse,
    SnapshotPage,
    SnapshotResponse,
    WorkspaceResponse,
)
from modelbridge.migration_contracts import (
    JobInput,
    JobResponse,
    OutcomeResponse,
    PlanInput,
    PlanResponse,
    ReportResponse,
)


class ApiError(Exception):
    def __init__(self, status: int, code: str, request_id: str | None) -> None:
        super().__init__(f"ModelBridge API error: {status} {code}")
        self.status, self.code, self.request_id = status, code, request_id


class TransportError(Exception):
    pass


class ContractError(Exception):
    pass


@dataclass(frozen=True)
class IssuedKey:
    metadata: KeyResponse
    secret: SecretStr


@dataclass(frozen=True)
class AccountSession:
    token: SecretStr
    expires_at: datetime


def decode[T: BaseModel](model: type[T], value: object) -> T:
    try:
        return model.model_validate(value)
    except ValidationError:
        raise ContractError("API response did not match the supported contract") from None


class ModelBridge:
    def __init__(
        self,
        base_url: str,
        token: str | SecretStr | None = None,
        workspace_id: str | None = None,
        timeout: float = 10,
        get_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        url = httpx.URL(base_url)
        if url.username or url.password or url.scheme not in {"http", "https"}:
            raise ValueError("API URL must be HTTP(S) without embedded credentials")
        if url.scheme == "http" and url.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Remote API URLs require HTTPS")
        if not 0 < timeout <= 120 or not 0 <= get_retries <= 5:
            raise ValueError("Invalid timeout or retry limit")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )
        self._token = token if isinstance(token, SecretStr) else SecretStr(token or "")
        self.workspace_id = workspace_id
        self.get_retries = get_retries

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        body: object = None,
        extra_headers: dict[str, str] | None = None,
    ) -> object:
        headers = (
            {"Authorization": "Bearer " + self._token.get_secret_value()}
            if self._token.get_secret_value()
            else {}
        )
        headers.update(extra_headers or {})
        attempts = self.get_retries + 1 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self._client.request(method, path, json=body, headers=headers)
            except httpx.TransportError:
                if attempt + 1 < attempts:
                    time.sleep(0.25 * 2**attempt)
                    continue
                raise TransportError("API transport failed; no mutation was retried") from None
            if response.status_code in {429, 502, 503, 504} and attempt + 1 < attempts:
                retry = response.headers.get("Retry-After", "1")
                time.sleep(min(float(retry), 60) if retry.isdigit() else 1)
                continue
            if response.status_code >= 300:
                code = "api_error"
                try:
                    payload = response.json()
                    candidate = payload.get("error", {}).get("code", "")
                    if isinstance(candidate, str) and re.fullmatch(r"[a-z0-9_]{1,64}", candidate):
                        code = candidate
                except (ValueError, AttributeError):
                    pass
                request_id = response.headers.get("X-Request-ID")
                if request_id is not None and not re.fullmatch(r"[a-f0-9-]{36}", request_id):
                    request_id = None
                raise ApiError(response.status_code, code, request_id)
            if response.status_code == 204:
                return None
            try:
                value: object = response.json()
                return value
            except ValueError:
                raise ContractError("API response was not JSON") from None
        raise TransportError("API transport failed")

    def _workspace(self) -> str:
        if self.workspace_id is None:
            raise ValueError("Select a workspace_id first")
        return "/api/v1/workspaces/" + self.workspace_id

    def register(
        self, email: str, password: SecretStr, registration_code: SecretStr
    ) -> AccountResponse:
        return decode(
            AccountResponse,
            self._request(
                "POST",
                "/api/v1/auth/register",
                {
                    "email": email,
                    "password": password.get_secret_value(),
                    "registration_code": registration_code.get_secret_value(),
                },
            ),
        )

    def login(self, email: str, password: SecretStr) -> AccountSession:
        result = decode(
            SessionResponse,
            self._request(
                "POST",
                "/api/v1/auth/login",
                {
                    "email": email,
                    "password": password.get_secret_value(),
                },
            ),
        )
        self._token = SecretStr(result.access_token)
        return AccountSession(self._token, result.expires_at)

    def logout(self) -> None:
        self._request("POST", "/api/v1/auth/logout")
        self._token = SecretStr("")

    def workspaces(self) -> list[WorkspaceResponse]:
        result = self._request("GET", "/api/v1/workspaces")
        if not isinstance(result, list):
            raise ContractError("Expected a workspace list")
        return [decode(WorkspaceResponse, item) for item in result]

    def create_workspace(self, name: str) -> WorkspaceResponse:
        return decode(
            WorkspaceResponse, self._request("POST", "/api/v1/workspaces", {"name": name})
        )

    def create_key(self, name: str, scopes: list[str], expires_at: datetime) -> IssuedKey:
        response = decode(
            KeyCreated,
            self._request(
                "POST",
                self._workspace() + "/keys",
                {
                    "name": name,
                    "scopes": scopes,
                    "expires_at": expires_at.isoformat(),
                },
            ),
        )
        metadata = decode(KeyResponse, response.model_dump(exclude={"api_key"}))
        return IssuedKey(metadata, SecretStr(response.api_key))

    def keys(self) -> list[KeyResponse]:
        result = self._request("GET", self._workspace() + "/keys")
        if not isinstance(result, list):
            raise ContractError("Expected an API key list")
        return [decode(KeyResponse, item) for item in result]

    def revoke_key(self, key_id: str) -> None:
        self._request("DELETE", self._workspace() + "/keys/" + key_id)

    def connections(self, offset: int = 0, limit: int = 50) -> ConnectionPage:
        return decode(
            ConnectionPage,
            self._request("GET", self._workspace() + f"/connections?offset={offset}&limit={limit}"),
        )

    def save_connection(
        self,
        configuration: ConnectionInput,
        connection_id: str | None = None,
    ) -> ConnectionResponse:
        payload = configuration.model_dump(mode="json", exclude={"password"})
        if configuration.password is not None:
            payload["password"] = configuration.password.get_secret_value()
        path = self._workspace() + "/connections"
        if connection_id is not None:
            path += "/" + connection_id
        return decode(
            ConnectionResponse,
            self._request("PATCH" if connection_id is not None else "POST", path, payload),
        )

    def delete_connection(self, connection_id: str) -> None:
        self._request("DELETE", self._workspace() + "/connections/" + connection_id)

    def test_connection(self, connection_id: str) -> ConnectionTestResponse:
        return decode(
            ConnectionTestResponse,
            self._request("POST", self._workspace() + "/connections/" + connection_id + "/test"),
        )

    def discover(self, connection_id: str, schema_name: str = "public") -> SnapshotResponse:
        return decode(
            SnapshotResponse,
            self._request(
                "POST",
                self._workspace() + "/connections/" + connection_id + "/discover",
                {"schema_name": schema_name},
            ),
        )

    def snapshots(self, offset: int = 0, limit: int = 50) -> SnapshotPage:
        return decode(
            SnapshotPage,
            self._request("GET", self._workspace() + f"/snapshots?offset={offset}&limit={limit}"),
        )

    def snapshot(self, snapshot_id: str) -> SnapshotResponse:
        return decode(
            SnapshotResponse, self._request("GET", self._workspace() + "/snapshots/" + snapshot_id)
        )

    def compare(self, source_snapshot_id: str, destination_snapshot_id: str) -> dict[str, object]:
        value = self._request(
            "POST",
            self._workspace() + "/schemas/compare",
            {
                "source_snapshot_id": source_snapshot_id,
                "destination_snapshot_id": destination_snapshot_id,
            },
        )
        if not isinstance(value, dict):
            raise ContractError("Expected a schema comparison")
        return {str(key): item for key, item in value.items()}

    def create_plan(self, specification: PlanInput) -> PlanResponse:
        return decode(
            PlanResponse,
            self._request(
                "POST", self._workspace() + "/plans", specification.model_dump(mode="json")
            ),
        )

    def approve_plan(self, plan_id: str) -> PlanResponse:
        return decode(
            PlanResponse,
            self._request("POST", self._workspace() + "/plans/" + plan_id + "/approve"),
        )

    def validate_plan(self, plan_id: str) -> dict[str, object]:
        value = self._request("POST", self._workspace() + "/plans/" + plan_id + "/validate")
        if not isinstance(value, dict):
            raise ContractError("Expected a plan validation")
        return {str(key): item for key, item in value.items()}

    def create_job(self, specification: JobInput, idempotency_key: str) -> JobResponse:
        return decode(
            JobResponse,
            self._request(
                "POST",
                self._workspace() + "/jobs",
                specification.model_dump(mode="json"),
                {"Idempotency-Key": idempotency_key},
            ),
        )

    def job(self, job_id: str) -> JobResponse:
        return decode(JobResponse, self._request("GET", self._workspace() + "/jobs/" + job_id))

    def wait(self, job_id: str, timeout: float = 120, interval: float = 2) -> JobResponse:
        if timeout <= 0 or interval < 0.1:
            raise ValueError("Invalid polling timeout or interval")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.job(job_id)
            if job.state in {"completed", "completed_with_errors", "cancelled", "failed"}:
                return job
            time.sleep(interval)
        raise TransportError(
            "Migration polling deadline exceeded; the durable job was not cancelled"
        )

    def cancel(self, job_id: str) -> JobResponse:
        return decode(
            JobResponse, self._request("POST", self._workspace() + "/jobs/" + job_id + "/cancel")
        )

    def retry(self, job_id: str) -> JobResponse:
        return decode(
            JobResponse, self._request("POST", self._workspace() + "/jobs/" + job_id + "/retry")
        )

    def reconcile(self, job_id: str) -> ReportResponse:
        return decode(
            ReportResponse,
            self._request("POST", self._workspace() + "/jobs/" + job_id + "/reconcile"),
        )

    def reports(self, job_id: str) -> list[ReportResponse]:
        value = self._request("GET", self._workspace() + "/jobs/" + job_id + "/reports")
        if not isinstance(value, list):
            raise ContractError("Expected reconciliation reports")
        return [decode(ReportResponse, item) for item in value]

    def outcomes(self, job_id: str, offset: int = 0, limit: int = 50) -> list[OutcomeResponse]:
        value = self._request(
            "GET", self._workspace() + f"/jobs/{job_id}/records?offset={offset}&limit={limit}"
        )
        if not isinstance(value, list):
            raise ContractError("Expected record outcomes")
        return [decode(OutcomeResponse, item) for item in value]
