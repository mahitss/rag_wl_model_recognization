"""M71: Classroom Intelligence & Teaching Effectiveness Analytics API routes.

Endpoints:

* GET    /api/v1/classrooms/{session_id}/dashboard             - Performance dashboard
* GET    /api/v1/classrooms/{session_id}/topic-performance      - Topic performance
* GET    /api/v1/classrooms/{session_id}/competency-performance - Competency performance
* GET    /api/v1/classrooms/{session_id}/gap-trends             - Learning gap trends
* GET    /api/v1/classrooms/{session_id}/assessment-trends      - Assessment trends
* GET    /api/v1/classrooms/{session_id}/teaching-effectiveness - Teaching effectiveness
* GET    /api/v1/classrooms/{session_id}/recommendations        - Recommendations
* POST   /api/v1/classrooms/{session_id}/recommendations        - Create recommendation
* POST   /api/v1/classrooms/{session_id}/snapshot               - Record analytics snapshot
* GET    /api/v1/classrooms/{session_id}/snapshots              - List snapshots

All endpoints require authentication and session access (educator or participant).

Real-time transport: These endpoints use HTTP polling. There is no WebSocket
infrastructure in the codebase; clients poll for updated analytics. No real-time
WebSocket behavior is claimed.

Every metric includes its data source, measurement period, and sample size
where relevant. Predictions are explicitly labeled as estimates, not guarantees.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.auth import AuthUser, require_auth
from app.classroom_intelligence import (
    build_classroom_intelligence_dashboard,
    compute_teaching_effectiveness,
    generate_classroom_recommendations,
    record_classroom_analytics_snapshot,
)
from app.config import Settings
from app.errors import ApiError
from app.schemas import (
    ClassroomAnalyticsSnapshotListResponse,
    ClassroomAssessmentTrendListResponse,
    ClassroomCompetencyPerformanceListResponse,
    ClassroomIntelligenceDashboardResponse,
    ClassroomLearningGapTrendListResponse,
    ClassroomRecommendationListResponse,
    ClassroomTeachingEffectivenessListResponse,
    ClassroomTopicPerformanceListResponse,
)
from app.storage import (
    create_classroom_analytics_snapshot,
    create_classroom_recommendation,
    list_classroom_analytics_snapshots,
    list_classroom_assessment_trends,
    list_classroom_competency_performance,
    list_classroom_learning_gap_trends,
    list_classroom_recommendations,
    list_classroom_teaching_effectiveness,
    list_classroom_topic_performance,
)

router = APIRouter(prefix="/api/v1", tags=["classroom intelligence"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get(
    "/classrooms/{session_id}/dashboard",
    response_model=dict[str, Any],
)
def get_classroom_intelligence_dashboard(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the classroom intelligence dashboard for an educator.

    Combines performance analytics, teaching effectiveness, and recommendations
    into a single evidence-based view.
    """
    dashboard = build_classroom_intelligence_dashboard(
        database_path=str(_database_path(request)),
        session_id=session_id,
        user_id=current_user.user_id,
        now=_now(),
    )
    if dashboard is None:
        raise ApiError(
            404,
            "CLASSROOM_NOT_FOUND",
            "Classroom session not found or access denied.",
        )
    return dashboard


# ---------------------------------------------------------------------------
# Topic performance
# ---------------------------------------------------------------------------


@router.get(
    "/classrooms/{session_id}/topic-performance",
    response_model=dict[str, Any],
)
def get_classroom_topic_performance(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return topic performance for a classroom session."""
    topics = list_classroom_topic_performance(
        database_path=str(_database_path(request)),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if topics is None:
        raise ApiError(
            404,
            "CLASSROOM_NOT_FOUND",
            "Classroom session not found or access denied.",
        )
    return {"topicPerformance": topics}


# ---------------------------------------------------------------------------
# Competency performance
# ---------------------------------------------------------------------------


@router.get(
    "/classrooms/{session_id}/competency-performance",
    response_model=dict[str, Any],
)
def get_classroom_competency_performance(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return competency performance for a classroom session."""
    competencies = list_classroom_competency_performance(
        database_path=str(_database_path(request)),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if competencies is None:
        raise ApiError(
            404,
            "CLASSROOM_NOT_FOUND",
            "Classroom session not found or access denied.",
        )
    return {"competencyPerformance": competencies}


# ---------------------------------------------------------------------------
# Learning gap trends
# ---------------------------------------------------------------------------


@router.get(
    "/classrooms/{session_id}/gap-trends",
    response_model=dict[str, Any],
)
def get_classroom_learning_gap_trends(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return learning gap trends for a classroom session."""
    gaps = list_classroom_learning_gap_trends(
        database_path=str(_database_path(request)),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if gaps is None:
        raise ApiError(
            404,
            "CLASSROOM_NOT_FOUND",
            "Classroom session not found or access denied.",
        )
    return {"gapTrends": gaps}


# ---------------------------------------------------------------------------
# Assessment trends
# ---------------------------------------------------------------------------


@router.get(
    "/classrooms/{session_id}/assessment-trends",
    response_model=dict[str, Any],
)
def get_classroom_assessment_trends(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return assessment trends for a classroom session."""
    trends = list_classroom_assessment_trends(
        database_path=str(_database_path(request)),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if trends is None:
        raise ApiError(
            404,
            "CLASSROOM_NOT_FOUND",
            "Classroom session not found or access denied.",
        )
    return {"assessmentTrends": trends}


# ---------------------------------------------------------------------------
# Teaching effectiveness
# ---------------------------------------------------------------------------


@router.get(
    "/classrooms/{session_id}/teaching-effectiveness",
    response_model=dict[str, Any],
)
def get_classroom_teaching_effectiveness(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return teaching effectiveness indicators for a classroom session."""
    effectiveness = list_classroom_teaching_effectiveness(
        database_path=str(_database_path(request)),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if effectiveness is None:
        raise ApiError(
            404,
            "CLASSROOM_NOT_FOUND",
            "Classroom session not found or access denied.",
        )
    return {"teachingEffectiveness": effectiveness}


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


@router.get(
    "/classrooms/{session_id}/recommendations",
    response_model=dict[str, Any],
)
def get_classroom_recommendations(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return recommendations for a classroom session."""
    recommendations = list_classroom_recommendations(
        database_path=str(_database_path(request)),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if recommendations is None:
        raise ApiError(
            404,
            "CLASSROOM_NOT_FOUND",
            "Classroom session not found or access denied.",
        )
    return {"recommendations": recommendations}


@router.post(
    "/classrooms/{session_id}/recommendations",
    response_model=dict[str, Any],
    status_code=201,
)
def create_classroom_recommendation_route(
    request: Request,
    session_id: str = Path(...),
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a classroom recommendation. Educator only."""
    recommendation = create_classroom_recommendation(
        database_path=str(_database_path(request)),
        session_id=session_id,
        educator_id=current_user.user_id,
        recommendation_type=payload.get("recommendationType", ""),
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        rationale=payload.get("rationale"),
        priority_score=payload.get("priorityScore", 50.0),
        target_topic=payload.get("targetTopic"),
        target_competency=payload.get("targetCompetency"),
        evidence_summary=payload.get("evidenceSummary"),
    )
    if recommendation is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid recommendation input or session not found/owned.",
        )
    return recommendation


# ---------------------------------------------------------------------------
# Analytics snapshots
# ---------------------------------------------------------------------------


@router.post(
    "/classrooms/{session_id}/snapshot",
    response_model=dict[str, Any],
    status_code=201,
)
def record_classroom_analytics_snapshot_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record a point-in-time analytics snapshot for a classroom session."""
    snapshot = record_classroom_analytics_snapshot(
        database_path=str(_database_path(request)),
        session_id=session_id,
        user_id=current_user.user_id,
        now=_now(),
    )
    if snapshot is None:
        raise ApiError(
            404,
            "CLASSROOM_NOT_FOUND",
            "Classroom session not found or access denied.",
        )
    return snapshot


@router.get(
    "/classrooms/{session_id}/snapshots",
    response_model=dict[str, Any],
)
def list_classroom_analytics_snapshots_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List analytics snapshots for a classroom session."""
    snapshots = list_classroom_analytics_snapshots(
        database_path=str(_database_path(request)),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if snapshots is None:
        raise ApiError(
            404,
            "CLASSROOM_NOT_FOUND",
            "Classroom session not found or access denied.",
        )
    return {"snapshots": snapshots}
