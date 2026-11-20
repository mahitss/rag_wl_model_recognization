"""M85: AI Learning Behavior Intelligence & Personalized Intervention API routes.

Endpoints:

* GET    /api/v1/learner-behavior/analyze      - Full behavior analysis
* GET    /api/v1/learner-behavior/state        - Learner state snapshot
* GET    /api/v1/learner-behavior/patterns     - List behavior patterns
* POST   /api/v1/learner-behavior/interventions - Generate interventions
* GET    /api/v1/learner-behavior/interventions - List interventions
* POST   /api/v1/learner-behavior/interventions/{id}/respond - Respond to intervention
* GET    /api/v1/learner-behavior/interventions/{id}/outcomes - List outcomes
* POST   /api/v1/learner-behavior/interventions/{id}/outcomes - Record outcome
* GET    /api/v1/learner-behavior/progress-with-insights - Progress + insights (M84 re-export)

All endpoints require authentication and tenant-scoped access.
"""
# ruff: noqa: B008,E501

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.auth import AuthUser, require_auth
from app.config import Settings
from app.errors import ApiError
from app.learning_behavior_engine import (
    analyze_learner_behavior,
    compute_learner_state,
    generate_interventions,
)
from app.learning_progress_engine import get_progress_with_insights
from app.schemas import (
    BehaviorPatternListResponse,
    BehaviorPatternResponse,
    InterventionListResponse,
    InterventionOutcomeCreate,
    InterventionOutcomeListResponse,
    InterventionOutcomeResponse,
    InterventionRespond,
    InterventionResponse,
    LearnerBehaviorAnalysisResult,
    LearnerStateResponse,
)
from app.storage import (
    list_behavior_patterns,
    list_intervention_outcomes,
    list_interventions,
    record_intervention_outcome,
    respond_to_intervention,
)

router = APIRouter(prefix="/api/v1/learner-behavior", tags=["learner-behavior"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _settings(request: Request) -> Settings:
    return request.app.state.settings


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


@router.get("/analyze", response_model=LearnerBehaviorAnalysisResult)
def analyze_learner_behavior_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> LearnerBehaviorAnalysisResult:
    """Run the full behavior analysis pipeline for the authenticated learner.

    Returns behavior patterns, learner state, and recommended interventions.
    """
    database_path = _database_path(request)
    return analyze_learner_behavior(
        database_path,
        user_id=current_user.user_id,
        settings=_settings(request),
    )


# ---------------------------------------------------------------------------
# Learner state
# ---------------------------------------------------------------------------


@router.get("/state", response_model=LearnerStateResponse)
def get_learner_state_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> LearnerStateResponse:
    """Get the current learner state snapshot."""
    database_path = _database_path(request)
    return compute_learner_state(
        database_path, user_id=current_user.user_id, settings=_settings(request)
    )


# ---------------------------------------------------------------------------
# Behavior patterns
# ---------------------------------------------------------------------------


@router.get("/patterns", response_model=BehaviorPatternListResponse)
def list_patterns_route(
    request: Request,
    is_active: bool | None = Query(default=None),
    pattern_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: AuthUser = Depends(require_auth),
) -> BehaviorPatternListResponse:
    """List behavior patterns for the authenticated learner."""
    database_path = _database_path(request)
    rows = list_behavior_patterns(
        database_path,
        user_id=current_user.user_id,
        is_active=is_active,
        pattern_type=pattern_type,
        limit=limit,
        offset=offset,
    )
    return BehaviorPatternListResponse(
        patterns=[BehaviorPatternResponse(**row) for row in rows]
    )


# ---------------------------------------------------------------------------
# Interventions
# ---------------------------------------------------------------------------


@router.post("/interventions", response_model=list[InterventionResponse])
def generate_interventions_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> list[InterventionResponse]:
    """Generate intervention proposals from behavior patterns and learner state.

    Interventions are proposals only; they do not modify learner records.
    """
    database_path = _database_path(request)
    return generate_interventions(
        database_path,
        user_id=current_user.user_id,
        settings=_settings(request),
    )


@router.get("/interventions", response_model=InterventionListResponse)
def list_interventions_route(
    request: Request,
    status: str | None = Query(default=None),
    intervention_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: AuthUser = Depends(require_auth),
) -> InterventionListResponse:
    """List interventions for the authenticated learner."""
    database_path = _database_path(request)
    rows = list_interventions(
        database_path,
        user_id=current_user.user_id,
        status=status,
        intervention_type=intervention_type,
        limit=limit,
        offset=offset,
    )
    return InterventionListResponse(
        interventions=[InterventionResponse(**row) for row in rows]
    )


@router.post("/interventions/{intervention_id}/respond")
def respond_to_intervention_route(
    request: Request,
    intervention_id: str = Path(...),
    payload: InterventionRespond = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Respond to an intervention: accept, dismiss, or complete."""
    database_path = _database_path(request)
    result = respond_to_intervention(
        database_path,
        intervention_id=intervention_id,
        user_id=current_user.user_id,
        response=payload.response,
    )
    if result is None:
        raise ApiError(
            404,
            "INTERVENTION_NOT_FOUND",
            "Intervention not found or not in proposed state.",
        )
    return result


# ---------------------------------------------------------------------------
# Intervention outcomes
# ---------------------------------------------------------------------------


@router.get("/interventions/{intervention_id}/outcomes", response_model=InterventionOutcomeListResponse)
def list_outcomes_route(
    request: Request,
    intervention_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> InterventionOutcomeListResponse:
    """List outcomes for an intervention."""
    database_path = _database_path(request)
    rows = list_intervention_outcomes(
        database_path, user_id=current_user.user_id
    )
    # Filter to this intervention
    rows = [r for r in rows if r["interventionId"] == intervention_id]
    return InterventionOutcomeListResponse(
        outcomes=[InterventionOutcomeResponse(**row) for row in rows]
    )


@router.post("/interventions/{intervention_id}/outcomes", response_model=InterventionOutcomeResponse)
def record_outcome_route(
    request: Request,
    intervention_id: str = Path(...),
    payload: InterventionOutcomeCreate = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> InterventionOutcomeResponse:
    """Record the outcome of an intervention."""
    database_path = _database_path(request)
    result = record_intervention_outcome(
        database_path,
        intervention_id=intervention_id,
        user_id=current_user.user_id,
        outcome_type=payload.outcomeType,
        mastered_before=payload.masteredBefore,
        mastered_after=payload.masteredAfter,
        evidence_event_ids=payload.evidenceEventIds,
        notes=payload.notes,
    )
    return InterventionOutcomeResponse(**result)


# ---------------------------------------------------------------------------
# Progress + insights (re-export from M84 for convenience)
# ---------------------------------------------------------------------------


@router.get("/progress-with-insights", response_model=dict[str, Any])
def get_progress_with_insights_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get progress summary with current insights (re-exported from M84)."""
    database_path = _database_path(request)
    return get_progress_with_insights(
        database_path, user_id=current_user.user_id, settings=_settings(request)
    )
