"""Security middleware for the WonderLearn API.

Provides three independent concerns:

1. **Rate limiting** — fixed-window counter per client IP and request path.
   Protects authentication, uploads, and expensive endpoints. Enabled by
   default; production startup rejects disabling it.

2. **Security headers** — defensive browser headers on every response
   (X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy).
   Always applied.

3. **Max request body size** — enforces ``WL_MAX_REQUEST_BODY_BYTES`` using
   both declared length and streamed-byte accounting.

All components are dependency-free and appropriate for a single-process,
₹0-budget deployment. Rate-limit state is in-memory per application and resets on restart;
for multi-instance deployments, place a rate-limiting reverse proxy in front.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Awaitable, Callable
from hmac import compare_digest
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.auth import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

# ---------------------------------------------------------------------------
# Security headers (always applied)
# ---------------------------------------------------------------------------

SECURITY_HEADERS: dict[str, str] = {
    # Prevent MIME-type sniffing.
    "X-Content-Type-Options": "nosniff",
    # Deny framing to mitigate clickjacking.
    "X-Frame-Options": "DENY",
    # Limit referrer information sent to external sites.
    "Referrer-Policy": "same-origin",
    # Disable browser features the app does not use.
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    # Advertise no inline script execution (defensive; no unsafe-inline is used).
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none';",
}

# ---------------------------------------------------------------------------
# Rate limiting (fixed window per IP + path)
# ---------------------------------------------------------------------------

_RATE_LIMIT_WINDOW_SECONDS = 60


def _client_key(request: Request) -> str:
    client = request.client
    host = client.host if client else "unknown"
    return f"{host}:{request.url.path}"


def _rate_limit_allowed(
    key: str,
    *,
    limit: int,
    records: dict[str, list[float]],
    lock: threading.Lock,
) -> bool:
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW_SECONDS
    with lock:
        if len(records) > 2_048:
            expired = [
                record_key
                for record_key, values in records.items()
                if not values or values[-1] <= window_start
            ]
            for record_key in expired:
                del records[record_key]
        timestamps = records.get(key, [])
        timestamps = [t for t in timestamps if t > window_start]
        if len(timestamps) >= limit:
            records[key] = timestamps
            return False
        timestamps.append(now)
        records[key] = timestamps
        return True


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add defensive security headers to every response."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window rate limiter keyed by client IP and request path."""

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool,
        requests_per_minute: int,
        auth_requests_per_minute: int = 10,
        expensive_requests_per_minute: int = 10,
        exempt_paths: frozenset[str],
    ) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.requests_per_minute = max(1, requests_per_minute)
        self.auth_requests_per_minute = max(1, auth_requests_per_minute)
        self.expensive_requests_per_minute = max(1, expensive_requests_per_minute)
        self.exempt_paths = exempt_paths
        self._records: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self.enabled or request.url.path in self.exempt_paths:
            return await call_next(request)

        key = _client_key(request)
        path = request.url.path
        if path.startswith("/api/v1/auth/"):
            limit = self.auth_requests_per_minute
        elif (
            path in {"/api/v1/documents", "/api/v1/images"}
            or path
            in {
                "/api/v1/embeddings/rebuild",
                "/api/v1/knowledge-graph/extract",
                "/api/v1/quizzes",
            }
            or path.endswith("/tutor")
            or path.endswith("/explanation")
        ):
            limit = self.expensive_requests_per_minute
        else:
            limit = self.requests_per_minute
        if not _rate_limit_allowed(key, limit=limit, records=self._records, lock=self._lock):
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests. Try again later.",
                        "requestId": None,
                        "details": None,
                    }
                },
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW_SECONDS)},
            )
        return await call_next(request)


class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    """Require a double-submit CSRF token for every state-changing API call."""

    _SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if (
            request.url.path.startswith("/api/v1/")
            and request.method.upper() not in self._SAFE_METHODS
        ):
            cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
            header_token = request.headers.get(CSRF_HEADER_NAME)
            if (
                not cookie_token
                or not header_token
                or not compare_digest(cookie_token, header_token)
            ):
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "CSRF_VALIDATION_FAILED",
                            "message": "The request could not be validated.",
                            "requestId": getattr(request.state, "request_id", None),
                            "details": None,
                        }
                    },
                )
        return await call_next(request)


class MaxBodySizeMiddleware:
    """Reject requests whose Content-Length exceeds the configured limit."""

    def __init__(self, app: Any, *, max_bytes: int | None) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if self.max_bytes is None or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = next(
            (value for key, value in scope["headers"] if key.lower() == b"content-length"),
            None,
        )
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                await self._reject(send, 400, "INVALID_CONTENT_LENGTH")
                return
            if length > self.max_bytes:
                await self._reject(send, 413, "REQUEST_ENTITY_TOO_LARGE")
                return

        received_bytes = 0
        response_started = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_bytes:
                    raise _RequestBodyTooLarge
            return message

        async def track_response(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, track_response)
        except _RequestBodyTooLarge:
            if not response_started:
                await self._reject(send, 413, "REQUEST_ENTITY_TOO_LARGE")

    async def _reject(self, send: Any, status_code: int, code: str) -> None:
        body = (
            b'{"error":{"code":"INVALID_CONTENT_LENGTH",'
            b'"message":"The request could not be processed.","requestId":null,"details":null}}'
            if status_code == 400
            else b'{"error":{"code":"REQUEST_ENTITY_TOO_LARGE",'
            b'"message":"The request body exceeds the configured limit.",'
            b'"requestId":null,"details":null}}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _RequestBodyTooLarge(Exception):
    pass
