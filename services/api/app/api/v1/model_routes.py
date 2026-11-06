"""M57 AI model routing & reliability endpoints.

All endpoints require administrator authorization. Telemetry is privacy-safe:
no user IDs, document IDs, prompts, secrets, or raw model outputs are stored
or returned.
"""
# ruff: noqa: B008

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.auth import AuthUser, require_admin
from app.config import Settings
from app.errors import ApiError
from app.model_eval import ModelEvaluationError, run_evaluation
from app.model_observability import get_model_observability
from app.schemas import (
    ModelDiscoveryResponse,
    ModelEvaluationCreateRequest,
    ModelEvaluationResponse,
    ModelHealthCheckRequest,
    ModelHealthResponse,
    ModelMetricsSnapshotResponse,
)

router = APIRouter(prefix="/api/v1/admin/models", tags=["admin.models"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get(
    "/observability/snapshot",
    response_model=ModelMetricsSnapshotResponse,
)
def model_observability_snapshot(
    request: Request,
    current_user: AuthUser = Depends(require_admin),
) -> dict[str, Any]:
    """Return an aggregate snapshot of model task execution telemetry.

    Admin only. No user IDs, document IDs, prompts, secrets, or raw model
    outputs are exposed.
    """
    snapshot = get_model_observability().snapshot()
    return snapshot


@router.post(
    "/evaluate",
    response_model=ModelEvaluationResponse,
    status_code=200,
)
def run_model_evaluation(
    request: Request,
    payload: ModelEvaluationCreateRequest,
    current_user: AuthUser = Depends(require_admin),
    lexical_only: bool = Query(default=True),
) -> dict[str, Any]:
    """Run an offline quality evaluation and return the result.

    Admin only. Runs entirely locally against bundled datasets. No external
    model calls and no paid services are used.
    """
    try:
        result = run_evaluation(
            task_type=payload.task_type,
            dataset=payload.dataset,
            metric=payload.metric,
            lexical_only=lexical_only,
        )
    except ModelEvaluationError as exc:
        raise ApiError(503, "EVALUATION_ERROR", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface evaluation failure as 503
        raise ApiError(
            503,
            "EVALUATION_ERROR",
            "The quality evaluation could not complete.",
        ) from exc

    return {
        "task_type": payload.task_type,
        "dataset": payload.dataset,
        "metric": payload.metric,
        "value": result.get("failure_rate")
        if payload.metric in {"model_failure_rate", "model_timeout_rate"}
        else result.get("citation_validity_rate")
        if payload.metric == "citation_validity"
        else result.get("answerable_evidence_coverage")
        if payload.metric == "evidence_coverage"
        else result.get("metrics")
        if payload.metric == "retrieval_quality"
        else result.get("report")
        if payload.metric == "quiz_schema_validity"
        else None,
        "details": result,
        "evaluated_at": result.get("evaluated_at"),
    }


@router.get(
    "/models",
    response_model=ModelDiscoveryResponse,
)
def list_available_models(
    request: Request,
    current_user: AuthUser = Depends(require_admin),
) -> dict[str, Any]:
    """Return the list of models available from the local Ollama server.

    Admin only. No user IDs, document IDs, prompts, secrets, or raw model
    outputs are exposed.
    """
    settings = _settings(request)
    router = settings.model_router
    result = router.discover_models()
    return {
        "models": result.available_models,
        "available": result.available,
        "error": result.error,
    }


@router.get(
    "/models/health",
    response_model=ModelHealthResponse,
)
def model_health(
    request: Request,
    current_user: AuthUser = Depends(require_admin),
) -> dict[str, Any]:
    """Return health information for the configured model(s).

    Admin only. No user IDs, document IDs, prompts, secrets, or raw model
    outputs are exposed.
    """
    settings = _settings(request)
    router = settings.model_router
    model_id = request.query_params.get("model_id", settings.tutor_model_id)
    result = router.discover_models()
    return {
        "model_id": model_id,
        "available": result.available,
        "error": result.error,
    }


@router.post(
    "/models/health",
    response_model=ModelHealthResponse,
)
def model_health_check(
    request: Request,
    payload: ModelHealthCheckRequest,
    current_user: AuthUser = Depends(require_admin),
) -> dict[str, Any]:
    """Check the health of a specific model.

    Admin only. No user IDs, document IDs, prompts, secrets, or raw model
    outputs are exposed.
    """
    settings = _settings(request)
    router = settings.model_router
    model_id = payload.model_id or settings.tutor_model_id
    result = router.discover_models()
    return {
        "model_id": model_id,
        "available": result.available,
        "error": result.error,
    }