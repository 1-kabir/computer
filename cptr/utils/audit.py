"""File-based HTTP audit logging middleware."""

from __future__ import annotations

import json
import re
import uuid
from enum import Enum
from typing import Any, Awaitable, Callable

from starlette.requests import Request

from cptr.env import AUDIT_LOG_LEVEL, AUDIT_MAX_BODY_SIZE
from cptr.utils.logger import audit_logger


class AuditLevel(str, Enum):
    NONE = "NONE"
    METADATA = "METADATA"
    REQUEST = "REQUEST"
    REQUEST_RESPONSE = "REQUEST_RESPONSE"


_SENSITIVE_KEYS = {
    "password",
    "api_key",
    "token",
    "authorization",
    "cookie",
    "secret",
    "device_code",
    "user_code",
    "verification_uri",
    "verification_uri_complete",
}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "********" if k.lower() in _SENSITIVE_KEYS else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _decode_body(body: bytearray) -> str | None:
    if not body:
        return None
    text = body.decode("utf-8", errors="replace")
    try:
        return json.dumps(_redact(json.loads(text)), ensure_ascii=False, default=str)
    except Exception:
        for key in _SENSITIVE_KEYS:
            text = re.sub(
                rf'("{re.escape(key)}"\s*:\s*")[^"]*(")',
                r"\1********\2",
                text,
                flags=re.IGNORECASE,
            )
        return text


class AuditContext:
    def __init__(self, max_body_size: int) -> None:
        self.request_body = bytearray()
        self.response_body = bytearray()
        self.max_body_size = max_body_size
        self.status_code: int | None = None

    def add_request(self, chunk: bytes) -> None:
        self._add(self.request_body, chunk)

    def add_response(self, chunk: bytes) -> None:
        self._add(self.response_body, chunk)

    def _add(self, target: bytearray, chunk: bytes) -> None:
        if not chunk or len(target) >= self.max_body_size:
            return
        target.extend(chunk[: self.max_body_size - len(target)])


class AuditLoggingMiddleware:
    """Capture safe HTTP audit entries to the audit Loguru sink."""

    AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(
        self,
        app,
        *,
        audit_level: AuditLevel,
        excluded_paths: list[str] | None = None,
        max_body_size: int = AUDIT_MAX_BODY_SIZE,
    ) -> None:
        self.app = app
        self.audit_level = audit_level
        self.excluded_paths = excluded_paths or []
        self.max_body_size = max_body_size

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        if self._should_skip(request):
            await self.app(scope, receive, send)
            return

        context = AuditContext(self.max_body_size)

        async def receive_wrapper() -> dict[str, Any]:
            message = await receive()
            if self.audit_level in (AuditLevel.REQUEST, AuditLevel.REQUEST_RESPONSE):
                if message.get("type") == "http.request":
                    context.add_request(message.get("body", b""))
            return message

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                context.status_code = message.get("status")
            elif (
                self.audit_level == AuditLevel.REQUEST_RESPONSE
                and message.get("type") == "http.response.body"
            ):
                context.add_response(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        finally:
            self._write_entry(request, context)

    def _should_skip(self, request: Request) -> bool:
        if AUDIT_LOG_LEVEL == "NONE" or request.method not in self.AUDITED_METHODS:
            return True
        path = request.url.path
        return any(path.startswith(excluded) for excluded in self.excluded_paths)

    def _write_entry(self, request: Request, context: AuditContext) -> None:
        auth = getattr(request.state, "auth", None)
        user = {}
        if auth is not None:
            user = {
                "id": getattr(auth, "user_id", None),
                "username": getattr(auth, "username", None),
                "role": getattr(auth, "role", None),
            }

        audit_logger().bind(
            id=str(uuid.uuid4()),
            audit_level=self.audit_level.value,
            user=user,
            method=request.method,
            path=request.url.path,
            status_code=context.status_code,
            source_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            request_body=_decode_body(context.request_body)
            if self.audit_level in (AuditLevel.REQUEST, AuditLevel.REQUEST_RESPONSE)
            else None,
            response_body=_decode_body(context.response_body)
            if self.audit_level == AuditLevel.REQUEST_RESPONSE
            else None,
        ).info("")


def get_audit_level() -> AuditLevel:
    try:
        return AuditLevel(AUDIT_LOG_LEVEL)
    except ValueError:
        return AuditLevel.NONE
