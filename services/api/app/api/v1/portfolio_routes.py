"""M88: Learning Portfolio, Achievement & Evidence Management API routes.

Endpoints:
- POST   /api/v1/portfolios                        - create portfolio
- GET    /api/v1/portfolios                        - list portfolios
- GET    /api/v1/portfolios/{portfolio_id}         - get portfolio
- PATCH  /api/v1/portfolios/{portfolio_id}         - update portfolio
- DELETE /api/v1/portfolios/{portfolio_id}         - delete portfolio
- POST   /api/v1/portfolios/{portfolio_id}/entries - create portfolio entry
- GET    /api/v1/portfolios/{portfolio_id}/entries - list portfolio entries
- POST   /api/v1/portfolios/{portfolio_id}/evidence - submit evidence
- GET    /api/v1/portfolios/{portfolio_id}/evidence - list evidence
- POST   /api/v1/evidence/{evidence_id}/versions   - create evidence version
- POST   /api/v1/evidence/{evidence_id}/review     - review evidence version
- POST   /api/v1/evidence/{evidence_id}/expire     - expire evidence
- POST   /api/v1/portfolios/{portfolio_id}/artifacts - create project artifact
- GET    /api/v1/portfolios/{portfolio_id}/artifacts - list project artifacts
- POST   /api/v1/portfolios/{portfolio_id}/sharing - create sharing link
- GET    /api/v1/portfolios/share/{token}          - resolve sharing link (public)
- POST   /api/v1/portfolios/sharing/{link_id}/revoke - revoke sharing link
- GET    /api/v1/portfolios/intelligence           - portfolio intelligence
"""
# ruff: noqa: B008

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.auth import AuthUser, require_auth
from app.errors import ApiError
from app.portfolio import (
    compute_portfolio_intelligence,
    create_portfolio_sharing_link,
    create_user_portfolio,
    expire_evidence,
    get_user_portfolio,
    list_user_portfolios,
    resolve_sharing_link,
    review_evidence_version,
    revoke_portfolio_sharing_link,
    submit_evidence,
)
from app.schemas import (
    EvidenceListResponse,
    EvidenceResponse,
    EvidenceReviewRequest,
    EvidenceReviewResponse,
    EvidenceSubmitRequest,
    EvidenceVersionCreateRequest,
    EvidenceVersionResponse,
    PortfolioCreateRequest,
    PortfolioEntryCreateRequest,
    PortfolioEntryListResponse,
    PortfolioEntryResponse,
    PortfolioIntelligenceResponse,
    PortfolioListResponse,
    PortfolioResponse,
    PortfolioUpdateRequest,
    ProjectArtifactCreateRequest,
    ProjectArtifactListResponse,
    ProjectArtifactResponse,
    SharingLinkCreateRequest,
    SharingLinkResolveResponse,
    SharingLinkResponse,
)
from app.storage import (
    create_evidence_version,
    create_portfolio_entry,
    create_project_artifact,
    delete_portfolio,
    list_evidence,
    list_portfolio_entries,
    list_project_artifacts,
    update_portfolio,
)

router = APIRouter(prefix="/api/v1")


def _database_path(request: Request):
    return request.app.state.settings.database_path


# ---------------------------------------------------------------------------
# Portfolios
# ---------------------------------------------------------------------------


@router.post(
    "/portfolios",
    response_model=PortfolioResponse,
    tags=["portfolio"],
    status_code=201,
)
def create_portfolio_route(
    request: Request,
    payload: PortfolioCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a learner portfolio."""
    database_path = _database_path(request)
    portfolio = create_user_portfolio(
        database_path,
        user_id=current_user.user_id,
        title=payload.title,
        summary=payload.summary,
        visibility=payload.visibility,
    )
    if portfolio is None:
        raise ApiError(400, "PORTFOLIO_CREATE_FAILED", "Portfolio could not be created.")
    return portfolio


@router.get(
    "/portfolios",
    response_model=PortfolioListResponse,
    tags=["portfolio"],
)
def list_portfolios_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List portfolios owned by the user."""
    database_path = _database_path(request)
    portfolios = list_user_portfolios(database_path, user_id=current_user.user_id)
    return {"portfolios": portfolios}


@router.get(
    "/portfolios/{portfolio_id}",
    response_model=PortfolioResponse,
    tags=["portfolio"],
)
def get_portfolio_route(
    request: Request,
    portfolio_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return a portfolio owned by the user."""
    database_path = _database_path(request)
    portfolio = get_user_portfolio(
        database_path, portfolio_id=portfolio_id, user_id=current_user.user_id
    )
    if portfolio is None:
        raise ApiError(404, "PORTFOLIO_NOT_FOUND", "Portfolio was not found or access denied.")
    return portfolio


@router.patch(
    "/portfolios/{portfolio_id}",
    response_model=PortfolioResponse,
    tags=["portfolio"],
)
def update_portfolio_route(
    request: Request,
    portfolio_id: str = Path(min_length=1, max_length=80),
    payload: PortfolioUpdateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update portfolio fields. Owner only."""
    database_path = _database_path(request)
    portfolio = update_portfolio(
        database_path,
        portfolio_id=portfolio_id,
        user_id=current_user.user_id,
        title=payload.title,
        summary=payload.summary,
        visibility=payload.visibility,
        share_token=payload.shareToken,
        share_expires_at=payload.shareExpiresAt,
    )
    if portfolio is None:
        raise ApiError(
            400,
            "PORTFOLIO_UPDATE_FAILED",
            "Portfolio could not be updated or access denied.",
        )
    return portfolio


@router.delete(
    "/portfolios/{portfolio_id}",
    tags=["portfolio"],
    status_code=204,
)
def delete_portfolio_route(
    request: Request,
    portfolio_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> None:
    """Delete a portfolio and its entries. Owner only."""
    database_path = _database_path(request)
    ok = delete_portfolio(database_path, portfolio_id=portfolio_id, user_id=current_user.user_id)
    if not ok:
        raise ApiError(
            400,
            "PORTFOLIO_DELETE_FAILED",
            "Portfolio could not be deleted or access denied.",
        )
    return None


# ---------------------------------------------------------------------------
# Portfolio entries
# ---------------------------------------------------------------------------


@router.post(
    "/portfolios/{portfolio_id}/entries",
    response_model=PortfolioEntryResponse,
    tags=["portfolio"],
    status_code=201,
)
def create_portfolio_entry_route(
    request: Request,
    portfolio_id: str = Path(min_length=1, max_length=80),
    payload: PortfolioEntryCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a portfolio entry linked to a real source."""
    database_path = _database_path(request)
    entry = create_portfolio_entry(
        database_path,
        portfolio_id=portfolio_id,
        user_id=current_user.user_id,
        entry_type=payload.entryType,
        title=payload.title,
        description=payload.description,
        source_type=payload.sourceType,
        source_id=payload.sourceId,
        source_url=payload.sourceUrl,
        verification_status=payload.verificationStatus,
        visibility=payload.visibility,
        competency_ids=payload.competencyIds,
        skill_tags=payload.skillTags,
        metadata=payload.metadata,
    )
    if entry is None:
        raise ApiError(
            400,
            "PORTFOLIO_ENTRY_CREATE_FAILED",
            "Portfolio entry could not be created.",
        )
    return entry


@router.get(
    "/portfolios/{portfolio_id}/entries",
    response_model=PortfolioEntryListResponse,
    tags=["portfolio"],
)
def list_portfolio_entries_route(
    request: Request,
    portfolio_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List portfolio entries for a portfolio."""
    database_path = _database_path(request)
    entries = list_portfolio_entries(
        database_path, portfolio_id=portfolio_id, user_id=current_user.user_id
    )
    return {"entries": entries}


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@router.post(
    "/portfolios/{portfolio_id}/evidence",
    response_model=EvidenceResponse,
    tags=["portfolio"],
    status_code=201,
)
def submit_evidence_route(
    request: Request,
    portfolio_id: str = Path(min_length=1, max_length=80),
    payload: EvidenceSubmitRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Submit evidence for a portfolio entry."""
    database_path = _database_path(request)
    result = submit_evidence(
        database_path,
        user_id=current_user.user_id,
        title=payload.title,
        evidence_type=payload.evidenceType,
        source_type=payload.sourceType,
        source_id=payload.sourceId,
        description=payload.description,
        portfolio_entry_id=payload.portfolioEntryId,
        verification_status=payload.verificationStatus,
        verification_notes=payload.verificationNotes,
        expires_at=payload.expiresAt,
        metadata=payload.metadata,
        initial_version=payload.initialVersion,
    )
    return result


@router.get(
    "/portfolios/{portfolio_id}/evidence",
    response_model=EvidenceListResponse,
    tags=["portfolio"],
)
def list_evidence_route(
    request: Request,
    portfolio_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List evidence for a portfolio."""
    database_path = _database_path(request)
    evidence = list_evidence(
        database_path, user_id=current_user.user_id
    )
    return {"evidence": evidence}


@router.post(
    "/evidence/{evidence_id}/versions",
    response_model=EvidenceVersionResponse,
    tags=["portfolio"],
    status_code=201,
)
def create_evidence_version_route(
    request: Request,
    evidence_id: str = Path(min_length=1, max_length=80),
    payload: EvidenceVersionCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new version of an evidence record."""
    database_path = _database_path(request)
    result = create_evidence_version(
        database_path,
        evidence_id=evidence_id,
        user_id=current_user.user_id,
        version_number=payload.versionNumber,
        title=payload.title,
        description=payload.description,
        content=payload.content,
        file_attachments=payload.fileAttachments,
        external_links=payload.externalLinks,
        submitted_by=payload.submittedBy,
        submitted_at=payload.submittedAt,
        review_status=payload.reviewStatus,
        is_current=payload.isCurrent,
    )
    if result is None:
        raise ApiError(
            400,
            "EVIDENCE_VERSION_CREATE_FAILED",
            "Evidence version could not be created or access denied.",
        )
    return result


@router.post(
    "/evidence/{evidence_id}/review",
    response_model=EvidenceReviewResponse,
    tags=["portfolio"],
)
def review_evidence_route(
    request: Request,
    evidence_id: str = Path(min_length=1, max_length=80),
    payload: EvidenceReviewRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Review an evidence version (approve/reject/under_review)."""
    database_path = _database_path(request)
    # Verify the evidence is owned by the user and belongs to this evidence id.
    evidence_rows = list_evidence(
        database_path, evidence_id=evidence_id, user_id=current_user.user_id
    )
    if not evidence_rows:
        raise ApiError(404, "EVIDENCE_NOT_FOUND", "Evidence was not found or access denied.")
    version_id = payload.versionId
    return review_evidence_version(
        database_path,
        evidence_id=evidence_id,
        user_id=current_user.user_id,
        version_id=version_id,
        review_status=payload.reviewStatus,
        reviewed_by=payload.reviewedBy,
        rejection_reason=payload.rejectionReason,
    )


@router.post(
    "/evidence/{evidence_id}/expire",
    tags=["portfolio"],
)
def expire_evidence_route(
    request: Request,
    evidence_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Expire an evidence record."""
    database_path = _database_path(request)
    return expire_evidence(
        database_path, evidence_id=evidence_id, user_id=current_user.user_id
    )


# ---------------------------------------------------------------------------
# Project artifacts
# ---------------------------------------------------------------------------


@router.post(
    "/portfolios/{portfolio_id}/entries/{entry_id}/artifacts",
    response_model=ProjectArtifactResponse,
    tags=["portfolio"],
    status_code=201,
)
def create_project_artifact_route(
    request: Request,
    portfolio_id: str = Path(min_length=1, max_length=80),
    entry_id: str = Path(min_length=1, max_length=80),
    payload: ProjectArtifactCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a project artifact linked to a portfolio entry."""
    database_path = _database_path(request)
    artifact = create_project_artifact(
        database_path,
        portfolio_entry_id=entry_id,
        user_id=current_user.user_id,
        title=payload.title,
        artifact_type=payload.artifactType,
        description=payload.description,
        file_name=payload.fileName,
        file_path=payload.filePath,
        file_size_bytes=payload.fileSizeBytes,
        file_hash=payload.fileHash,
        content_type=payload.contentType,
        external_url=payload.externalUrl,
        skills=payload.skills,
    )
    if artifact is None:
        raise ApiError(
            400,
            "PROJECT_ARTIFACT_CREATE_FAILED",
            "Project artifact could not be created.",
        )
    return artifact


@router.get(
    "/portfolios/{portfolio_id}/entries/{entry_id}/artifacts",
    response_model=ProjectArtifactListResponse,
    tags=["portfolio"],
)
def list_project_artifacts_route(
    request: Request,
    portfolio_id: str = Path(min_length=1, max_length=80),
    entry_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List project artifacts for a portfolio entry."""
    database_path = _database_path(request)
    artifacts = list_project_artifacts(
        database_path, portfolio_entry_id=entry_id, user_id=current_user.user_id
    )
    return {"artifacts": artifacts}


# ---------------------------------------------------------------------------
# Sharing links
# ---------------------------------------------------------------------------


@router.post(
    "/portfolios/{portfolio_id}/sharing",
    response_model=SharingLinkResponse,
    tags=["portfolio"],
    status_code=201,
)
def create_portfolio_sharing_link_route(
    request: Request,
    portfolio_id: str = Path(min_length=1, max_length=80),
    payload: SharingLinkCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a revocable sharing link for a portfolio. Owner only."""
    database_path = _database_path(request)
    result = create_portfolio_sharing_link(
        database_path,
        portfolio_id=portfolio_id,
        user_id=current_user.user_id,
        token=payload.token,
        entry_ids=payload.entryIds,
        expires_at=payload.expiresAt,
        max_uses=payload.maxUses,
    )
    return result


@router.get(
    "/portfolios/share/{token}",
    response_model=SharingLinkResolveResponse,
    tags=["portfolio"],
)
def resolve_sharing_link_route(
    request: Request,
    token: str = Path(min_length=8, max_length=60),
) -> dict[str, Any]:
    """Public resolution of a sharing link token. No auth required."""
    database_path = _database_path(request)
    result = resolve_sharing_link(database_path, token=token)
    if result is None:
        raise ApiError(404, "SHARING_LINK_NOT_FOUND", "Sharing link was not found or is invalid.")
    return result


@router.post(
    "/portfolios/sharing/{link_id}/revoke",
    tags=["portfolio"],
)
def revoke_portfolio_sharing_link_route(
    request: Request,
    link_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Revoke a sharing link. Owner only."""
    database_path = _database_path(request)
    return revoke_portfolio_sharing_link(
        database_path, link_id=link_id, user_id=current_user.user_id
    )


# ---------------------------------------------------------------------------
# Portfolio intelligence
# ---------------------------------------------------------------------------


@router.get(
    "/portfolios/intelligence",
    response_model=PortfolioIntelligenceResponse,
    tags=["portfolio"],
)
def portfolio_intelligence_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    portfolio_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Assemble read-only intelligence for a user's portfolio."""
    database_path = _database_path(request)
    return compute_portfolio_intelligence(
        database_path, user_id=current_user.user_id, portfolio_id=portfolio_id
    )
