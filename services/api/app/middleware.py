"""Request-ID and structured access-log middleware.

Every request gets a stable `X-Request-ID` (from the inbound header or a new
UUID).  The ID is attached to `request.state.request_id` so route handlers and
error responses can echo it.  Access logs are emitted as structured JSON and
never include request bodies, uploaded file bytes, secret-like query values,
or camera frames.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.logging import get_logger
from app.observability import OperationalMetrics

_LOGGER = get_logger("wonderlearn.access")

_SENSITIVE_QUERY_KEYS = re.compile(
    r"(token|key|secret|password|passwd|api[_ ]?key|authorization|credential)",
    re.IGNORECASE,
)

_REQUEST_ID_HEADER = "x-request-id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign/echo a request ID and log each request/response pair."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id

        start = time.perf_counter()
        response: Response | None = None
        status_code = 500
        error_category: str | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[_REQUEST_ID_HEADER] = request_id
            return response
        except Exception:
            error_category = "unhandled_exception"
            _LOGGER.exception(
                "unhandled_exception",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            if response is None:
                response = Response(status_code=status_code)
                response.headers[_REQUEST_ID_HEADER] = request_id
            metrics: OperationalMetrics = request.app.state.metrics
            metrics.record_request(
                method=request.method,
                route=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
                error_category=error_category,
            )
            _LOGGER.info(
                "request_handled",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 2),
                    "query": self._redact_query(request.url.query),
                },
            )

    @staticmethod
    def _redact_query(query: str) -> str:
        if not query:
            return ""
        parts = []
        for part in query.split("&"):
            if "=" not in part:
                parts.append(part)
                continue
            key, _, value = part.partition("=")
            if _SENSITIVE_QUERY_KEYS.search(key):
                parts.append(f"{key}=[REDACTED]")
            else:
                parts.append(part)
        return "&".join(parts)
