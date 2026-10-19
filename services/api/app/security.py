"""Security middleware for the WonderLearn API.

Provides three independent concerns:

1. **Rate limiting** — fixed-window counter per client IP and request path.
   Protects expensive endpoints (embedding rebuild, document upload, retrieval)
   from denial-of-service abuse.  Disabled by default; enable with
   ``WL_RATE_LIMIT_ENABLED=true``.

2. **Security headers** — defensive browser headers on every response
   (X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy).
   Always applied.

3. **Max request body size** — rejects requests whose declared
   ``Content-Length`` exceeds ``WL_MAX_REQUEST_BODY_BYTES`` before the body
   is read, preventing resource exhaustion on upload endpoints.

All components are dependency-free and appropriate for a single-process,
₹0-budget deployment.  Rate-limit state is in-memory and resets on restart;
for multi-instance deployments, place a rate-limiting reverse proxy in front.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

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
_RATE_LIMIT_RECORD: dict[str, list[float]] = {}
_RATE_LIMIT_LOCK = None  # set lazily below

import threading  # noqa: E402

_RATE_LIMIT_LOCK = threading.Lock()


def _client_key(request: Request) -> str:
    client = request.client
    host = client.host if client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        host = forwarded.split(",")[0].strip()
    return f"{host}:{request.url.path}"


def _rate_limit_allowed(key: str, *, limit: int) -> bool:
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW_SECONDS
    with _RATE_LIMIT_LOCK:
        timestamps = _RATE_LIMIT_RECORD.get(key, [])
        timestamps = [t for t in timestamps if t > window_start]
        if len(timestamps) >= limit:
            _RATE_LIMIT_RECORD[key] = timestamps
            return False
        timestamps.append(now)
        _RATE_LIMIT_RECORD[key] = timestamps
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
        exempt_paths: frozenset[str],
    ) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.requests_per_minute = max(1, requests_per_minute)
        self.exempt_paths = exempt_paths

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self.enabled or request.url.path in self.exempt_paths:
            return await call_next(request)

        key = _client_key(request)
        if not _rate_limit_allowed(key, limit=self.requests_per_minute):
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


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds the configured limit."""

    def __init__(
        self,
        app: Any,
        *,
        max_bytes: int | None,
    ) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if self.max_bytes is None:
            return await call_next(request)
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                length = 0
            if length > self.max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "REQUEST_ENTITY_TOO_LARGE",
                            "message": "The request body exceeds the configured limit.",
                            "requestId": None,
                            "details": None,
                        }
                    },
                )
        return await call_next(request)
