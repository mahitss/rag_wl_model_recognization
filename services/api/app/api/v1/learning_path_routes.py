"""M82: AI Learning Path Generator & Dynamic Curriculum Orchestration API routes.

Endpoints:

* POST   /api/v1/learning-paths/generate          - Generate a new learning path
* GET    /api/v1/learning-paths/{path_id}/versions - List path versions
* GET    /api/v1/learning-paths/{path_id}/modules  - List path modules
* POST   /api/v1/learning-paths/{path_id}/modules  - Create a module
* GET    /api/v1/learning-paths/{path_id}/milestones - List milestones
* POST   /api/v1/learning-paths/{path_id}/milestones - Create a milestone
* GET    /api/v1/learning-paths/{path_id}/progress - List progress
* POST   /api/v1/learning-paths/{path_id}/progress - Record progress
* POST   /api/v1/learning-paths/{path_id}/adapt    - Adapt the path
* GET    /api/v1/learning-paths/{path_id}/adaptations - List adaptation history

All endpoints require authentication and tenant-scoped access.
"""
# ruff: noqa: B008,E501

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request

from app.auth import AuthUser, require_auth
from app.config import Settings
from app.errors import ApiError
from app.learning_path_generator import (
    adapt_learning_path,
    create_path_milestone,
    create_path_module,
    generate_learning_path,
    list_path_adaptations,
    list_path_milestones,
    list_path_modules,
    list_path_progress,
    list_path_versions,
    upsert_path_progress,
)
from app.schemas import (
    LearningPathAdaptationCreate,
    LearningPathAdaptationListResponse,
    LearningPathAdaptationResponse,
    LearningPathGenerateRequest,
    LearningPathGenerationResult,
    LearningPathMilestoneCreate,
    LearningPathMilestoneListResponse,
    LearningPathMilestoneResponse,
    LearningPathModuleCreate,
    LearningPathModuleListResponse,
    LearningPathModuleResponse,
    LearningPathProgressCreate,
    LearningPathProgressListResponse,
    LearningPathProgressResponse,
    LearningPathVersionResponse,
)
from app.storage import (
    get_learning_path,
    list_learning_path_versions,
)

router = APIRouter(prefix="/api/v1/learning-paths", tags=["learning paths"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _require_path_ownership(
    database_path, path_id: str, user_id: str
) -> None:
    path = get_learning_path(database_path, path_id)
    if path is None:
        raise ApiError(404, "LEARNING_PATH_NOT_FOUND", "Learning path was not found.")
    if path["userId"] != user_id:
        raise ApiError(403, "LEARNING_PATH_FORBIDDEN", "You do not own this path.")


# ---------------------------------------------------------------------------
# Path generation
# ---------------------------------------------------------------------------


@router.post(
    "/generate",
    response_model=LearningPathGenerationResult,
    status_code=201,
)
def generate_learning_path_route(
    request: Request,
    payload: LearningPathGenerateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> LearningPathGenerationResult:
    """Generate a prerequisite-aware learning path for the authenticated
    learner.

    The path is built from knowledge-graph concept dependencies, learner
    mastery state, and available study time.  No LLM is used; generation is
    fully deterministic.
    """
    database_path = _database_path(request)
    return generate_learning_path(
        database_path,
        user_id=current_user.user_id,
        settings=_settings(request),
        request=payload,
    )


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


@router.get("/{path_id}/versions", response_model=list[LearningPathVersionResponse])
def list_path_versions_route(
    request: Request,
    path_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> list[LearningPathVersionResponse]:
    """List all versions of a learning path."""
    _require_path_ownership(_database_path(request), path_id, current_user.user_id)
    return list_path_versions(
        _database_path(request), path_id=path_id, user_id=current_user.user_id
    )


# ---------------------------------------------------------------------------
# Modules
# ---------------------------------------------------------------------------


@router.get("/{path_id}/modules", response_model=LearningPathModuleListResponse)
def list_path_modules_route(
    request: Request,
    path_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> LearningPathModuleListResponse:
    """List modules for a learning path."""
    _require_path_ownership(_database_path(request), path_id, current_user.user_id)
    return list_path_modules(
        _database_path(request), path_id=path_id, user_id=current_user.user_id
    )


@router.post(
    "/{path_id}/modules",
    response_model=LearningPathModuleResponse,
    status_code=201,
)
def create_path_module_route(
    request: Request,
    path_id: str,
    payload: LearningPathModuleCreate = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> LearningPathModuleResponse:
    """Create a module within a learning path."""
    _require_path_ownership(_database_path(request), path_id, current_user.user_id)

    versions = list_learning_path_versions(
        _database_path(request), path_id=path_id, user_id=current_user.user_id
    )
    current_version = None
    for v in versions:
        if v["isCurrent"]:
            current_version = v
            break
    if current_version is None:
        raise ApiError(404, "PATH_VERSION_NOT_FOUND", "No current version found.")

    return create_path_module(
        _database_path(request),
        path_id=path_id,
        user_id=current_user.user_id,
        version_id=current_version["versionId"],
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------


@router.get("/{path_id}/milestones", response_model=LearningPathMilestoneListResponse)
def list_path_milestones_route(
    request: Request,
    path_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> LearningPathMilestoneListResponse:
    """List milestones for a learning path."""
    _require_path_ownership(_database_path(request), path_id, current_user.user_id)
    return list_path_milestones(
        _database_path(request), path_id=path_id, user_id=current_user.user_id
    )


@router.post(
    "/{path_id}/milestones",
    response_model=LearningPathMilestoneResponse,
    status_code=201,
)
def create_path_milestone_route(
    request: Request,
    path_id: str,
    payload: LearningPathMilestoneCreate = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> LearningPathMilestoneResponse:
    """Create a milestone within a learning path."""
    _require_path_ownership(_database_path(request), path_id, current_user.user_id)

    versions = list_learning_path_versions(
        _database_path(request), path_id=path_id, user_id=current_user.user_id
    )
    current_version = None
    for v in versions:
        if v["isCurrent"]:
            current_version = v
            break
    if current_version is None:
        raise ApiError(404, "PATH_VERSION_NOT_FOUND", "No current version found.")

    return create_path_milestone(
        _database_path(request),
        path_id=path_id,
        user_id=current_user.user_id,
        version_id=current_version["versionId"],
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


@router.get("/{path_id}/progress", response_model=LearningPathProgressListResponse)
def list_path_progress_route(
    request: Request,
    path_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> LearningPathProgressListResponse:
    """List progress records for a learning path."""
    _require_path_ownership(_database_path(request), path_id, current_user.user_id)
    return list_path_progress(
        _database_path(request), path_id=path_id, user_id=current_user.user_id
    )


@router.post(
    "/{path_id}/progress",
    response_model=LearningPathProgressResponse,
    status_code=201,
)
def record_path_progress_route(
    request: Request,
    path_id: str,
    payload: LearningPathProgressCreate = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> LearningPathProgressResponse:
    """Record or update progress for a module/objective within a path.

    Completed progress is preserved across adaptations.
    """
    _require_path_ownership(_database_path(request), path_id, current_user.user_id)
    return upsert_path_progress(
        _database_path(request),
        path_id=path_id,
        user_id=current_user.user_id,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Adaptation
# ---------------------------------------------------------------------------


@router.post(
    "/{path_id}/adapt",
    response_model=LearningPathAdaptationResponse,
    status_code=201,
)
def adapt_path_route(
    request: Request,
    path_id: str,
    payload: LearningPathAdaptationCreate = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> LearningPathAdaptationResponse:
    """Adapt a learning path in response to a learner event.

    Supported triggers: module_completed, mastery_changed, prerequisite_gap,
    schedule_fell_behind, target_changed, resource_unavailable.
    """
    _require_path_ownership(_database_path(request), path_id, current_user.user_id)

    versions = list_learning_path_versions(
        _database_path(request), path_id=path_id, user_id=current_user.user_id
    )
    current_version = None
    for v in versions:
        if v["isCurrent"]:
            current_version = v
            break
    if current_version is None:
        raise ApiError(404, "PATH_VERSION_NOT_FOUND", "No current version found.")

    return adapt_learning_path(
        _database_path(request),
        path_id=path_id,
        user_id=current_user.user_id,
        trigger=payload.trigger,
        description=payload.description,
        settings=_settings(request),
    )


@router.get("/{path_id}/adaptations", response_model=LearningPathAdaptationListResponse)
def list_path_adaptations_route(
    request: Request,
    path_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> LearningPathAdaptationListResponse:
    """List adaptation history for a learning path."""
    _require_path_ownership(_database_path(request), path_id, current_user.user_id)
    return list_path_adaptations(
        _database_path(request), path_id=path_id, user_id=current_user.user_id
    )
