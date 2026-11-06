# ruff: noqa: B008,E501
"""M60: Unified Learning Journey & Experience Orchestration API routes.

Endpoints:

* GET   /api/v1/journey/dashboard        - Full unified dashboard view
* GET   /api/v1/journey/overview         - Lightweight journey overview
* GET   /api/v1/journey/next-action      - Recommended next action
* GET   /api/v1/journey/stage-progression - Journey stage completion details

All endpoints require authentication.  Every metric is derived from persisted
data or explicitly labelled as an estimate.  No cross-user data leakage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.v1.routes import _database_call, _database_path, _settings
from app.auth import AuthUser, require_auth
from app.journey import (
    build_journey_dashboard,
    build_journey_overview,
    build_next_action,
)

router = APIRouter(prefix="/api/v1/journey", tags=["journey"])


@router.get(
    "/dashboard",
    response_model=dict[str, Any],
    tags=["journey"],
)
def journey_dashboard(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the unified learner journey dashboard for the authenticated user.

    Aggregates all persisted subsystems (analytics, revision queue, study coach
    plan, learning paths, documents, images, knowledge graph concepts, content
    resources) into a single view with journey stage progression, dashboard
    widgets, and a recommended next action.
    """
    settings = _settings(request)
    database_path = str(_database_path(request))
    now = datetime.now(UTC)

    return _database_call(
        build_journey_dashboard,
        settings=settings,
        database_path=database_path,
        user_id=current_user.user_id,
        now=now,
    )


@router.get(
    "/overview",
    response_model=dict[str, Any],
    tags=["journey"],
)
def journey_overview(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return a lightweight journey overview (no full subsystem details)."""
    settings = _settings(request)
    database_path = str(_database_path(request))
    now = datetime.now(UTC)

    return _database_call(
        build_journey_overview,
        settings=settings,
        database_path=database_path,
        user_id=current_user.user_id,
        now=now,
    )


@router.get(
    "/next-action",
    response_model=dict[str, Any] | None,
    tags=["journey"],
)
def journey_next_action(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any] | None:
    """Return the single recommended next action for the learner.

    The action is derived from journey stage completion, overdue reviews,
    weak topics, and study coach recommendations.
    """
    settings = _settings(request)
    database_path = str(_database_path(request))
    now = datetime.now(UTC)

    return _database_call(
        build_next_action,
        settings=settings,
        database_path=database_path,
        user_id=current_user.user_id,
        now=now,
    )


@router.get(
    "/stage-progression",
    response_model=dict[str, Any],
    tags=["journey"],
)
def journey_stage_progression(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return journey stage completion details for the authenticated user.

    Shows which of the ten journey stages are complete, what evidence
    supports each completion, and the current stage.
    """
    database_path = str(_database_path(request))
    now = datetime.now(UTC)

    from app.journey import _compute_stage_completion
    from app.learning_analytics import build_analytics
    from app.storage import (
        get_revision_stats,
        get_weak_topics,
        list_content_resources,
        list_documents,
        list_knowledge_concepts,
        list_learning_images,
        list_learning_paths,
    )

    data = _database_call(
        __import__("app.storage", fromlist=["get_learning_analytics_data"]).get_learning_analytics_data,
        database_path,
        user_id=current_user.user_id,
    )
    analytics = build_analytics(
        attempts=data["attempts"],
        sessions=data["sessions"],
        reviews=data["reviews"],
        topic_sources=data["topicSources"],
        concepts=data["concepts"],
        now=now,
    )

    stage_completion = _compute_stage_completion(
        analytics=analytics,
        documents=list_documents(database_path, user_id=current_user.user_id),
        images=list_learning_images(database_path, user_id=current_user.user_id),
        paths=list_learning_paths(database_path, user_id=current_user.user_id),
        concepts=list_knowledge_concepts(database_path, user_id=current_user.user_id),
        content_resources=list_content_resources(database_path, user_id=current_user.user_id),
        revision_stats=get_revision_stats(database_path, user_id=current_user.user_id),
        weak_topics=get_weak_topics(database_path, user_id=current_user.user_id),
        now=now,
    )
    completed = sum(1 for s in stage_completion if s["completed"])
    total = len(stage_completion)

    return {
        "generatedAt": now.isoformat(),
        "userId": current_user.user_id,
        "completionPercent": round((completed / total) * 100) if total else 0,
        "completedStages": completed,
        "totalStages": total,
        "stages": stage_completion,
    }
