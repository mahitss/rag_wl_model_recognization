"""M74: API v2 router.

Provides backward-compatible API versioning.  The v1 router continues to
serve all existing endpoints unchanged under /api/v1/.  The v2 router serves
the same platform routes under /api/v2/ so clients can migrate at their own
pace.  Future breaking changes can be introduced in v2 without affecting v1.

The v2 router re-declares the core platform endpoints (version, health) with
identical response contracts.  Additional v2 endpoints can be added here as
the API evolves.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api_platform import api_health, api_version_info

router = APIRouter(prefix="/api/v2/platform", tags=["platform-v2"])


@router.get("/version", response_model=dict)
def get_api_version_v2() -> dict:
    """Return API version information (v2)."""
    return api_version_info()


@router.get("/health", response_model=dict)
def get_api_health_v2() -> dict:
    """Return API health status (v2)."""
    return api_health()


__all__ = ["router"]
