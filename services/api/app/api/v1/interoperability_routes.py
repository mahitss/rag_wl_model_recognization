"""M80: Educational Standards & Learning Interoperability API routes.

Endpoints:

* POST   /api/v1/interoperability/xapi/statements          - Store an xAPI statement
* GET    /api/v1/interoperability/xapi/statements          - List xAPI statements
* POST   /api/v1/interoperability/scorm/packages           - Import a SCORM package
* GET    /api/v1/interoperability/scorm/packages           - List SCORM packages
* POST   /api/v1/interoperability/scorm/sessions           - Record SCORM runtime
* GET    /api/v1/interoperability/scorm/sessions           - List SCORM sessions
* POST   /api/v1/interoperability/lti/launch               - Validate & record LTI launch
* GET    /api/v1/interoperability/lti/launches             - List LTI launch audits
* GET    /api/v1/interoperability/audit                    - List interoperability audit
* GET    /api/v1/interoperability/standards                - Supported standards matrix

All endpoints require authentication and tenant-scoped access.
"""
# ruff: noqa: B008,E501

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, File, Query, Request, UploadFile

from app.auth import AuthUser, require_auth
from app.config import Settings
from app.errors import ApiError
from app.interoperability import (
    LTI_VERSION,
    SCORM12_VERSION,
    SCORM2004_VERSION,
    XAPI_VERSION,
    import_scorm12_package,
    list_xapi_statements_adapter,
    record_lti13_launch,
    record_scorm12_runtime,
    store_xapi_statement_adapter,
    validate_lti13_launch_shape,
)
from app.schemas import (
    InteroperabilityAuditRecord,
    InteroperabilityImportResult,
    LTILaunchResponse,
    SCORMPackageListResponse,
    SCORMSessionCreate,
    SCORMSessionListResponse,
    SCORMSessionResponse,
    XAPIStatementCreate,
    XAPIStatementListResponse,
    XAPIStatementResponse,
)
from app.storage import (
    list_interoperability_audit,
    list_lti_launches,
    list_scorm_packages,
    list_scorm_sessions,
    store_interoperability_audit,
)

router = APIRouter(prefix="/api/v1/interoperability", tags=["interoperability"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _tenant_id(database_path, user_id: str) -> str:
    import hashlib

    return f"tenant_{hashlib.sha256(user_id.encode()).hexdigest()[:24]}"


def _audit(
    database_path,
    *,
    user_id: str,
    tenant_id: str,
    operation: str,
    standard: str,
    target_id: str | None = None,
    success: bool = False,
    error_code: str | None = None,
    details: dict | None = None,
) -> None:
    store_interoperability_audit(
        database_path,
        user_id=user_id,
        tenant_id=tenant_id,
        operation=operation,
        standard=standard,
        target_id=target_id,
        success=success,
        error_code=error_code,
        details=details,
    )


# ---------------------------------------------------------------------------
# Standards matrix
# ---------------------------------------------------------------------------


@router.get("/standards", response_model=dict[str, Any])
def interoperability_standards(
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the supported standards matrix with honest limitations."""
    return {
        "standards": [
            {
                "standard": "xAPI (Experience API)",
                "version": XAPI_VERSION,
                "supported": True,
                "capabilities": [
                    "store_statement",
                    "list_statements",
                    "validate_statement",
                ],
                "limitations": [
                    "Statement attachments are recorded as URLs only; binary "
                    "attachments are not stored.",
                ],
            },
            {
                "standard": "SCORM 1.2",
                "version": SCORM12_VERSION,
                "supported": True,
                "capabilities": [
                    "import_package",
                    "validate_manifest",
                    "record_runtime",
                ],
                "limitations": [
                    "Full SCORM RTE API run-time is not implemented; only "
                    "cmi.core and cmi.suspend_data fields are mapped.",
                    "Content packages are not executed in a sandboxed API "
                    "wrapper.",
                ],
            },
            {
                "standard": "SCORM 2004",
                "version": SCORM2004_VERSION,
                "supported": False,
                "capabilities": [],
                "limitations": [
                    "Sequencing and navigation model not implemented.",
                    "Data model differences vs SCORM 1.2 not mapped.",
                    "No conformance testing performed.",
                ],
            },
            {
                "standard": "LTI 1.3",
                "version": LTI_VERSION,
                "supported": False,
                "capabilities": ["validate_launch_shape", "record_launch_audit"],
                "limitations": [
                    "End-to-end LTI launch requires live consumer credentials "
                    "and an external OIDC issuer; not configured.",
                    "JWT signature verification is not performed.",
                ],
            },
        ],
        "note": "Supported subsets only. No conformance claims are made beyond "
        "the capabilities listed above.",
    }


# ---------------------------------------------------------------------------
# xAPI
# ---------------------------------------------------------------------------


@router.post(
    "/xapi/statements",
    response_model=XAPIStatementResponse,
    status_code=201,
)
def create_xapi_statement_route(
    request: Request,
    payload: XAPIStatementCreate = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> XAPIStatementResponse:
    """Store a validated xAPI learning statement."""
    database_path = _database_path(request)
    return store_xapi_statement_adapter(
        database_path,
        user_id=current_user.user_id,
        statement=payload,
        settings=_settings(request),
    )


@router.get("/xapi/statements", response_model=XAPIStatementListResponse)
def list_xapi_statements_route(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    verb_id: str | None = Query(default=None),
    current_user: AuthUser = Depends(require_auth),
) -> XAPIStatementListResponse:
    """List xAPI statements for the authenticated user."""
    database_path = _database_path(request)
    return list_xapi_statements_adapter(
        database_path,
        user_id=current_user.user_id,
        limit=limit,
        offset=offset,
        verb_id=verb_id,
    )


# ---------------------------------------------------------------------------
# SCORM
# ---------------------------------------------------------------------------


@router.post(
    "/scorm/packages",
    response_model=InteroperabilityImportResult,
    status_code=201,
)
def import_scorm_package_route(
    request: Request,
    file: UploadFile = File(...),
    display_name: str = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> InteroperabilityImportResult:
    """Import a SCORM 1.2 package archive (.zip or .imscc)."""
    database_path = _database_path(request)
    tenant_id = _tenant_id(database_path, current_user.user_id)

    if not file.filename:
        raise ApiError(400, "MISSING_FILENAME", "Archive filename is required")
    lower = file.filename.lower()
    if not any(lower.endswith(ext) for ext in (".zip", ".imscc")):
        raise ApiError(
            400,
            "UNSUPPORTED_ARCHIVE",
            "Archive must be a .zip or .imscc file",
        )

    contents = file.file.read()
    result = import_scorm12_package(
        database_path,
        user_id=current_user.user_id,
        display_name=display_name,
        archive_bytes=contents,
        settings=_settings(request),
    )

    _audit(
        database_path,
        user_id=current_user.user_id,
        tenant_id=tenant_id,
        operation="import_package",
        standard="SCORM 1.2",
        target_id=result.packageId,
        success=result.status == "imported",
        error_code=None if result.status == "imported" else "IMPORT_FAILED",
        details={"status": result.status, "errors": result.errors},
    )
    return result


@router.get("/scorm/packages", response_model=SCORMPackageListResponse)
def list_scorm_packages_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> SCORMPackageListResponse:
    """List imported SCORM packages for the authenticated user."""
    database_path = _database_path(request)
    rows = list_scorm_packages(database_path, user_id=current_user.user_id)
    return SCORMPackageListResponse(packages=rows)


@router.post(
    "/scorm/sessions",
    response_model=SCORMSessionResponse,
    status_code=201,
)
def record_scorm_session_route(
    request: Request,
    payload: SCORMSessionCreate = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> SCORMSessionResponse:
    """Record SCORM 1.2 cmi.runtime learner-session data."""
    database_path = _database_path(request)
    return record_scorm12_runtime(
        database_path,
        user_id=current_user.user_id,
        package_id=payload.packageId,
        learner_id=payload.learnerId,
        lesson_status=payload.lessonStatus,
        score_raw=payload.scoreRaw,
        score_min=payload.scoreMin,
        score_max=payload.scoreMax,
        total_time_ms=payload.totalTimeMs,
        lesson_location=payload.lessonLocation,
        suspend_data=payload.suspendData,
    )


@router.get("/scorm/sessions", response_model=SCORMSessionListResponse)
def list_scorm_sessions_route(
    request: Request,
    package_id: str | None = Query(default=None),
    current_user: AuthUser = Depends(require_auth),
) -> SCORMSessionListResponse:
    """List SCORM learner sessions for the authenticated user."""
    database_path = _database_path(request)
    rows = list_scorm_sessions(
        database_path, user_id=current_user.user_id, package_id=package_id
    )
    return SCORMSessionListResponse(sessions=rows)


# ---------------------------------------------------------------------------
# LTI 1.3
# ---------------------------------------------------------------------------


@router.post(
    "/lti/launch",
    response_model=LTILaunchResponse,
    status_code=201,
)
def lti13_launch_route(
    request: Request,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> LTILaunchResponse:
    """Validate the shape of an LTI 1.3 launch request and record an audit
    entry. No cryptographic verification is performed (see /standards)."""
    database_path = _database_path(request)
    tenant_id = _tenant_id(database_path, current_user.user_id)

    validation = validate_lti13_launch_shape(payload)
    stored = record_lti13_launch(
        database_path,
        user_id=current_user.user_id,
        tenant_id=tenant_id,
        launch_params=payload,
        audit={
            "validation": {
                "valid": validation.valid,
                "warnings": validation.warnings,
                "errors": validation.errors,
            }
        },
    )

    _audit(
        database_path,
        user_id=current_user.user_id,
        tenant_id=tenant_id,
        operation="record_launch",
        standard="LTI 1.3",
        target_id=stored.launchId,
        success=validation.valid,
        error_code=None if validation.valid else "LAUNCH_VALIDATION_FAILED",
        details={"errors": validation.errors},
    )
    return stored


@router.get("/lti/launches", response_model=list[LTILaunchResponse])
def list_lti_launches_route(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    current_user: AuthUser = Depends(require_auth),
) -> list[LTILaunchResponse]:
    """List LTI launch audit entries for the authenticated user."""
    database_path = _database_path(request)
    rows = list_lti_launches(
        database_path, user_id=current_user.user_id, limit=limit, offset=offset
    )
    return [LTILaunchResponse(**row) for row in rows]


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@router.get("/audit", response_model=list[InteroperabilityAuditRecord])
def list_interoperability_audit_route(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    standard: str | None = Query(default=None),
    current_user: AuthUser = Depends(require_auth),
) -> list[InteroperabilityAuditRecord]:
    """List interoperability audit records for the authenticated user."""
    database_path = _database_path(request)
    rows = list_interoperability_audit(
        database_path,
        user_id=current_user.user_id,
        limit=limit,
        offset=offset,
        standard=standard,
    )
    return [InteroperabilityAuditRecord(**row) for row in rows]
