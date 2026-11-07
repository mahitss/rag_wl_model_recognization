"""M65: Learning Gap Intelligence & Remediation Engine API routes.

Endpoints:

* GET    /api/v1/learning-gaps                                      - List gaps
* POST   /api/v1/learning-gaps/detect                               - Run gap detection
* GET    /api/v1/learning-gaps/{gap_id}                             - Get gap details
* GET    /api/v1/learning-gaps/{gap_id}/evidence                    - Get gap evidence
* GET    /api/v1/learning-gaps/{gap_id}/root-cause                  - Root-cause analysis
* GET    /api/v1/learning-gaps/{gap_id}/remediations                - List remediations
* POST   /api/v1/learning-gaps/{gap_id}/remediations                - Generate remediations
* GET    /api/v1/learning-gaps/{gap_id}/reassessments               - List reassessments
* POST   /api/v1/learning-gaps/{gap_id}/reassessments               - Record reassessment
* POST   /api/v1/remediations/{remediation_id}/accept               - Accept remediation
* POST   /api/v1/remediations/{remediation_id}/dismiss              - Dismiss remediation
* POST   /api/v1/remediations/{remediation_id}/complete             - Complete remediation
* POST   /api/v1/learning-gaps/{gap_id}/dismiss                     - Dismiss gap
* GET    /api/v1/learning-gaps/dashboard                            - Improvement dashboard

All endpoints require authentication and gap ownership (where applicable).
"""
# ruff: noqa: B008,E501

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Path as PathParam, Query, Request

from app.auth import AuthUser, require_auth
from app.errors import ApiError
from app.gap_detection import (
    analyze_gap_root_cause,
    detect_gaps_for_user,
    get_gap_details,
    list_user_gaps,
)
from app.remediation import (
    accept_remediation_activity,
    complete_remediation_activity,
    dismiss_remediation_activity,
    generate_remediations_for_gap,
    get_remediation_dashboard,
    record_reassessment,
)
from app.storage import (
    add_gap_evidence,
    create_learning_gap,
    dismiss_learning_gap,
    get_learning_gap,
    list_gap_evidence,
)

router = APIRouter(prefix="/api/v1", tags=["learning-gaps", "remediation"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Gap listing and detection
# ---------------------------------------------------------------------------


@router.get("/learning-gaps", response_model=dict[str, Any])
def list_learning_gaps_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = Query(None),
    gap_type: str | None = Query(None),
    severity: str | None = Query(None),
) -> dict[str, Any]:
    """List learning gaps for the authenticated user."""
    from app.storage import list_learning_gaps

    gaps = list_learning_gaps(
        _database_path(request),
        user_id=current_user.user_id,
        status=status,
        gap_type=gap_type,
        severity=severity,
    )
    counts = {
        "total": len(gaps),
        "critical": sum(1 for g in gaps if g["severity"] == "critical"),
        "moderate": sum(1 for g in gaps if g["severity"] == "moderate"),
        "minor": sum(1 for g in gaps if g["severity"] == "minor"),
        "detected": sum(1 for g in gaps if g["status"] == "detected"),
        "acknowledged": sum(1 for g in gaps if g["status"] == "acknowledged"),
        "remediating": sum(1 for g in gaps if g["status"] == "remediating"),
        "resolved": sum(1 for g in gaps if g["status"] == "resolved"),
        "dismissed": sum(1 for g in gaps if g["status"] == "dismissed"),
    }
    return {"userId": current_user.user_id, "counts": counts, "gaps": gaps}


@router.post("/learning-gaps/detect", response_model=dict[str, Any])
def detect_learning_gaps_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Run gap detection for the authenticated user."""
    result = detect_gaps_for_user(
        database_path=_database_path(request),
        user_id=current_user.user_id,
        now=_now(),
    )
    return result


@router.get("/learning-gaps/{gap_id}", response_model=dict[str, Any])
def get_learning_gap_route(
    request: Request,
    gap_id: str = PathParam(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one learning gap if owned by the user."""
    gap = get_learning_gap(
        _database_path(request), gap_id=gap_id, user_id=current_user.user_id
    )
    if gap is None:
        raise ApiError(404, "GAP_NOT_FOUND", "Learning gap not found or access denied.")
    return gap


@router.get("/learning-gaps/{gap_id}/details", response_model=dict[str, Any])
def get_gap_details_route(
    request: Request,
    gap_id: str = PathParam(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return gap with evidence, remediations, and reassessments."""
    details = get_gap_details(
        database_path=_database_path(request),
        gap_id=gap_id,
        user_id=current_user.user_id,
    )
    if details is None:
        raise ApiError(404, "GAP_NOT_FOUND", "Learning gap not found or access denied.")
    return details


@router.get("/learning-gaps/{gap_id}/evidence", response_model=dict[str, Any])
def get_gap_evidence_route(
    request: Request,
    gap_id: str = PathParam(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List supporting evidence for a learning gap."""
    from app.storage import list_gap_evidence

    evidence = list_gap_evidence(
        _database_path(request), gap_id=gap_id, user_id=current_user.user_id
    )
    if evidence is None:
        raise ApiError(404, "GAP_NOT_FOUND", "Learning gap not found or access denied.")
    return {"gapId": gap_id, "evidence": evidence}


@router.post("/learning-gaps/{gap_id}/evidence", response_model=dict[str, Any], status_code=201)
def add_gap_evidence_route(
    request: Request,
    gap_id: str = PathParam(...),
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Add supporting evidence to a learning gap."""
    evidence = add_gap_evidence(
        _database_path(request),
        gap_id=gap_id,
        user_id=current_user.user_id,
        evidence_type=payload.get("evidenceType", ""),
        reference_id=payload.get("referenceId", ""),
        description=payload.get("description"),
        recorded_at=payload.get("recordedAt"),
    )
    if evidence is None:
        raise ApiError(
            422, "INVALID_REQUEST", "Invalid evidence or gap not found/access denied."
        )
    return evidence


@router.get("/learning-gaps/{gap_id}/root-cause", response_model=dict[str, Any])
def get_gap_root_cause_route(
    request: Request,
    gap_id: str = PathParam(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Perform root-cause analysis for a gap."""
    result = analyze_gap_root_cause(
        database_path=_database_path(request),
        gap_id=gap_id,
        user_id=current_user.user_id,
        now=_now(),
    )
    if "error" in result:
        raise ApiError(404, "GAP_NOT_FOUND", result["error"])
    return result


@router.patch("/learning-gaps/{gap_id}/root-cause", response_model=dict[str, Any])
def update_gap_root_cause_route(
    request: Request,
    gap_id: str = PathParam(...),
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record root-cause hypothesis/evidence for a gap (learner or expert input)."""
    from app.storage import update_learning_gap

    updated = update_learning_gap(
        _database_path(request),
        gap_id=gap_id,
        user_id=current_user.user_id,
        root_cause_hypothesis=payload.get("hypothesis"),
        root_cause_evidence=payload.get("evidence"),
    )
    if updated is None:
        raise ApiError(404, "GAP_NOT_FOUND", "Learning gap not found or access denied.")
    return updated


@router.post("/learning-gaps/{gap_id}/dismiss", response_model=dict[str, Any])
def dismiss_learning_gap_route(
    request: Request,
    gap_id: str = PathParam(...),
    payload: dict[str, Any] = Body(default={}),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Dismiss a learning gap. Owner only."""
    ok = dismiss_learning_gap(
        _database_path(request),
        gap_id=gap_id,
        user_id=current_user.user_id,
        reason=payload.get("reason"),
    )
    if not ok:
        raise ApiError(404, "GAP_NOT_FOUND", "Learning gap not found or access denied.")
    return {"status": "dismissed"}


@router.get("/learning-gaps/dashboard", response_model=dict[str, Any])
def get_remediation_dashboard_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the learning improvement dashboard."""
    return get_remediation_dashboard(
        database_path=_database_path(request), user_id=current_user.user_id
    )


# ---------------------------------------------------------------------------
# Remediation activities
# ---------------------------------------------------------------------------


@router.get(
    "/learning-gaps/{gap_id}/remediations",
    response_model=dict[str, Any],
)
def list_gap_remediations_route(
    request: Request,
    gap_id: str = PathParam(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List remediation activities for a gap."""
    from app.storage import list_gap_remediations

    remediations = list_gap_remediations(
        _database_path(request), gap_id=gap_id, user_id=current_user.user_id
    )
    if remediations is None:
        raise ApiError(404, "GAP_NOT_FOUND", "Learning gap not found or access denied.")
    return {"gapId": gap_id, "remediations": remediations}


@router.post(
    "/learning-gaps/{gap_id}/remediations",
    response_model=dict[str, Any],
    status_code=201,
)
def generate_remediations_route(
    request: Request,
    gap_id: str = PathParam(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Generate ranked remediation recommendations for a gap."""
    result = generate_remediations_for_gap(
        database_path=_database_path(request),
        gap_id=gap_id,
        user_id=current_user.user_id,
        now=_now(),
    )
    if "error" in result:
        raise ApiError(404, "GAP_NOT_FOUND", result["error"])
    return result


@router.post(
    "/remediations/{remediation_id}/accept",
    response_model=dict[str, Any],
)
def accept_remediation_route(
    request: Request,
    remediation_id: str = PathParam(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Learner accepts a remediation recommendation."""
    result = accept_remediation_activity(
        database_path=_database_path(request),
        remediation_id=remediation_id,
        user_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            404,
            "REMEDIATION_NOT_FOUND",
            "Remediation not found, access denied, or not in recommended state.",
        )
    return result


@router.post(
    "/remediations/{remediation_id}/dismiss",
    response_model=dict[str, Any],
)
def dismiss_remediation_route(
    request: Request,
    remediation_id: str = PathParam(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Learner dismisses a remediation recommendation."""
    result = dismiss_remediation_activity(
        database_path=_database_path(request),
        remediation_id=remediation_id,
        user_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            404,
            "REMEDIATION_NOT_FOUND",
            "Remediation not found, access denied, or not in recommended state.",
        )
    return result


@router.post(
    "/remediations/{remediation_id}/complete",
    response_model=dict[str, Any],
)
def complete_remediation_route(
    request: Request,
    remediation_id: str = PathParam(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Learner marks a remediation as completed."""
    result = complete_remediation_activity(
        database_path=_database_path(request),
        remediation_id=remediation_id,
        user_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            404,
            "REMEDIATION_NOT_FOUND",
            "Remediation not found, access denied, or not in accepted/in-progress state.",
        )
    return result


# ---------------------------------------------------------------------------
# Reassessment
# ---------------------------------------------------------------------------


@router.get(
    "/learning-gaps/{gap_id}/reassessments",
    response_model=dict[str, Any],
)
def list_gap_reassessments_route(
    request: Request,
    gap_id: str = PathParam(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List reassessments for a gap."""
    from app.storage import list_remediation_reassessments

    reassessments = list_remediation_reassessments(
        _database_path(request), gap_id=gap_id, user_id=current_user.user_id
    )
    if reassessments is None:
        raise ApiError(404, "GAP_NOT_FOUND", "Learning gap not found or access denied.")
    return {"gapId": gap_id, "reassessments": reassessments}


@router.post(
    "/learning-gaps/{gap_id}/reassessments",
    response_model=dict[str, Any],
    status_code=201,
)
def create_gap_reassessment_route(
    request: Request,
    gap_id: str = PathParam(...),
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record a reassessment comparing before/after mastery.

    A gap is resolved only when post-remediation mastery >= 60% AND the
    learner explicitly requests resolution.
    """
    result = record_reassessment(
        database_path=_database_path(request),
        gap_id=gap_id,
        user_id=current_user.user_id,
        activity_id=payload.get("activityId"),
        before_mastery=payload.get("beforeMastery", 0.0),
        after_mastery=payload.get("afterMastery", 0.0),
        resolved=payload.get("resolved", False),
    )
    if isinstance(result, dict) and "error" in result:
        raise ApiError(404, "GAP_NOT_FOUND", result["error"])
    if result is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid reassessment data.")
    return result
