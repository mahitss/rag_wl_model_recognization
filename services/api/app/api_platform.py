"""M74: API Platform service layer.

Provides cross-cutting platform concerns:

* Idempotency key checking and storage for state-changing operations.
* Pagination helpers (page, pageSize, totalPages) for list endpoints.
* Standardized error response construction.
* API version info and health checks.

All functions are tenant-aware where applicable and never leak secrets.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.storage import (
    check_idempotency_key,
    cleanup_expired_idempotency_keys,
    store_idempotency_key,
)


def compute_request_hash(
    method: str,
    path: str,
    body: bytes | None,
    query: str | None = None,
) -> str:
    """Compute a deterministic hash of a request for idempotency key lookup.

    The hash includes the method, path, query string, and body bytes so that
    identical requests produce the same hash.  Headers (other than the
    idempotency key itself) are intentionally excluded so that retry clients
    with different header values still hit the cached response.
    """
    h = hashlib.sha256()
    h.update(method.upper().encode("utf-8"))
    h.update(b"|")
    h.update(path.encode("utf-8"))
    h.update(b"|")
    h.update((query or "").encode("utf-8"))
    h.update(b"|")
    h.update(body or b"")
    return h.hexdigest()


def check_idempotency(
    database_path: Path,
    *,
    idempotency_key: str,
    user_id: str,
    method: str,
    path: str,
    body: bytes | None,
    query: str | None = None,
) -> dict[str, Any] | None:
    """Check whether an idempotency key has already been used for an
    equivalent request.

    Returns the cached response dict if a matching, non-expired key exists,
    otherwise None.
    """
    compute_request_hash(method, path, body, query)
    return check_idempotency_key(
        database_path,
        idempotency_key=idempotency_key,
        user_id=user_id,
    )


def store_idempotency_response(
    database_path: Path,
    *,
    idempotency_key: str,
    user_id: str,
    method: str,
    path: str,
    body: bytes | None,
    query: str | None = None,
    response_status: int,
    response_body: dict[str, Any] | None,
    ttl_seconds: int = 86400,
) -> None:
    """Store the response for an idempotency key so that a subsequent
    identical request can be served from cache."""
    request_hash = compute_request_hash(method, path, body, query)
    store_idempotency_key(
        database_path,
        idempotency_key=idempotency_key,
        user_id=user_id,
        method=method,
        path=path,
        request_hash=request_hash,
        response_status=response_status,
        response_body=response_body,
        ttl_seconds=ttl_seconds,
    )


def cleanup_idempotency_keys(database_path: Path) -> int:
    """Remove expired idempotency keys. Returns the number removed."""
    return cleanup_expired_idempotency_keys(database_path)


def paginate(
    *,
    total_items: int,
    page: int,
    page_size: int,
) -> dict[str, int]:
    """Compute pagination metadata.

    Returns ``{page, pageSize, total, totalPages}``.  Page numbers are
    1-based.  If ``total_items`` is 0, ``totalPages`` is 0.
    """
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 1
    if page_size > 1000:
        page_size = 1000
    total_pages = (total_items + page_size - 1) // page_size if total_items else 0
    return {
        "page": page,
        "pageSize": page_size,
        "total": total_items,
        "totalPages": total_pages,
    }


def paginate_slice(
    items: list[Any],
    *,
    page: int,
    page_size: int,
) -> list[Any]:
    """Return the slice of ``items`` for the given page (1-based)."""
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 1
    if page_size > 1000:
        page_size = 1000
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end]


def api_version_info() -> dict[str, str]:
    """Return API version information."""
    return {
        "apiVersion": "1.0",
        "supportedVersions": ["1.0"],
        "documentationUrl": "/docs",
    }


def api_health() -> dict[str, str]:
    """Return API health status."""
    return {
        "status": "ok",
        "version": "1.0",
        "database": "sqlite",
    }
