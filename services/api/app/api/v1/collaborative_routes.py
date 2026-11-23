"""M90: AI-Powered Peer Learning & Collaborative Knowledge Intelligence API routes.

Endpoints:
- POST   /api/v1/collaborative/discussions            - create discussion
- GET    /api/v1/collaborative/discussions            - list discussions
- GET    /api/v1/collaborative/discussions/{id}        - get discussion
- POST   /api/v1/collaborative/discussions/{id}/summary - AI discussion summary
- POST   /api/v1/collaborative/discussions/{id}/unanswered - detect unanswered
- POST   /api/v1/collaborative/discussions/{id}/questions - suggested questions
- POST   /api/v1/collaborative/explanations            - submit peer explanation
- GET    /api/v1/collaborative/explanations            - list explanations
- GET    /api/v1/collaborative/explanations/{id}        - get explanation
- POST   /api/v1/collaborative/explanations/{id}/evaluate - evaluate vs objective
- POST   /api/v1/collaborative/explanations/{id}/feedback - submit peer feedback
- GET    /api/v1/collaborative/explanations/{id}/feedback - list feedback
- POST   /api/v1/collaborative/artifacts               - create shared artifact
- GET    /api/v1/collaborative/artifacts               - list artifacts
- GET    /api/v1/collaborative/artifacts/{id}           - get artifact
- POST   /api/v1/collaborative/artifacts/{id}/versions  - new artifact version
- GET    /api/v1/collaborative/artifacts/{id}/versions  - list versions
- POST   /api/v1/collaborative/artifacts/{id}/contributors - add contributor
- GET    /api/v1/collaborative/groups/{id}/analytics    - group analytics
- GET    /api/v1/collaborative/contributions            - user contribution history
"""
# ruff: noqa: B008

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.auth import AuthUser, require_auth
from app.collaborative import (
    create_collab_artifact,
    create_collab_discussion,
    create_peer_explanation,
    create_peer_feedback,
    detect_unanswered_questions,
    evaluate_explanation_against_objective,
    generate_discussion_questions,
    get_collab_artifact,
    get_collab_discussion,
    get_group_analytics,
    list_collab_artifacts,
    list_collab_discussions,
    list_contribution_history,
    list_explanation_feedback,
    list_peer_explanations,
    summarize_discussion,
)
from app.config import Settings
from app.errors import ApiError
from app.schemas import (
    CollaborationAISummaryResponse,
    CollaborativeDiscussionCreateRequest,
    CollaborativeDiscussionListResponse,
    CollaborativeDiscussionResponse,
    ExplainEvaluationResponse,
    GroupAnalyticsResponse,
    PeerExplanationCreateRequest,
    PeerExplanationListResponse,
    PeerExplanationResponse,
    PeerFeedbackCreateRequest,
    PeerFeedbackListResponse,
    PeerFeedbackResponse,
    SharedArtifactCreateRequest,
    SharedArtifactListResponse,
    SharedArtifactResponse,
)
from app.storage import (
    add_artifact_contributor,
    create_artifact_version,
    get_shared_artifact,
    list_artifact_versions,
)

router = APIRouter(prefix="/api/v1")


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _settings(request: Request) -> Settings:
    return request.app.state.settings


# ---------------------------------------------------------------------------
# Collaborative discussions
# ---------------------------------------------------------------------------


@router.post(
    "/collaborative/discussions",
    response_model=CollaborativeDiscussionResponse,
    tags=["collaborative"],
    status_code=201,
)
def create_collaborative_discussion_route(
    request: Request,
    payload: CollaborativeDiscussionCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a collaborative discussion."""
    database_path = _database_path(request)
    settings = _settings(request)
    discussion = create_collab_discussion(
        database_path,
        settings=settings,
        group_id=payload.groupId,
        author_id=current_user.user_id,
        topic=payload.topic,
        title=payload.title,
        discussion_type=payload.discussionType,
        visibility=payload.visibility,
        ai_generated=payload.aiGenerated,
    )
    return discussion


@router.get(
    "/collaborative/discussions",
    response_model=CollaborativeDiscussionListResponse,
    tags=["collaborative"],
)
def list_collaborative_discussions_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    group_id: str | None = Query(default=None),
    topic: str | None = Query(default=None),
    discussion_type: str | None = Query(default=None),
    visibility: str | None = Query(default=None),
    author_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List collaborative discussions."""
    database_path = _database_path(request)
    discussions = list_collab_discussions(
        database_path,
        group_id=group_id,
        topic=topic,
        discussion_type=discussion_type,
        visibility=visibility,
        author_id=author_id,
        viewer_id=current_user.user_id,
        limit=limit,
        offset=offset,
    )
    return {"discussions": discussions}


@router.get(
    "/collaborative/discussions/{discussion_id}",
    response_model=CollaborativeDiscussionResponse,
    tags=["collaborative"],
)
def get_collaborative_discussion_route(
    request: Request,
    discussion_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return a collaborative discussion."""
    database_path = _database_path(request)
    discussion = get_collab_discussion(
        database_path, discussion_id=discussion_id, viewer_id=current_user.user_id
    )
    if discussion is None:
        raise ApiError(404, "COLLAB_DISCUSSION_NOT_FOUND", "Discussion was not found.")
    return discussion


@router.post(
    "/collaborative/discussions/{discussion_id}/summary",
    response_model=CollaborationAISummaryResponse,
    tags=["collaborative"],
)
def summarize_discussion_route(
    request: Request,
    discussion_id: str = Path(min_length=1, max_length=80),
    generated_by: str = Query(default="ai"),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """AI-facilitated discussion summary grounded in actual content."""
    database_path = _database_path(request)
    settings = _settings(request)
    return summarize_discussion(
        database_path,
        settings=settings,
        discussion_id=discussion_id,
        generated_by=generated_by,
    )


@router.post(
    "/collaborative/discussions/{discussion_id}/unanswered",
    tags=["collaborative"],
)
def detect_unanswered_questions_route(
    request: Request,
    discussion_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Identify questions lacking a verified peer answer."""
    database_path = _database_path(request)
    return detect_unanswered_questions(
        database_path, discussion_id=discussion_id
    )


@router.post(
    "/collaborative/discussions/{discussion_id}/questions",
    tags=["collaborative"],
)
def generate_discussion_questions_route(
    request: Request,
    discussion_id: str = Path(min_length=1, max_length=80),
    count: int = Query(default=3, ge=1, le=10),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Suggest follow-up questions for a discussion."""
    database_path = _database_path(request)
    return generate_discussion_questions(
        database_path, discussion_id=discussion_id, count=count
    )


# ---------------------------------------------------------------------------
# Peer explanations
# ---------------------------------------------------------------------------


@router.post(
    "/collaborative/explanations",
    response_model=PeerExplanationResponse,
    tags=["collaborative"],
    status_code=201,
)
def submit_peer_explanation_route(
    request: Request,
    payload: PeerExplanationCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Submit a learner explanation of a concept."""
    database_path = _database_path(request)
    explanation = create_peer_explanation(
        database_path,
        author_id=current_user.user_id,
        concept_id=payload.conceptId,
        concept_name=payload.conceptName,
        explanation=payload.explanation,
        discussion_id=payload.discussionId,
        learning_objective=payload.learningObjective,
    )
    return explanation


@router.get(
    "/collaborative/explanations",
    response_model=PeerExplanationListResponse,
    tags=["collaborative"],
)
def list_peer_explanations_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    discussion_id: str | None = Query(default=None),
    concept_id: str | None = Query(default=None),
    author_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List peer explanations."""
    database_path = _database_path(request)
    explanations = list_peer_explanations(
        database_path,
        discussion_id=discussion_id,
        concept_id=concept_id,
        author_id=author_id,
        limit=limit,
        offset=offset,
    )
    return {"explanations": explanations}


@router.get(
    "/collaborative/explanations/{explanation_id}",
    response_model=PeerExplanationResponse,
    tags=["collaborative"],
)
def get_peer_explanation_route(
    request: Request,
    explanation_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return a peer explanation."""
    database_path = _database_path(request)
    from app.storage import get_peer_explanation as _get_peer_explanation

    explanation = _get_peer_explanation(database_path, explanation_id=explanation_id)
    if explanation is None:
        raise ApiError(404, "EXPLANATION_NOT_FOUND", "Explanation was not found.")
    return explanation


@router.post(
    "/collaborative/explanations/{explanation_id}/evaluate",
    response_model=ExplainEvaluationResponse,
    tags=["collaborative"],
)
def evaluate_explanation_route(
    request: Request,
    explanation_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Evaluate a peer explanation against its learning objective."""
    database_path = _database_path(request)
    return evaluate_explanation_against_objective(
        database_path, explanation_id=explanation_id
    )


@router.post(
    "/collaborative/explanations/{explanation_id}/feedback",
    response_model=PeerFeedbackResponse,
    tags=["collaborative"],
    status_code=201,
)
def submit_peer_feedback_route(
    request: Request,
    explanation_id: str = Path(min_length=1, max_length=80),
    payload: PeerFeedbackCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Submit structured peer feedback on an explanation."""
    database_path = _database_path(request)
    if payload.explanationId != explanation_id:
        raise ApiError(400, "EXPLANATION_ID_MISMATCH", "Explanation ID mismatch.")
    feedback = create_peer_feedback(
        database_path,
        explanation_id=explanation_id,
        author_id=current_user.user_id,
        feedback_type=payload.feedbackType,
        comment=payload.comment,
        addresses_concept_gap=payload.addressesConceptGap,
        suggestion=payload.suggestion,
    )
    return feedback


@router.get(
    "/collaborative/explanations/{explanation_id}/feedback",
    response_model=PeerFeedbackListResponse,
    tags=["collaborative"],
)
def list_explanation_feedback_route(
    request: Request,
    explanation_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List peer feedback for an explanation."""
    database_path = _database_path(request)
    feedback = list_explanation_feedback(
        database_path, explanation_id=explanation_id
    )
    return {"feedback": feedback}


# ---------------------------------------------------------------------------
# Shared artifacts
# ---------------------------------------------------------------------------


@router.post(
    "/collaborative/artifacts",
    response_model=SharedArtifactResponse,
    tags=["collaborative"],
    status_code=201,
)
def create_collaborative_artifact_route(
    request: Request,
    payload: SharedArtifactCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a shared knowledge artifact."""
    database_path = _database_path(request)
    artifact = create_collab_artifact(
        database_path,
        group_id=payload.groupId,
        author_id=current_user.user_id,
        title=payload.title,
        artifact_type=payload.artifactType,
        description=payload.description,
        visibility=payload.visibility,
        content=payload.content,
    )
    return artifact


@router.get(
    "/collaborative/artifacts",
    response_model=SharedArtifactListResponse,
    tags=["collaborative"],
)
def list_collaborative_artifacts_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    group_id: str | None = Query(default=None),
    artifact_type: str | None = Query(default=None),
    visibility: str | None = Query(default=None),
    author_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List shared artifacts."""
    database_path = _database_path(request)
    artifacts = list_collab_artifacts(
        database_path,
        group_id=group_id,
        artifact_type=artifact_type,
        visibility=visibility,
        author_id=author_id,
        viewer_id=current_user.user_id,
        limit=limit,
        offset=offset,
    )
    return {"artifacts": artifacts}


@router.get(
    "/collaborative/artifacts/{artifact_id}",
    response_model=SharedArtifactResponse,
    tags=["collaborative"],
)
def get_collaborative_artifact_route(
    request: Request,
    artifact_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return a shared artifact."""
    database_path = _database_path(request)
    artifact = get_collab_artifact(database_path, artifact_id=artifact_id)
    if artifact is None:
        raise ApiError(404, "ARTIFACT_NOT_FOUND", "Artifact was not found.")
    return artifact


@router.post(
    "/collaborative/artifacts/{artifact_id}/versions",
    response_model=dict[str, Any],
    tags=["collaborative"],
    status_code=201,
)
def create_collaborative_artifact_version_route(
    request: Request,
    artifact_id: str = Path(min_length=1, max_length=80),
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new version of a shared artifact."""
    database_path = _database_path(request)
    artifact = get_shared_artifact(database_path, artifact_id=artifact_id)
    if artifact is None:
        raise ApiError(404, "ARTIFACT_NOT_FOUND", "Artifact was not found.")
    new_version_number = artifact["versionNumber"] + 1
    result = create_artifact_version(
        database_path,
        artifact_id=artifact_id,
        author_id=current_user.user_id,
        version_number=new_version_number,
        title=payload.get("title", artifact["title"]),
        content=payload.get("content"),
        edit_summary=payload.get("editSummary"),
        is_current=payload.get("isCurrent", True),
    )
    if result is None:
        raise ApiError(400, "ARTIFACT_VERSION_FAILED", "New version could not be created.")
    add_artifact_contributor(
        database_path,
        artifact_id=artifact_id,
        user_id=current_user.user_id,
        role="editor",
        contribution_summary=payload.get("editSummary") or "Edited the artifact.",
    )
    return result


@router.get(
    "/collaborative/artifacts/{artifact_id}/versions",
    response_model=dict[str, Any],
    tags=["collaborative"],
)
def list_collaborative_artifact_versions_route(
    request: Request,
    artifact_id: str = Path(min_length=1, max_length=80),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List the edit history of a shared artifact."""
    database_path = _database_path(request)
    versions = list_artifact_versions(
        database_path, artifact_id=artifact_id, limit=limit, offset=offset
    )
    return {"versions": versions}


@router.post(
    "/collaborative/artifacts/{artifact_id}/contributors",
    response_model=dict[str, Any],
    tags=["collaborative"],
)
def add_collaborative_artifact_contributor_route(
    request: Request,
    artifact_id: str = Path(min_length=1, max_length=80),
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Add a contributor to a shared artifact."""
    database_path = _database_path(request)
    result = add_artifact_contributor(
        database_path,
        artifact_id=artifact_id,
        user_id=payload.get("userId", current_user.user_id),
        role=payload.get("role", "contributor"),
        contribution_summary=payload.get("contributionSummary"),
    )
    if result is None:
        raise ApiError(400, "CONTRIBUTOR_ADD_FAILED", "Contributor could not be added.")
    return result


# ---------------------------------------------------------------------------
# Group analytics
# ---------------------------------------------------------------------------


@router.get(
    "/collaborative/groups/{group_id}/analytics",
    response_model=GroupAnalyticsResponse,
    tags=["collaborative"],
)
def get_group_analytics_route(
    request: Request,
    group_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Read-only group-level analytics."""
    database_path = _database_path(request)
    return get_group_analytics(
        database_path, group_id=group_id, viewer_id=current_user.user_id
    )


# ---------------------------------------------------------------------------
# Contribution history
# ---------------------------------------------------------------------------


@router.get(
    "/collaborative/contributions",
    tags=["collaborative"],
)
def list_contribution_history_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List the authenticated user's own collaboration contributions."""
    database_path = _database_path(request)
    history = list_contribution_history(
        database_path, user_id=current_user.user_id, limit=limit, offset=offset
    )
    return {"history": history}
