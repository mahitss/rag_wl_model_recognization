"""AI Content Studio validation routes.

Runs the deterministic evidence-verification pipeline over a generated
content resource (citation provenance, claim support, schema validity) and
exposes human review of section verification status. Semantic-model checks,
when a local tutor is configured, are advisory estimates only and never
override the deterministic result.
"""
# ruff: noqa: B008,E501

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth import AuthUser, require_auth
from app.config import Settings
from app.content_validation import validate_resource
from app.errors import ApiError
from app.schemas import (
    ContentResourceSectionVerification,
    ContentResourceValidation,
    ContentSectionVerifyRequest,
)
from app.storage import (
    get_content_resource,
    get_latest_content_validation,
    verify_content_section,
)

router = APIRouter(prefix="/api/v1/content", tags=["content studio"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post(
    "/resources/{resource_id}/validate",
    response_model=ContentResourceValidation,
)
def validate_content_resource_route(
    request: Request,
    resource_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Run the evidence-verification pipeline over a content resource.

    Deterministic checks (citation provenance, claim support, schema
    validity) are the source of truth. If a local tutor is configured, an
    additional semantic-estimate pass is recorded as advisory only and can
    downgrade but never upgrade the deterministic result.
    """
    settings = _settings(request)
    try:
        result = validate_resource(
            _database_path(request),
            resource_id=resource_id,
            user_id=current_user.user_id,
            settings=settings,
        )
    except sqlite3.Error as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    return result


@router.get(
    "/resources/{resource_id}/validation",
    response_model=ContentResourceValidation,
)
def get_content_resource_validation_route(
    request: Request,
    resource_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the latest validation record for a resource. Owner only."""
    resource = get_content_resource(
        _database_path(request),
        resource_id=resource_id,
        user_id=current_user.user_id,
    )
    if resource is None:
        raise ApiError(404, "CONTENT_RESOURCE_NOT_FOUND", "Content resource not found or access denied.")
    record = get_latest_content_validation(
        _database_path(request),
        resource_id=resource_id,
        user_id=current_user.user_id,
    )
    if record is None:
        raise ApiError(
            404,
            "VALIDATION_NOT_FOUND",
            "No validation record exists for this resource.",
        )
    return record


@router.patch(
    "/resources/{resource_id}/sections/{section_id}/verify",
    response_model=ContentResourceSectionVerification,
)
def verify_content_section_route(
    request: Request,
    resource_id: str,
    section_id: str,
    payload: ContentSectionVerifyRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record a human review decision for a section.

    The learner (resource owner) marks a section verified / needs_review /
    insufficient_evidence. This is authoritative and overrides the
    deterministic result. Approved content is never silently overwritten:
    regeneration archives the previous version as a revision.
    """
    try:
        section = verify_content_section(
            _database_path(request),
            resource_id=resource_id,
            section_id=section_id,
            user_id=current_user.user_id,
            verification_status=payload.verificationStatus,
            verification_notes=payload.verificationNotes,
        )
    except ValueError as exc:
        raise ApiError(400, "INVALID_VERIFICATION", str(exc)) from exc
    except sqlite3.Error as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if section is None:
        raise ApiError(404, "CONTENT_SECTION_NOT_FOUND", "Section not found or access denied.")
    return section
