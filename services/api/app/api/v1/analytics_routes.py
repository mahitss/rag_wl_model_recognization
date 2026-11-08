"""M67: Predictive Learning Analytics & Student Performance Intelligence API routes.

Endpoints:

* GET    /api/v1/analytics/dashboard              - Performance intelligence dashboard
* GET    /api/v1/analytics/trends                 - Performance trends
* GET    /api/v1/analytics/snapshots              - Historical snapshots
* POST   /api/v1/analytics/snapshots              - Record snapshot
* POST   /api/v1/analytics/predictions            - Generate predictions
* GET    /api/v1/analytics/predictions            - List predictions
* POST   /api/v1/analytics/recommendations        - Generate recommendations
* GET    /api/v1/analytics/model-versions         - List model versions
* POST   /api/v1/analytics/model-versions         - Register model version
* GET    /api/v1/analytics/readiness              - Exam readiness

All endpoints require authentication and user-level data isolation.
"""
# ruff: noqa: B008,E501

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request

from app.auth import AuthUser, require_auth
from app.errors import ApiError
from app.predictive_analytics import (
    build_performance_dashboard,
    compute_performance_trends,
    generate_explainable_recommendations,
    generate_performance_predictions,
    record_analytics_snapshot,
)
from app.storage import (
    create_prediction_model_version,
    create_topic_trend,
    list_analytics_snapshots,
    list_performance_predictions,
    list_prediction_model_versions,
    list_topic_trends,
)

router = APIRouter(prefix="/api/v1", tags=["analytics", "predictions"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/analytics/dashboard", response_model=dict[str, Any])
def get_performance_dashboard(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the performance intelligence dashboard for the user."""
    return build_performance_dashboard(
        database_path=str(_database_path(request)),
        user_id=current_user.user_id,
        now=_now(),
    )


# ---------------------------------------------------------------------------
# Trends
# ---------------------------------------------------------------------------


@router.get("/analytics/trends", response_model=dict[str, Any])
def get_performance_trends(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    lookback_days: int = Query(default=90, ge=1, le=365),
) -> dict[str, Any]:
    """Return historical performance trends for the user."""
    return compute_performance_trends(
        database_path=str(_database_path(request)),
        user_id=current_user.user_id,
        now=_now(),
        lookback_days=lookback_days,
    )


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@router.get("/analytics/snapshots", response_model=dict[str, Any])
def list_analytics_snapshots_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    topic_id: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
) -> dict[str, Any]:
    """List historical analytics snapshots for the user."""
    snapshots = list_analytics_snapshots(
        str(_database_path(request)),
        user_id=current_user.user_id,
        topic_id=topic_id,
        start_date=start_date,
        end_date=end_date,
    )
    return {"snapshots": snapshots}


@router.post("/analytics/snapshots", response_model=dict[str, Any], status_code=201)
def record_analytics_snapshot_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record a point-in-time analytics snapshot."""
    snapshot = record_analytics_snapshot(
        database_path=str(_database_path(request)),
        user_id=current_user.user_id,
        now=_now(),
    )
    if snapshot is None:
        raise ApiError(422, "INVALID_REQUEST", "Could not record analytics snapshot.")
    return snapshot


# ---------------------------------------------------------------------------
# Predictions
# ---------------------------------------------------------------------------


@router.post(
    "/analytics/predictions",
    response_model=dict[str, Any],
    status_code=201,
)
def generate_predictions_route(
    request: Request,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Generate performance predictions for the user."""
    prediction_type = payload.get("predictionType")
    result = generate_performance_predictions(
        database_path=str(_database_path(request)),
        user_id=current_user.user_id,
        now=_now(),
    )
    if prediction_type is not None:
        result["predictions"] = [
            p for p in result["predictions"]
            if p["predictionType"] == prediction_type
        ]
        result["predictionCount"] = len(result["predictions"])
    return result


@router.get("/analytics/predictions", response_model=dict[str, Any])
def list_performance_predictions_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    prediction_type: str | None = Query(None),
) -> dict[str, Any]:
    """List stored performance predictions for the user."""
    predictions = list_performance_predictions(
        str(_database_path(request)),
        user_id=current_user.user_id,
        prediction_type=prediction_type,
    )
    return {"predictions": predictions}


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


@router.post(
    "/analytics/recommendations",
    response_model=dict[str, Any],
    status_code=201,
)
def generate_recommendations_route(
    request: Request,
    payload: dict[str, Any] = Body(default={}),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Generate explainable recommendations for the user."""
    limit = payload.get("limit", 10)
    return generate_explainable_recommendations(
        database_path=str(_database_path(request)),
        user_id=current_user.user_id,
        now=_now(),
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Model versions
# ---------------------------------------------------------------------------


@router.get("/analytics/model-versions", response_model=dict[str, Any])
def list_prediction_model_versions_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List prediction model versions (system-wide, read-only)."""
    models = list_prediction_model_versions(str(_database_path(request)))
    return {"models": models}


@router.post(
    "/analytics/model-versions",
    response_model=dict[str, Any],
    status_code=201,
)
def register_prediction_model_version_route(
    request: Request,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Register a new prediction model version."""
    model = create_prediction_model_version(
        str(_database_path(request)),
        model_name=payload.get("modelName", ""),
        version=payload.get("version", ""),
        description=payload.get("description"),
        is_active=payload.get("isActive", False),
        validation_metrics=payload.get("validationMetrics"),
    )
    if model is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid model version input.")
    return model


# ---------------------------------------------------------------------------
# Exam readiness
# ---------------------------------------------------------------------------


@router.get("/analytics/readiness", response_model=dict[str, Any])
def get_exam_readiness(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return exam readiness estimate based on actual evidence."""
    from app.predictive_analytics import _predict_exam_readiness

    result = _predict_exam_readiness(
        database_path=str(_database_path(request)),
        user_id=current_user.user_id,
        now=_now(),
    )
    if result is None:
        return {
            "userId": current_user.user_id,
            "readinessScore": None,
            "readinessLevel": "insufficient_evidence",
            "evidenceBased": False,
            "weakTopics": [],
            "recommendedActions": [
                "Complete more assessments to build evidence for readiness estimation."
            ],
            "calculatedAt": _now().isoformat(),
            "limitations": {
                "noCompetencyData": True,
                "note": (
                    "No competency mastery evidence exists. Readiness cannot be "
                    "estimated without assessment data."
                ),
            },
        }
    return {
        "userId": current_user.user_id,
        "readinessScore": result.get("predictedValue"),
        "readinessLevel": result.get("readinessLevel"),
        "evidenceBased": result.get("confidence", 0) > 0.5,
        "weakTopics": [],
        "recommendedActions": [
            f"Confidence: {result.get('confidence', 0):.0%} "
            f"({result.get('uncertainty', 'unknown')} uncertainty)"
        ],
        "calculatedAt": _now().isoformat(),
        "limitations": result.get("limitations", {}),
    }
