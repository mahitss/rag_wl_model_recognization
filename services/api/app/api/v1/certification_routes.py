"""M87: Learning Outcome Verification & Competency Certification API routes.

Endpoints:
- GET    /api/v1/certification/competencies              - list competencies w/ evaluation
- POST   /api/v1/certification/competencies/{id}/evaluate - evaluate one competency
- POST   /api/v1/certification/outcomes                   - create learning outcome
- GET    /api/v1/certification/outcomes                   - list outcomes
- GET    /api/v1/certification/outcomes/{id}              - get outcome
- POST   /api/v1/certification/outcomes/{id}/evidence     - record outcome evidence
- GET    /api/v1/certification/outcomes/{id}/evidence     - list outcome evidence
- POST   /api/v1/certification/outcomes/{id}/verify       - verify learning outcome
- GET    /api/v1/certification/outcomes/{id}/verifications- list outcome verifications
- POST   /api/v1/certification/certifications             - create certification definition
- GET    /api/v1/certification/certifications             - list certification definitions
- GET    /api/v1/certification/certifications/{id}        - get certification definition
- POST   /api/v1/certification/certifications/{id}/eligibility - check eligibility
- POST   /api/v1/certification/certifications/{id}/certificates - issue certificate
- GET    /api/v1/certification/certificates               - list certificates
- GET    /api/v1/certification/certificates/{id}          - get certificate
- POST   /api/v1/certification/certificates/{id}/revoke   - revoke certificate
- GET    /api/v1/certification/verify/{code}              - public certificate verification
"""
# ruff: noqa: B008

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.auth import AuthUser, require_auth
from app.certification import (
    check_certification_eligibility,
    evaluate_competency,
    issue_certificate_if_eligible,
    list_user_competency_status,
    verify_learning_outcome,
)
from app.errors import ApiError
from app.schemas import (
    CertificateIssueRequest,
    CertificateListResponse,
    CertificateResponse,
    CertificateRevokeRequest,
    CertificationDefinitionCreateRequest,
    CertificationDefinitionListResponse,
    CertificationDefinitionResponse,
    CertificationEligibilityResponse,
    CompetencyEvaluationRequest,
    CompetencyEvaluationResponse,
    LearningOutcomeCreateRequest,
    LearningOutcomeListResponse,
    LearningOutcomeResponse,
    OutcomeEvidenceRecordRequest,
    OutcomeEvidenceResponse,
    OutcomeVerificationResponse,
    PublicCertificateVerifyResponse,
)
from app.storage import (
    create_certification_definition,
    create_learning_outcome,
    get_certificate,
    get_certification_definition,
    get_learning_outcome,
    list_certificates,
    list_certification_definitions,
    list_learning_outcomes,
    list_outcome_evidence,
    list_outcome_verifications,
    record_outcome_evidence,
    revoke_certificate,
    verify_certificate_public,
)

router = APIRouter(prefix="/api/v1")


def _database_path(request: Request):
    return request.app.state.settings.database_path


# ---------------------------------------------------------------------------
# Competency evaluation
# ---------------------------------------------------------------------------


@router.get(
    "/certification/competencies",
    response_model=list[CompetencyEvaluationResponse],
    tags=["certification"],
)
def list_competency_evaluations_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    proficiency_threshold: float = Query(
        default=0.80, ge=0.0, le=1.0
    ),
) -> list[dict[str, Any]]:
    """Evaluate and return the current status of all user competencies."""
    database_path = _database_path(request)
    return list_user_competency_status(
        database_path,
        user_id=current_user.user_id,
        proficiency_threshold=proficiency_threshold,
    )


@router.post(
    "/certification/competencies/{competency_id}/evaluate",
    response_model=CompetencyEvaluationResponse,
    tags=["certification"],
)
def evaluate_competency_route(
    request: Request,
    competency_id: str = Path(min_length=1, max_length=80),
    payload: CompetencyEvaluationRequest | None = Body(default=None),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Deterministically evaluate a single competency from persisted evidence."""
    database_path = _database_path(request)
    threshold = (
        payload.proficiencyThreshold
        if payload and payload.proficiencyThreshold is not None
        else 0.80
    )
    return evaluate_competency(
        database_path,
        user_id=current_user.user_id,
        competency_id=competency_id,
        proficiency_threshold=threshold,
    )


# ---------------------------------------------------------------------------
# Learning outcomes
# ---------------------------------------------------------------------------


@router.post(
    "/certification/outcomes",
    response_model=LearningOutcomeResponse,
    tags=["certification"],
    status_code=201,
)
def create_learning_outcome_route(
    request: Request,
    payload: LearningOutcomeCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a learning outcome linked to a competency."""
    database_path = _database_path(request)
    outcome = create_learning_outcome(
        database_path,
        user_id=current_user.user_id,
        competency_id=payload.competencyId,
        title=payload.title,
        description=payload.description,
        performance_criteria=payload.performanceCriteria,
        evidence_requirements=payload.evidenceRequirements,
        proficiency_threshold=payload.proficiencyThreshold,
        status=payload.status,
    )
    if outcome is None:
        raise ApiError(
            400,
            "LEARNING_OUTCOME_CREATE_FAILED",
            "Could not create learning outcome (check competency ownership).",
        )
    return outcome


@router.get(
    "/certification/outcomes",
    response_model=LearningOutcomeListResponse,
    tags=["certification"],
)
def list_learning_outcomes_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    competency_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    """List learning outcomes owned by the user."""
    database_path = _database_path(request)
    outcomes = list_learning_outcomes(
        database_path,
        user_id=current_user.user_id,
        competency_id=competency_id,
        status=status,
    )
    return {"outcomes": outcomes}


@router.get(
    "/certification/outcomes/{outcome_id}",
    response_model=LearningOutcomeResponse,
    tags=["certification"],
)
def get_learning_outcome_route(
    request: Request,
    outcome_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return a single learning outcome."""
    database_path = _database_path(request)
    outcome = get_learning_outcome(
        database_path, outcome_id=outcome_id, user_id=current_user.user_id
    )
    if outcome is None:
        raise ApiError(404, "LEARNING_OUTCOME_NOT_FOUND", "Learning outcome was not found.")
    return outcome


@router.post(
    "/certification/outcomes/{outcome_id}/evidence",
    response_model=OutcomeEvidenceResponse,
    tags=["certification"],
    status_code=201,
)
def record_outcome_evidence_route(
    request: Request,
    outcome_id: str = Path(min_length=1, max_length=80),
    payload: OutcomeEvidenceRecordRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record evidence for a learning outcome. Duplicate evidence is rejected."""
    database_path = _database_path(request)
    result = record_outcome_evidence(
        database_path,
        outcome_id=outcome_id,
        user_id=current_user.user_id,
        evidence_type=payload.evidenceType,
        source_id=payload.sourceId,
        source_type=payload.sourceType,
        score=payload.score,
        recorded_at=payload.recordedAt,
    )
    if result is None:
        raise ApiError(
            409,
            "OUTCOME_EVIDENCE_DUPLICATE_OR_INVALID",
            "Evidence was not recorded (duplicate source, invalid score, or access denied).",
        )
    return result


@router.get(
    "/certification/outcomes/{outcome_id}/evidence",
    response_model=list[OutcomeEvidenceResponse],
    tags=["certification"],
)
def list_outcome_evidence_route(
    request: Request,
    outcome_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> list[dict[str, Any]]:
    """List evidence records for a learning outcome."""
    database_path = _database_path(request)
    return list_outcome_evidence(
        database_path, outcome_id=outcome_id, user_id=current_user.user_id
    )


@router.post(
    "/certification/outcomes/{outcome_id}/verify",
    response_model=OutcomeVerificationResponse,
    tags=["certification"],
)
def verify_learning_outcome_route(
    request: Request,
    outcome_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Verify a learning outcome using persisted evidence."""
    database_path = _database_path(request)
    return verify_learning_outcome(
        database_path, user_id=current_user.user_id, outcome_id=outcome_id
    )


@router.get(
    "/certification/outcomes/{outcome_id}/verifications",
    response_model=list[OutcomeVerificationResponse],
    tags=["certification"],
)
def list_outcome_verifications_route(
    request: Request,
    outcome_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> list[dict[str, Any]]:
    """List historical outcome verification decisions."""
    database_path = _database_path(request)
    return list_outcome_verifications(
        database_path, outcome_id=outcome_id, user_id=current_user.user_id
    )


# ---------------------------------------------------------------------------
# Certification definitions
# ---------------------------------------------------------------------------


@router.post(
    "/certification/certifications",
    response_model=CertificationDefinitionResponse,
    tags=["certification"],
    status_code=201,
)
def create_certification_definition_route(
    request: Request,
    payload: CertificationDefinitionCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a certification definition with eligibility requirements."""
    database_path = _database_path(request)
    definition = create_certification_definition(
        database_path,
        user_id=current_user.user_id,
        title=payload.title,
        description=payload.description,
        requirements=payload.requirements,
        proficiency_threshold=payload.proficiencyThreshold,
        expires_days=payload.expiresDays,
        is_active=payload.isActive,
    )
    if definition is None:
        raise ApiError(
            400,
            "CERTIFICATION_DEFINITION_CREATE_FAILED",
            "Could not create certification definition.",
        )
    return definition


@router.get(
    "/certification/certifications",
    response_model=CertificationDefinitionListResponse,
    tags=["certification"],
)
def list_certification_definitions_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    is_active: bool | None = Query(default=None),
) -> dict[str, Any]:
    """List certification definitions owned by the user."""
    database_path = _database_path(request)
    definitions = list_certification_definitions(
        database_path, user_id=current_user.user_id, is_active=is_active
    )
    return {"definitions": definitions}


@router.get(
    "/certification/certifications/{cert_id}",
    response_model=CertificationDefinitionResponse,
    tags=["certification"],
)
def get_certification_definition_route(
    request: Request,
    cert_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return a single certification definition."""
    database_path = _database_path(request)
    definition = get_certification_definition(
        database_path, cert_id=cert_id, user_id=current_user.user_id
    )
    if definition is None:
        raise ApiError(
            404, "CERTIFICATION_DEFINITION_NOT_FOUND",
            "Certification definition was not found.",
        )
    return definition


@router.post(
    "/certification/certifications/{cert_id}/eligibility",
    response_model=CertificationEligibilityResponse,
    tags=["certification"],
)
def check_certification_eligibility_route(
    request: Request,
    cert_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Check certification eligibility for the authenticated user."""
    database_path = _database_path(request)
    return check_certification_eligibility(
        database_path, user_id=current_user.user_id, cert_definition_id=cert_id
    )


# ---------------------------------------------------------------------------
# Certificates (issuance)
# ---------------------------------------------------------------------------


@router.post(
    "/certification/certifications/{cert_id}/certificates",
    response_model=CertificateResponse,
    tags=["certification"],
    status_code=201,
)
def issue_certificate_route(
    request: Request,
    cert_id: str = Path(min_length=1, max_length=80),
    payload: CertificateIssueRequest | None = Body(default=None),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Issue a certificate only when all eligibility requirements are met.

    Transactional and idempotent.
    """
    database_path = _database_path(request)
    certificate = issue_certificate_if_eligible(
        database_path,
        user_id=current_user.user_id,
        cert_definition_id=cert_id,
        verification_code=payload.verificationCode if payload else None,
    )
    if certificate is None:
        # Distinguish ineligible vs already-issued.
        eligibility = check_certification_eligibility(
            database_path,
            user_id=current_user.user_id,
            cert_definition_id=cert_id,
        )
        if eligibility.get("alreadyCertified"):
            raise ApiError(
                409,
                "CERTIFICATE_ALREADY_ISSUED",
                "A certificate for this definition has already been issued to this user.",
            )
        raise ApiError(
            403,
            "CERTIFICATION_INELIGIBLE",
            "Not all certification requirements are satisfied.",
        )
    return certificate


@router.get(
    "/certification/certificates",
    response_model=CertificateListResponse,
    tags=["certification"],
)
def list_certificates_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    """List certificates owned by the user."""
    database_path = _database_path(request)
    certificates = list_certificates(
        database_path, user_id=current_user.user_id, status=status
    )
    return {"certificates": certificates}


@router.get(
    "/certification/certificates/{certificate_id}",
    response_model=CertificateResponse,
    tags=["certification"],
)
def get_certificate_route(
    request: Request,
    certificate_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return a certificate (ownership enforced)."""
    database_path = _database_path(request)
    certificate = get_certificate(
        database_path, certificate_id=certificate_id, user_id=current_user.user_id
    )
    if certificate is None:
        raise ApiError(404, "CERTIFICATE_NOT_FOUND", "Certificate was not found.")
    return certificate


@router.post(
    "/certification/certificates/{certificate_id}/revoke",
    response_model=CertificateResponse,
    tags=["certification"],
)
def revoke_certificate_route(
    request: Request,
    certificate_id: str = Path(min_length=1, max_length=80),
    payload: CertificateRevokeRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Revoke an issued certificate. Owner only."""
    database_path = _database_path(request)
    certificate = revoke_certificate(
        database_path,
        certificate_id=certificate_id,
        user_id=current_user.user_id,
        reason=payload.reason,
    )
    if certificate is None:
        raise ApiError(
            400,
            "CERTIFICATE_REVOKE_FAILED",
            "Could not revoke certificate (not found, not owned, or not issued).",
        )
    return certificate


# ---------------------------------------------------------------------------
# Public certificate verification (no auth)
# ---------------------------------------------------------------------------


@router.get(
    "/certification/verify/{verification_code}",
    response_model=PublicCertificateVerifyResponse,
    tags=["certification"],
)
def verify_certificate_public_route(
    request: Request,
    verification_code: str = Path(min_length=8, max_length=40),
) -> dict[str, Any]:
    """Public certificate verification.

    Validates the certificate identifier, confirms authenticity and current
    status, and exposes only approved, non-sensitive information. No learner
    personal data, assessment answers, or analytics are revealed.
    """
    database_path = _database_path(request)
    return verify_certificate_public(
        database_path, verification_code=verification_code
    )
