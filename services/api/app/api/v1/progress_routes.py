"""M84: Real-Time Learning Progress & Engagement Intelligence API routes.

Endpoints:

* POST   /api/v1/progress/events              - Ingest a learning event
* POST   /api/v1/progress/events/batch        - Ingest multiple events
* GET    /api/v1/progress/events              - List learning events
* GET    /api/v1/progress/summary             - Progress summary with insights
* GET    /api/v1/progress/aggregates          - List progress aggregates
* POST   /api/v1/progress/aggregates          - Upsert a progress aggregate
* GET    /api/v1/progress/engagement          - Engagement indicators
* GET    /api/v1/progress/insights            - List learner insights
* POST   /api/v1/progress/insights/{id}/dismiss - Dismiss an insight
* GET    /api/v1/progress/insights/dismissals - List insight dismissals

All endpoints require authentication and tenant-scoped access.
"""
# ruff: noqa: B008,E501

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request

from app.auth import AuthUser, require_auth
from app.config import Settings
from app.errors import ApiError
from app.learning_progress_engine import (
    compute_progress_summary,
    ingest_event,
    ingest_events_batch,
)
from app.schemas import (
    InsightDismissalCreate,
    InsightDismissalResponse,
    LearnerInsightListResponse,
    LearnerInsightResponse,
    LearningEventCreate,
    LearningEventListResponse,
    LearningEventResponse,
    ProgressAggregateCreate,
    ProgressAggregateListResponse,
    ProgressAggregateResponse,
)
from app.storage import (
    dismiss_learner_insight,
    list_insight_dismissals,
    list_learner_insights,
    list_learning_events,
    list_progress_aggregates,
    upsert_progress_aggregate,
)

router = APIRouter(prefix="/api/v1/learner-progress", tags=["progress"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Event ingestion
# ---------------------------------------------------------------------------


@router.post("/events", response_model=LearningEventResponse, status_code=201)
def create_event(
    request: Request,
    payload: LearningEventCreate = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> LearningEventResponse:
    """Ingest a single learning event. Duplicate events (same idempotencyKey)
    are detected and the original is returned without a new write."""
    database_path = _database_path(request)
    return ingest_event(
        database_path,
        user_id=current_user.user_id,
        payload=payload,
        settings=_settings(request),
    )


@router.post(
    "/events/batch",
    response_model=list[LearningEventResponse],
    status_code=201,
)
def create_events_batch(
    request: Request,
    payload: list[LearningEventCreate] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> list[LearningEventResponse]:
    """Ingest multiple learning events. Each event is processed
    independently; duplicates are skipped."""
    if len(payload) > 100:
        raise ApiError(400, "BATCH_TOO_LARGE", "Batch size must not exceed 100.")
    database_path = _database_path(request)
    return ingest_events_batch(
        database_path,
        user_id=current_user.user_id,
        events=payload,
        settings=_settings(request),
    )


@router.get("/events", response_model=LearningEventListResponse)
def list_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    event_type: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    current_user: AuthUser = Depends(require_auth),
) -> LearningEventListResponse:
    """List learning events for the authenticated learner."""
    database_path = _database_path(request)
    rows = list_learning_events(
        database_path,
        user_id=current_user.user_id,
        limit=limit,
        offset=offset,
        event_type=event_type,
        start_date=start_date,
        end_date=end_date,
    )
    return LearningEventListResponse(
        events=[LearningEventResponse(**row) for row in rows]
    )


# ---------------------------------------------------------------------------
# Progress summary
# ---------------------------------------------------------------------------


@router.get("/summary", response_model=dict[str, Any])
def get_progress_summary(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get a real-time progress summary with insights for the learner."""
    database_path = _database_path(request)
    return compute_progress_summary(
        database_path, user_id=current_user.user_id, settings=_settings(request)
    ).model_dump()


# ---------------------------------------------------------------------------
# Progress aggregates
# ---------------------------------------------------------------------------


@router.get("/aggregates", response_model=ProgressAggregateListResponse)
def list_aggregates(
    request: Request,
    metric_type: str | None = Query(default=None),
    subject: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    current_user: AuthUser = Depends(require_auth),
) -> ProgressAggregateListResponse:
    """List progress aggregates for the authenticated learner."""
    database_path = _database_path(request)
    rows = list_progress_aggregates(
        database_path,
        user_id=current_user.user_id,
        metric_type=metric_type,
        subject=subject,
        limit=limit,
        offset=offset,
    )
    return ProgressAggregateListResponse(
        aggregates=[ProgressAggregateResponse(**row) for row in rows]
    )


@router.post(
    "/aggregates",
    response_model=ProgressAggregateResponse,
    status_code=201,
)
def create_aggregate(
    request: Request,
    payload: ProgressAggregateCreate = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> ProgressAggregateResponse:
    """Create or update a progress aggregate (idempotent per metric)."""
    database_path = _database_path(request)
    stored = upsert_progress_aggregate(
        database_path,
        user_id=current_user.user_id,
        metric_type=payload.metricType,
        value=payload.value,
        unit=payload.unit,
        computed_at=payload.computedAt,
        data_as_of=payload.dataAsOf,
        subject=payload.subject,
        topic_id=payload.topicId,
        metric_subtype=payload.metricSubtype,
        details=payload.details,
    )
    return ProgressAggregateResponse(**stored)


# ---------------------------------------------------------------------------
# Engagement indicators
# ---------------------------------------------------------------------------


@router.get("/engagement", response_model=dict[str, Any])
def get_engagement(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get engagement indicators for the authenticated learner."""
    database_path = _database_path(request)
    return compute_progress_summary(
        database_path, user_id=current_user.user_id, settings=_settings(request)
    ).model_dump()


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


@router.get("/insights", response_model=LearnerInsightListResponse)
def list_insights(
    request: Request,
    insight_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: AuthUser = Depends(require_auth),
) -> LearnerInsightListResponse:
    """List learner insights for the authenticated learner."""
    database_path = _database_path(request)
    rows = list_learner_insights(
        database_path,
        user_id=current_user.user_id,
        insight_type=insight_type,
        status=status,
        limit=limit,
        offset=offset,
    )
    return LearnerInsightListResponse(
        insights=[LearnerInsightResponse(**row) for row in rows]
    )


@router.post(
    "/insights/{insight_id}/dismiss",
    response_model=InsightDismissalResponse,
    status_code=201,
)
def dismiss_insight(
    request: Request,
    insight_id: str,
    payload: InsightDismissalCreate = Body(default={}),
    current_user: AuthUser = Depends(require_auth),
) -> InsightDismissalResponse:
    """Dismiss a learner insight."""
    database_path = _database_path(request)
    stored = dismiss_learner_insight(
        database_path,
        insight_id=insight_id,
        user_id=current_user.user_id,
        reason=payload.reason,
    )
    if stored is None:
        raise ApiError(404, "INSIGHT_NOT_FOUND", "Insight not found or not active.")
    return InsightDismissalResponse(
        insightId=stored["insightId"],
        userId=stored["userId"],
        tenantId=stored["tenantId"],
        status=stored["status"],
        dismissedAt=stored["dismissedAt"],
    )


@router.get("/insights/dismissals", response_model=list[dict[str, Any]])
def list_dismissals(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> list[dict[str, Any]]:
    """List insight dismissals for the authenticated learner."""
    database_path = _database_path(request)
    return list_insight_dismissals(
        database_path, user_id=current_user.user_id
    )


# ---------------------------------------------------------------------------
# Combined progress + insights
# ---------------------------------------------------------------------------


@router.get("/progress-with-insights", response_model=dict[str, Any])
def get_progress_with_insights(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get progress summary with current insights in one call."""
    database_path = _database_path(request)
    return compute_progress_summary(
        database_path, user_id=current_user.user_id, settings=_settings(request)
    )
