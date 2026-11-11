"""M81: AI-Powered Learning Content Recommendation & Discovery API routes.

Endpoints:

* GET    /api/v1/recommendations/          - Personalized recommendations
* POST   /api/v1/recommendations/feedback  - Record learner feedback
* GET    /api/v1/recommendations/feedback  - List learner feedback
* DELETE /api/v1/recommendations/feedback/{feedback_id}  - Undo a dismissal

All endpoints require authentication and enforce tenant-scoped access.
"""
# ruff: noqa: B008,E501

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request

from app.auth import AuthUser, require_auth
from app.config import Settings
from app.errors import ApiError
from app.recommendations import (
    RecommendationRequest,
    generate_recommendations,
    list_feedback,
    record_feedback,
)
from app.schemas import (
    RecommendationFeedbackCreate,
    RecommendationFeedbackListResponse,
    RecommendationFeedbackResponse,
    RecommendationResponse,
)
from app.storage import delete_recommendation_feedback

router = APIRouter(prefix="/api/v1/recommendations", tags=["recommendations"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("", response_model=RecommendationResponse)
def get_recommendations(
    request: Request,
    limit: int = Query(default=10, ge=1, le=100),
    include_dismissed: bool = Query(default=False),
    topic_id: str | None = Query(default=None),
    difficulty_level: str | None = Query(default=None),
    include_prerequisites: bool = Query(default=True),
    include_knowledge_gaps: bool = Query(default=True),
    current_user: AuthUser = Depends(require_auth),
) -> RecommendationResponse:
    """Return personalized, explainable learning recommendations for the
    authenticated learner.

    Combines candidates from the adaptive, predictive, study-coach,
    knowledge-graph, and gap-detection subsystems into a single ranked,
    deduplicated list.  Dismissed content is suppressed by default.
    """
    database_path = _database_path(request)
    req = RecommendationRequest(
        limit=limit,
        includeDismissed=include_dismissed,
        topicId=topic_id,
        difficultyLevel=difficulty_level,
        includePrerequisites=include_prerequisites,
        includeKnowledgeGaps=include_knowledge_gaps,
    )
    return generate_recommendations(
        database_path,
        user_id=current_user.user_id,
        settings=_settings(request),
        request=req,
    )


@router.post(
    "/feedback",
    response_model=RecommendationFeedbackResponse,
    status_code=201,
)
def post_feedback(
    request: Request,
    payload: RecommendationFeedbackCreate = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> RecommendationFeedbackResponse:
    """Record learner feedback (dismiss/select/rate/postpone) on a
    recommendation.  Dismissals suppress the target from future
    recommendations."""
    database_path = _database_path(request)
    return record_feedback(
        database_path,
        user_id=current_user.user_id,
        feedback=payload,
        settings=_settings(request),
    )


@router.get("/feedback", response_model=RecommendationFeedbackListResponse)
def get_feedback(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None),
    current_user: AuthUser = Depends(require_auth),
) -> RecommendationFeedbackListResponse:
    """List recommendation feedback for the authenticated learner."""
    database_path = _database_path(request)
    return list_feedback(
        database_path,
        user_id=current_user.user_id,
        limit=limit,
        offset=offset,
        action=action,
    )


@router.delete("/feedback/{feedback_id}", response_model=dict[str, Any])
def delete_feedback(
    request: Request,
    feedback_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Undo a recorded feedback action (e.g. reinstate a dismissed
    recommendation).  Only the learner's own feedback may be removed."""
    database_path = _database_path(request)
    deleted = delete_recommendation_feedback(
        database_path,
        feedback_id=feedback_id,
        user_id=current_user.user_id,
    )
    if deleted is None:
        raise ApiError(404, "FEEDBACK_NOT_FOUND", "Feedback not found or access denied.")
    return {"feedbackId": feedback_id, "deleted": True}
