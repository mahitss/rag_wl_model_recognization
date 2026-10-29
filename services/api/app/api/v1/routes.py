"""Versioned system, document, retrieval, and evidence-only answer routes."""
# ruff: noqa: B008,E501

import asyncio
import hashlib
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.auth import AuthUser, get_current_user, require_admin, require_auth
from app.config import Settings
from app.diagrams.catalog import DIAGRAMS, get_diagram, resolve_region
from app.embeddings.service import EmbeddingService, ModelUnavailable
from app.errors import ApiError
from app.ingestion.image import (
    normalize_image,
    recognize_region,
    recognize_text,
    safe_image_filename,
)
from app.ingestion.pdf import ingest_pdf
from app.knowledge_graph import extract_chunk
from app.learning_analytics import build_analytics
from app.observability import OperationalMetrics
from app.quiz import evaluate_answer, generate_quiz_questions
from app.retrieval.service import answer_from_evidence, retrieve_with_mode
from app.schemas import (
    AdaptiveRecommendationsResponse,
    AnswerRequest,
    AnswerResponse,
    DeleteDocumentResponse,
    DiagramConceptResponse,
    DiagramEvidenceRequest,
    DiagramEvidenceResponse,
    DiagramGeometry,
    DiagramRegionResponse,
    DiagramResponse,
    DiagramsResponse,
    DocumentResponse,
    DocumentsResponse,
    EmbeddingStatusResponse,
    FeedbackAdminStatusRequest,
    FeedbackAdminUpdateRequest,
    FeedbackCreateRequest,
    FeedbackListResponse,
    FeedbackUpdateRequest,
    HealthResponse,
    ImageEvidenceResponse,
    ImageRegionCreateRequest,
    ImageRegionCreateResponse,
    ImageTutorRequest,
    ImageTutorResponse,
    ImprovementTaskCreateRequest,
    ImprovementTaskListResponse,
    ImprovementTaskResponse,
    ImprovementTaskUpdateRequest,
    KnowledgeGraphConceptDetail,
    KnowledgeGraphConceptList,
    KnowledgeGraphExtractRequest,
    KnowledgeGraphExtractResponse,
    LearningAnalyticsResponse,
    LearningImageDetailResponse,
    LearningImageListResponse,
    LearningObjectiveCreateRequest,
    LearningObjectiveListResponse,
    LearningObjectiveResponse,
    LearningObjectiveUpdateRequest,
    LearningPathCreateRequest,
    LearningPathListResponse,
    LearningPathResponse,
    LearningPathUpdateRequest,
    LearningProgressResponse,
    LinkObjectiveActivityRequest,
    LinkObjectiveEvidenceRequest,
    PrioritizationExplanation,
    ProductAnalyticsEventRequest,
    ProductAnalyticsSummary,
    QuizAttemptRequest,
    QuizAttemptResponse,
    QuizGenerateRequest,
    QuizSetResponse,
    ReadinessResponse,
    RebuildEmbeddingsRequest,
    RebuildEmbeddingsResponse,
    RetrievalRequest,
    RetrievalResponse,
    RevisionQueueResponse,
    StudyCoachActionRequest,
    StudyCoachExplanationResponse,
    StudyCoachPlanResponse,
    StudyCoachRecommendation,
    StudySessionRequest,
    StudySessionResponse,
    TutorAnswerRequest,
    TutorAnswerResponse,
    TutorStatusResponse,
    UnifiedSearchRequest,
    UnifiedSearchResponse,
)
from app.storage import (
    complete_study_session,
    compute_adaptive_recommendations,
    create_feedback_item,
    create_improvement_task,
    create_learning_image_region,
    create_learning_objective,
    create_learning_path,
    create_quiz_set,
    delete_document,
    delete_learning_image,
    delete_learning_objective,
    delete_learning_path,
    get_document,
    get_knowledge_concept,
    get_learning_analytics_data,
    get_learning_image,
    get_learning_image_detail,
    get_learning_image_region,
    get_learning_objective,
    get_learning_path,
    get_learning_progress,
    get_or_create_study_coach_plan,
    get_product_analytics_snapshot,
    get_quiz_question,
    get_quiz_set,
    get_revision_queue,
    knowledge_graph_chunks,
    link_feedback_to_task,
    link_objective_activity,
    link_objective_evidence,
    list_documents,
    list_feedback_for_admin,
    list_feedback_for_user,
    list_improvement_tasks,
    list_knowledge_concepts,
    list_learning_images,
    list_learning_objectives,
    list_learning_paths,
    prune_product_events,
    record_flashcard_review,
    record_product_event,
    record_quiz_attempt,
    record_quiz_review,
    replace_image_region_citations,
    search_content_sections,
    search_knowledge_concepts,
    search_learning_objectives,
    search_quiz_questions,
    start_study_session,
    store_knowledge_extraction,
    store_learning_image,
    unlink_feedback_from_task,
    update_feedback_item,
    update_feedback_item_admin,
    update_improvement_task,
    update_learning_objective,
    update_learning_path,
    update_study_coach_recommendation,
    verify_knowledge_relationship,
)
from app.study_coach import build_daily_recommendations
from app.tutor import InvalidTutorResponse, TutorUnavailable, generate_grounded_answer

router = APIRouter(prefix="/api/v1")


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _database_path(request: Request) -> Path:
    return _settings(request).database_path


def _embedding_service(request: Request) -> EmbeddingService:
    return request.app.state.embedding_service


def _current_user(request: Request) -> AuthUser | None:
    """Return the authenticated user, or None if not authenticated."""
    return get_current_user(request)


def _require_user(request: Request) -> AuthUser:
    """Require an authenticated user, raising 401 if not authenticated."""
    return require_auth(request)


def _safe_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "documentId": document["document_id"],
        "displayFilename": document["display_name"],
        "sha256": document["sha256"],
        "byteSize": document["byte_size"],
        "pageCount": document["page_count"],
        "chunkCount": document["chunk_count"],
        "createdAt": document["created_at"],
        "parserVersion": document["parser_version"],
        "status": "indexed",
    }


def _database_call(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except ApiError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc


async def _grounded_tutor_response(
    settings: Settings,
    *,
    question: str,
    concept: str | None,
    evidence: list[dict[str, Any]],
    retrieval_mode: str,
    fallback_reason: str | None,
) -> dict[str, Any]:
    if not evidence:
        return {
            "status": "insufficient_evidence",
            "answer": None,
            "citations": [],
            "retrievalMode": retrieval_mode,
            "fallbackReason": fallback_reason,
        }
    try:
        generated = await asyncio.to_thread(
            generate_grounded_answer,
            settings,
            question=question,
            concept=concept,
            evidence=evidence,
        )
    except TutorUnavailable as exc:
        raise ApiError(
            503,
            "TUTOR_MODEL_UNAVAILABLE",
            "The optional local tutor is not configured or could not be reached.",
        ) from exc
    except InvalidTutorResponse as exc:
        raise ApiError(
            502,
            "TUTOR_INVALID_RESPONSE",
            "The local tutor returned an invalid response. Try again or check its status.",
        ) from exc
    return {
        "status": "answered" if generated["answer"] is not None else "insufficient_evidence",
        "answer": generated["answer"],
        "citations": generated["citations"],
        "retrievalMode": retrieval_mode,
        "fallbackReason": fallback_reason,
    }


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(request: Request) -> dict[str, Any]:
    settings = _settings(request)
    return {
        "status": "ok",
        "service": "wonderlearn-api",
        "version": "0.1.0",
        "environment": settings.environment,
        "capabilities": {
            "document_ingestion": True,
            "retrieval": True,
            "model_inference": settings.tutor_configured,
        },
    }


@router.post("/feedback", response_model=dict[str, Any], tags=["beta"])
def create_feedback(
    request: Request,
    payload: FeedbackCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a private beta feedback report for the authenticated account."""
    item = create_feedback_item(
        _database_path(request),
        user_id=current_user.user_id,
        category=payload.category,
        title=payload.title.strip(),
        description=payload.description.strip(),
    )
    return {
        "feedback": item,
        "message": "Your feedback was saved.",
    }


@router.get("/feedback", response_model=FeedbackListResponse, tags=["beta"])
def list_feedback(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, list[dict[str, Any]]]:
    """List the current user's feedback submissions."""
    return {"feedback": list_feedback_for_user(_database_path(request), current_user.user_id)}


@router.patch("/feedback/{feedback_id}", response_model=dict[str, Any], tags=["beta"])
def update_feedback(
    request: Request,
    feedback_id: str,
    payload: FeedbackUpdateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Allow an owner to update or withdraw their own feedback when suitable."""
    updates: dict[str, Any] = {}
    if payload.category is not None:
        updates["category"] = payload.category
    if payload.title is not None:
        updates["title"] = payload.title.strip()
    if payload.description is not None:
        updates["description"] = payload.description.strip()
    if payload.status is not None:
        updates["status"] = payload.status

    if not updates:
        raise ApiError(400, "EMPTY_FEEDBACK_UPDATE", "No updates were provided.")

    try:
        existing = update_feedback_item(
            _database_path(request),
            feedback_id=feedback_id,
            user_id=current_user.user_id,
            **updates,
        )
    except PermissionError as exc:
        raise ApiError(403, "FEEDBACK_FORBIDDEN", "You do not own this feedback item.") from exc
    if existing is None:
        raise ApiError(404, "FEEDBACK_NOT_FOUND", "Feedback item was not found.")
    return {"feedback": existing, "message": "Feedback updated."}


@router.get("/feedback/admin", response_model=FeedbackListResponse, tags=["beta"])
def list_feedback_admin(
    request: Request,
    _: AuthUser = Depends(require_admin),
    category: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort_by: str = "recency",
    sort_order: str = "desc",
) -> dict[str, list[dict[str, Any]]]:
    """List all feedback for private-beta review by administrators.

    Supports filtering by category, status, and date range, and sorting by
    recency or creation time.  All values are measured from stored rows only.
    """
    return {
        "feedback": list_feedback_for_admin(
            _database_path(request),
            category=category,
            status=status,
            date_from=date_from,
            date_to=date_to,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    }


@router.patch(
    "/feedback/admin/{feedback_id}",
    response_model=dict[str, Any],
    tags=["beta"],
)
def update_feedback_admin(
    request: Request,
    feedback_id: str,
    payload: FeedbackAdminUpdateRequest,
    _: AuthUser = Depends(require_admin),
) -> dict[str, Any]:
    """Administrator update of a feedback item.

    Allows updating category, title, description, status, resolution notes,
    and linking to an improvement task.  No ownership check is applied;
    caller must be an administrator.
    """
    updates: dict[str, Any] = {}
    if payload.category is not None:
        updates["category"] = payload.category
    if payload.title is not None:
        updates["title"] = payload.title
    if payload.description is not None:
        updates["description"] = payload.description
    if payload.status is not None:
        updates["status"] = payload.status
    if payload.resolution_notes is not None:
        updates["resolution_notes"] = payload.resolution_notes
    if payload.improvement_task_id is not None:
        updates["improvement_task_id"] = payload.improvement_task_id

    if not updates:
        raise ApiError(400, "EMPTY_FEEDBACK_UPDATE", "No updates were provided.")

    existing = update_feedback_item_admin(
        _database_path(request),
        feedback_id=feedback_id,
        **updates,
    )
    if existing is None:
        raise ApiError(404, "FEEDBACK_NOT_FOUND", "Feedback item was not found.")
    return {"feedback": existing, "message": "Feedback updated."}


@router.patch("/feedback/{feedback_id}/status", response_model=dict[str, Any], tags=["beta"])
def update_feedback_status(
    request: Request,
    feedback_id: str,
    payload: FeedbackAdminStatusRequest,
    _: AuthUser = Depends(require_admin),
) -> dict[str, Any]:
    """Allow administrators to move feedback through its review lifecycle."""
    existing = update_feedback_item(
        _database_path(request),
        feedback_id=feedback_id,
        status=payload.status,
    )
    if existing is None:
        raise ApiError(404, "FEEDBACK_NOT_FOUND", "Feedback item was not found.")
    return {"feedback": existing, "message": "Feedback status updated."}


@router.post(
    "/feedback/admin/improvement-tasks",
    response_model=ImprovementTaskResponse,
    tags=["beta"],
)
def create_improvement_task_route(
    request: Request,
    payload: ImprovementTaskCreateRequest,
    current_user: AuthUser = Depends(require_admin),
) -> dict[str, Any]:
    """Create a tracked improvement task from beta feedback.

    Duplicate titles are rejected.  Linked feedback IDs are stored and the
    measured report_count is derived from them.  Priority factors that are
    administrator estimates are clearly distinguished from measured data.
    """
    try:
        task = create_improvement_task(
            _database_path(request),
            user_id=current_user.user_id,
            title=payload.title,
            description=payload.description,
            status=payload.status,
            severity=payload.severity,
            impact=payload.impact,
            reproducibility=payload.reproducibility,
            workflow_frequency=payload.workflow_frequency,
            implementation_effort=payload.implementation_effort,
            regression_test=payload.regression_test,
            feedback_ids=payload.feedback_ids,
        )
    except ValueError as exc:
        raise ApiError(409, "DUPLICATE_IMPROVEMENT_TASK", str(exc)) from exc
    return task


@router.get(
    "/feedback/admin/improvement-tasks",
    response_model=ImprovementTaskListResponse,
    tags=["beta"],
)
def list_improvement_tasks_route(
    request: Request,
    _: AuthUser = Depends(require_admin),
    status: str | None = None,
    sort_by: str = "priority",
    sort_order: str = "desc",
) -> dict[str, list[dict[str, Any]]]:
    """List improvement tasks with optional status filter and sorting."""
    return {
        "tasks": list_improvement_tasks(
            _database_path(request),
            status=status,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    }


@router.patch(
    "/feedback/admin/improvement-tasks/{task_id}",
    response_model=ImprovementTaskResponse,
    tags=["beta"],
)
def update_improvement_task_route(
    request: Request,
    task_id: str,
    payload: ImprovementTaskUpdateRequest,
    current_user: AuthUser = Depends(require_admin),
) -> dict[str, Any]:
    """Update an improvement task and recompute its priority score.

    Priority factors that are administrator estimates are clearly marked.
    Only the measured report_count is derived from linked feedback rows.
    """
    updates: dict[str, Any] = {}
    if payload.title is not None:
        updates["title"] = payload.title
    if payload.description is not None:
        updates["description"] = payload.description
    if payload.status is not None:
        updates["status"] = payload.status
    if payload.severity is not None:
        updates["severity"] = payload.severity
    if payload.impact is not None:
        updates["impact"] = payload.impact
    if payload.reproducibility is not None:
        updates["reproducibility"] = payload.reproducibility
    if payload.workflow_frequency is not None:
        updates["workflow_frequency"] = payload.workflow_frequency
    if payload.implementation_effort is not None:
        updates["implementation_effort"] = payload.implementation_effort
    if payload.regression_test is not None:
        updates["regression_test"] = payload.regression_test
    if payload.implemented is not None:
        updates["implemented"] = payload.implemented

    task = update_improvement_task(
        _database_path(request),
        task_id=task_id,
        user_id=current_user.user_id,
        **updates,
    )
    if task is None:
        raise ApiError(404, "IMPROVEMENT_TASK_NOT_FOUND", "Improvement task was not found.")
    return task


@router.post(
    "/feedback/admin/improvement-tasks/{task_id}/feedback/{feedback_id}",
    response_model=ImprovementTaskResponse,
    tags=["beta"],
)
def link_feedback_to_task_route(
    request: Request,
    task_id: str,
    feedback_id: str,
    _: AuthUser = Depends(require_admin),
) -> dict[str, Any]:
    """Link a feedback item to an improvement task and recompute priority."""
    task = link_feedback_to_task(
        _database_path(request),
        task_id=task_id,
        feedback_id=feedback_id,
    )
    if task is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "Task or feedback item was not found.")
    return task


@router.delete(
    "/feedback/admin/improvement-tasks/{task_id}/feedback/{feedback_id}",
    response_model=ImprovementTaskResponse,
    tags=["beta"],
)
def unlink_feedback_from_task_route(
    request: Request,
    task_id: str,
    feedback_id: str,
    _: AuthUser = Depends(require_admin),
) -> dict[str, Any]:
    """Unlink a feedback item from an improvement task and recompute priority."""
    task = unlink_feedback_from_task(
        _database_path(request),
        task_id=task_id,
        feedback_id=feedback_id,
    )
    if task is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "Task or feedback item was not found.")
    return task


@router.get(
    "/feedback/admin/prioritization",
    response_model=PrioritizationExplanation,
    tags=["beta"],
)
def prioritization_explanation(
    _: AuthUser = Depends(require_admin),
) -> dict[str, Any]:
    """Return the documented prioritization formula and field classifications.

    Measured fields are derived from stored rows only.  Estimated fields are
    administrator judgments and are never treated as observed facts.
    """
    return {
        "formula": (
            "priority_score = severity*20 + impact*15 + report_count*10 + "
            "reproducibility*10 + workflow_frequency*10 - "
            "implementation_effort*5"
        ),
        "weights": {
            "severity": 20,
            "impact": 15,
            "report_count": 10,
            "reproducibility": 10,
            "workflow_frequency": 10,
            "implementation_effort": 5,
        },
        "measured_fields": ["report_count"],
        "estimated_fields": [
            "severity",
            "impact",
            "reproducibility",
            "workflow_frequency",
            "implementation_effort",
        ],
        "range_note": (
            "Approximate range 0 to 150.  Higher scores indicate higher "
            "priority.  Implementation effort reduces the score."
        ),
    }


# ---------------------------------------------------------------------------
# M34: Personalized learning paths
# ---------------------------------------------------------------------------


@router.get(
    "/learning-paths/adaptive-recommendations",
    response_model=AdaptiveRecommendationsResponse,
    tags=["learning paths"],
)
def adaptive_recommendations_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    limit: int = 5,
) -> dict[str, Any]:
    """Return deterministic adaptive learning recommendations for the user.

    Recommendations are derived from persisted quiz attempts, study sessions,
    topic reviews, and source passages.  No external model or AI service is
    used.
    """
    if limit < 1 or limit > 20:
        raise ApiError(422, "INVALID_LIMIT", "Limit must be between 1 and 20.")
    recommendations = compute_adaptive_recommendations(
        _database_path(request),
        user_id=current_user.user_id,
        now=datetime.now(UTC),
        limit=limit,
    )
    return {"recommendations": recommendations}


@router.get(
    "/learning-paths",
    response_model=LearningPathListResponse,
    tags=["learning paths"],
)
def list_learning_paths_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """List the current user's learning paths."""
    return {
        "paths": list_learning_paths(
            _database_path(request),
            user_id=current_user.user_id,
            status=status,
        )
    }


@router.post(
    "/learning-paths",
    response_model=LearningPathResponse,
    status_code=201,
    tags=["learning paths"],
)
def create_learning_path_route(
    request: Request,
    payload: LearningPathCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new learning path for the current user."""
    path = create_learning_path(
        _database_path(request),
        user_id=current_user.user_id,
        title=payload.title,
        description=payload.description,
    )
    return path


@router.get(
    "/learning-paths/{path_id}",
    response_model=dict[str, Any],
    tags=["learning paths"],
)
def get_learning_path_route(
    request: Request,
    path_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return a learning path with its objectives."""
    path = get_learning_path(_database_path(request), path_id)
    if path is None:
        raise ApiError(404, "LEARNING_PATH_NOT_FOUND", "Learning path was not found.")
    if path["userId"] != current_user.user_id:
        raise ApiError(403, "LEARNING_PATH_FORBIDDEN", "You do not own this path.")
    objectives = list_learning_objectives(_database_path(request), path_id=path_id)
    return {"path": path, "objectives": objectives}


@router.get(
    "/learning-paths/{path_id}/objectives",
    response_model=LearningObjectiveListResponse,
    tags=["learning paths"],
)
def list_learning_objectives_route(
    request: Request,
    path_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List objectives for a learning path."""
    path = get_learning_path(_database_path(request), path_id)
    if path is None:
        raise ApiError(404, "LEARNING_PATH_NOT_FOUND", "Learning path was not found.")
    if path["userId"] != current_user.user_id:
        raise ApiError(403, "LEARNING_PATH_FORBIDDEN", "You do not own this path.")
    objectives = list_learning_objectives(_database_path(request), path_id=path_id)
    return {"objectives": objectives}


@router.get(
    "/learning-objectives/{objective_id}",
    response_model=LearningObjectiveResponse,
    tags=["learning paths"],
)
def get_learning_objective_route(
    request: Request,
    objective_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get a single learning objective."""
    objective = get_learning_objective(_database_path(request), objective_id)
    if objective is None:
        raise ApiError(404, "LEARNING_OBJECTIVE_NOT_FOUND", "Objective was not found.")
    path = get_learning_path(_database_path(request), objective["pathId"])
    if path is None or path["userId"] != current_user.user_id:
        raise ApiError(403, "LEARNING_OBJECTIVE_FORBIDDEN", "You do not own this objective.")
    return objective


@router.patch(
    "/learning-paths/{path_id}",
    response_model=LearningPathResponse,
    tags=["learning paths"],
)
def update_learning_path_route(
    request: Request,
    path_id: str,
    payload: LearningPathUpdateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a learning path (title, description, status)."""
    updates: dict[str, Any] = {}
    if payload.title is not None:
        updates["title"] = payload.title
    if payload.description is not None:
        updates["description"] = payload.description
    if payload.status is not None:
        updates["status"] = payload.status

    if not updates:
        raise ApiError(400, "EMPTY_LEARNING_PATH_UPDATE", "No updates were provided.")

    try:
        path = update_learning_path(
            _database_path(request),
            path_id=path_id,
            user_id=current_user.user_id,
            **updates,
        )
    except PermissionError:
        raise ApiError(403, "LEARNING_PATH_FORBIDDEN", "You do not own this path.") from None
    if path is None:
        raise ApiError(404, "LEARNING_PATH_NOT_FOUND", "Learning path was not found.")
    return path


@router.delete(
    "/learning-paths/{path_id}",
    tags=["learning paths"],
)
def delete_learning_path_route(
    request: Request,
    path_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    try:
        deleted = delete_learning_path(
            _database_path(request),
            path_id=path_id,
            user_id=current_user.user_id,
        )
    except PermissionError:
        raise ApiError(403, "LEARNING_PATH_FORBIDDEN", "You do not own this path.") from None
    if not deleted:
        raise ApiError(404, "LEARNING_PATH_NOT_FOUND", "Learning path was not found.")
    return {"pathId": path_id, "deleted": True}


@router.post(
    "/learning-paths/{path_id}/objectives",
    response_model=LearningObjectiveResponse,
    status_code=201,
    tags=["learning paths"],
)
def create_learning_objective_route(
    request: Request,
    path_id: str,
    payload: LearningObjectiveCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new objective under a learning path.

    The caller must own the path.  Prerequisite objectives must belong to the
    same path.
    """
    path = get_learning_path(_database_path(request), path_id)
    if path is None:
        raise ApiError(404, "LEARNING_PATH_NOT_FOUND", "Learning path was not found.")
    if path["userId"] != current_user.user_id:
        raise ApiError(403, "LEARNING_PATH_FORBIDDEN", "You do not own this path.")

    try:
        objective = create_learning_objective(
            _database_path(request),
            path_id=path_id,
            title=payload.title,
            description=payload.description,
            order_index=payload.order_index,
            source_document_id=payload.source_document_id,
            source_chunk_id=payload.source_chunk_id,
            source_passage=payload.source_passage,
            prerequisite_objective_ids=payload.prerequisite_objective_ids,
            rationale=payload.rationale,
            difficulty_level=payload.difficulty_level,
        )
    except ValueError as exc:
        raise ApiError(400, "INVALID_OBJECTIVE", str(exc)) from exc
    if objective is None:
        raise ApiError(404, "LEARNING_PATH_NOT_FOUND", "Learning path was not found.")
    return objective


@router.patch(
    "/learning-objectives/{objective_id}",
    response_model=LearningObjectiveResponse,
    tags=["learning paths"],
)
def update_learning_objective_route(
    request: Request,
    objective_id: str,
    payload: LearningObjectiveUpdateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a learning objective's mutable fields."""
    objective = get_learning_objective(_database_path(request), objective_id)
    if objective is None:
        raise ApiError(404, "LEARNING_OBJECTIVE_NOT_FOUND", "Objective was not found.")
    path = get_learning_path(_database_path(request), objective["pathId"])
    if path is None or path["userId"] != current_user.user_id:
        raise ApiError(403, "LEARNING_OBJECTIVE_FORBIDDEN", "You do not own this objective.")

    updates: dict[str, Any] = {}
    if payload.title is not None:
        updates["title"] = payload.title
    if payload.description is not None:
        updates["description"] = payload.description
    if payload.status is not None:
        updates["status"] = payload.status
    if payload.order_index is not None:
        updates["order_index"] = payload.order_index
    if payload.rationale is not None:
        updates["rationale"] = payload.rationale
    if payload.difficulty_level is not None:
        updates["difficulty_level"] = payload.difficulty_level

    if not updates:
        raise ApiError(400, "EMPTY_OBJECTIVE_UPDATE", "No updates were provided.")

    objective = update_learning_objective(
        _database_path(request),
        objective_id=objective_id,
        **updates,
    )
    if objective is None:
        raise ApiError(404, "LEARNING_OBJECTIVE_NOT_FOUND", "Objective was not found.")
    return objective


@router.delete(
    "/learning-objectives/{objective_id}",
    tags=["learning paths"],
)
def delete_learning_objective_route(
    request: Request,
    objective_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a learning objective and its evidence/activity links."""
    objective = get_learning_objective(_database_path(request), objective_id)
    if objective is None:
        raise ApiError(404, "LEARNING_OBJECTIVE_NOT_FOUND", "Objective was not found.")
    path = get_learning_path(_database_path(request), objective["pathId"])
    if path is None or path["userId"] != current_user.user_id:
        raise ApiError(403, "LEARNING_OBJECTIVE_FORBIDDEN", "You do not own this objective.")

    deleted = delete_learning_objective(_database_path(request), objective_id=objective_id)
    if not deleted:
        raise ApiError(404, "LEARNING_OBJECTIVE_NOT_FOUND", "Objective was not found.")
    return {"objectiveId": objective_id, "deleted": True}


@router.post(
    "/learning-objectives/{objective_id}/evidence",
    response_model=LearningObjectiveResponse,
    tags=["learning paths"],
)
def link_objective_evidence_route(
    request: Request,
    objective_id: str,
    payload: LinkObjectiveEvidenceRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Link evidence citations to a learning objective."""
    objective = get_learning_objective(_database_path(request), objective_id)
    if objective is None:
        raise ApiError(404, "LEARNING_OBJECTIVE_NOT_FOUND", "Objective was not found.")
    path = get_learning_path(_database_path(request), objective["pathId"])
    if path is None or path["userId"] != current_user.user_id:
        raise ApiError(403, "LEARNING_OBJECTIVE_FORBIDDEN", "You do not own this objective.")

    objective = link_objective_evidence(
        _database_path(request),
        objective_id=objective_id,
        citation_ids=payload.citation_ids,
    )
    if objective is None:
        raise ApiError(404, "LEARNING_OBJECTIVE_NOT_FOUND", "Objective was not found.")
    return objective


@router.post(
    "/learning-objectives/{objective_id}/activities",
    response_model=LearningObjectiveResponse,
    tags=["learning paths"],
)
def link_objective_activity_route(
    request: Request,
    objective_id: str,
    payload: LinkObjectiveActivityRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Link a quiz attempt or study session to a learning objective."""
    objective = get_learning_objective(_database_path(request), objective_id)
    if objective is None:
        raise ApiError(404, "LEARNING_OBJECTIVE_NOT_FOUND", "Objective was not found.")
    path = get_learning_path(_database_path(request), objective["pathId"])
    if path is None or path["userId"] != current_user.user_id:
        raise ApiError(403, "LEARNING_OBJECTIVE_FORBIDDEN", "You do not own this objective.")

    objective = link_objective_activity(
        _database_path(request),
        objective_id=objective_id,
        activity_type=payload.activity_type,
        activity_id=payload.activity_id,
    )
    if objective is None:
        raise ApiError(404, "LEARNING_OBJECTIVE_NOT_FOUND", "Objective was not found.")
    return objective


@router.get("/product-analytics", response_model=ProductAnalyticsSummary, tags=["analytics"])
def product_analytics(request: Request) -> dict[str, Any]:
    settings = _settings(request)
    current_user = require_auth(request)
    if not settings.analytics_enabled:
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "enabled": False,
            "window_days": settings.product_analytics_retention_days,
            "totals": {
                "event_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "feature_count": 0,
            },
            "features": [],
            "trends": [],
            "privacy": {
                "retention_days": settings.product_analytics_retention_days,
                "raw_content": "not_collected",
                "identifiers": "user_id_only_for_personalized_views",
            },
        }
    prune_product_events(
        _database_path(request), retention_days=settings.product_analytics_retention_days
    )
    snapshot = get_product_analytics_snapshot(
        _database_path(request),
        user_id=current_user.user_id,
        window_days=settings.product_analytics_retention_days,
    )
    snapshot["privacy"]["retention_days"] = settings.product_analytics_retention_days
    snapshot["enabled"] = True
    return snapshot


@router.post("/product-analytics/events", tags=["analytics"])
def record_product_analytics_event(
    request: Request,
    payload: ProductAnalyticsEventRequest,
) -> dict[str, str]:
    current_user = require_auth(request)
    settings = _settings(request)
    if not settings.analytics_enabled:
        return {"status": "disabled"}
    record_product_event(
        _database_path(request),
        user_id=current_user.user_id,
        event_name=payload.event_name,
        feature=payload.feature,
        result=payload.result,
        category="error" if payload.result == "failure" else "feature",
        metadata=payload.metadata,
    )
    return {"status": "recorded"}


@router.get("/ready", response_model=ReadinessResponse, tags=["system"])
def readiness(request: Request) -> JSONResponse:
    from app.storage import check_storage

    checks = check_storage(_database_path(request))
    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@router.get("/metrics", tags=["system"])
def metrics(request: Request) -> dict[str, Any]:
    metrics_state: OperationalMetrics = request.app.state.metrics
    return metrics_state.snapshot()


@router.get("/diagnostics", tags=["system"])
def diagnostics(request: Request) -> JSONResponse:
    settings = _settings(request)
    if not settings.diagnostics_token:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "DIAGNOSTICS_DISABLED",
                    "message": "Diagnostics are not enabled.",
                }
            },
        )
    provided = request.headers.get("X-Diagnostics-Token")
    if not provided or not hasattr(request.app.state, "metrics"):
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "DIAGNOSTICS_UNAUTHORIZED", "message": "Unauthorized."}},
        )
    if provided != settings.diagnostics_token:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "DIAGNOSTICS_UNAUTHORIZED", "message": "Unauthorized."}},
        )
    return JSONResponse(content={"status": "ok", "metrics": request.app.state.metrics.snapshot()})


@router.post("/documents", status_code=201, response_model=DocumentResponse, tags=["documents"])
def upload_document(
    request: Request,
    file: Annotated[UploadFile, File()],
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    settings = _settings(request)
    metadata = ingest_pdf(
        file.file,
        filename=file.filename,
        database_path=settings.database_path,
        settings=settings,
        user_id=current_user.user_id,
    )
    response = _safe_document(metadata)  # type: ignore[arg-type]
    response["embeddingStatus"] = "not_requested"
    if settings.retrieval_mode != "lexical":
        try:
            result = _embedding_service(request).rebuild(
                settings.database_path,
                user_id=current_user.user_id,
            )
            response["embeddingStatus"] = "indexed" if result["complete"] else "incomplete"
        except ModelUnavailable:
            response["embeddingStatus"] = "unavailable"
        except (OSError, sqlite3.Error, ValueError):
            response["embeddingStatus"] = "incomplete"
    return response


@router.get("/documents", response_model=DocumentsResponse, tags=["documents"])
def documents(request: Request, current_user: AuthUser = Depends(require_auth)) -> dict[str, Any]:
    rows = _database_call(list_documents, _database_path(request), user_id=current_user.user_id)
    return {"documents": [_safe_document(row) for row in rows]}


@router.get("/documents/{document_id}", response_model=DocumentResponse, tags=["documents"])
def document(
    request: Request, document_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    row = _database_call(
        get_document, _database_path(request), document_id, user_id=current_user.user_id
    )
    if row is None:
        raise ApiError(404, "DOCUMENT_NOT_FOUND", "The requested document was not found.")
    return _safe_document(row)


@router.delete(
    "/documents/{document_id}", response_model=DeleteDocumentResponse, tags=["documents"]
)
def remove_document(
    request: Request, document_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    deleted = _database_call(
        delete_document, _database_path(request), document_id, user_id=current_user.user_id
    )
    if not deleted:
        raise ApiError(404, "DOCUMENT_NOT_FOUND", "The requested document was not found.")
    return {"documentId": document_id, "deleted": True}


@router.post(
    "/images", response_model=LearningImageDetailResponse, status_code=201, tags=["images"]
)
async def upload_learning_image(
    request: Request,
    file: Annotated[UploadFile, File()],
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    settings = _settings(request)
    content = await file.read(settings.max_image_upload_bytes + 1)
    normalized = await asyncio.to_thread(
        normalize_image,
        content,
        max_bytes=settings.max_image_upload_bytes,
        max_dimension=settings.max_image_dimension,
        max_pixels=settings.max_image_pixels,
    )
    ocr_status, extracted_text = await asyncio.to_thread(recognize_text, normalized.content)
    metadata = {
        "imageId": f"img_{uuid.uuid4().hex}",
        "displayFilename": safe_image_filename(file.filename),
        "contentType": normalized.content_type,
        "width": normalized.width,
        "height": normalized.height,
        "sha256": normalized.sha256,
        "createdAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "ocrStatus": ocr_status,
        "extractedText": extracted_text,
    }
    _database_call(
        store_learning_image,
        settings.database_path,
        metadata,
        normalized.content,
        user_id=current_user.user_id,
    )
    return _database_call(
        get_learning_image_detail,
        settings.database_path,
        metadata["imageId"],
        user_id=current_user.user_id,
    )


@router.get("/images", response_model=LearningImageListResponse, tags=["images"])
def learning_images(
    request: Request, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    return {
        "images": _database_call(
            list_learning_images, _database_path(request), user_id=current_user.user_id
        )
    }


@router.get("/images/{image_id}", response_model=LearningImageDetailResponse, tags=["images"])
def learning_image(
    request: Request, image_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    result = _database_call(
        get_learning_image_detail, _database_path(request), image_id, user_id=current_user.user_id
    )
    if result is None:
        raise ApiError(404, "IMAGE_NOT_FOUND", "The requested image was not found.")
    return result


@router.get("/images/{image_id}/content", tags=["images"])
def learning_image_content(
    request: Request, image_id: str, current_user: AuthUser = Depends(require_auth)
) -> Response:
    image = _database_call(
        get_learning_image, _database_path(request), image_id, user_id=current_user.user_id
    )
    if image is None:
        raise ApiError(404, "IMAGE_NOT_FOUND", "The requested image was not found.")
    return Response(
        image["imageBytes"],
        media_type=image["contentType"],
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/images/{image_id}", tags=["images"])
def remove_learning_image(
    request: Request, image_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    deleted = _database_call(
        delete_learning_image, _database_path(request), image_id, user_id=current_user.user_id
    )
    if not deleted:
        raise ApiError(404, "IMAGE_NOT_FOUND", "The requested image was not found.")
    return {"imageId": image_id, "deleted": True}


@router.post(
    "/images/{image_id}/regions",
    response_model=ImageRegionCreateResponse,
    status_code=201,
    tags=["images"],
)
def create_image_region(
    request: Request,
    image_id: str,
    payload: ImageRegionCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    settings = _settings(request)
    image = _database_call(
        get_learning_image, settings.database_path, image_id, user_id=current_user.user_id
    )
    if image is None:
        raise ApiError(404, "IMAGE_NOT_FOUND", "The requested image was not found.")
    concept = None
    if payload.diagram_id and payload.concept_id:
        diagram = get_diagram(payload.diagram_id)
        if diagram is None:
            raise ApiError(404, "DIAGRAM_NOT_FOUND", "The requested curated diagram was not found.")
        concept = next(
            (item for item in diagram.concepts if item.concept_id == payload.concept_id), None
        )
        if concept is None:
            raise ApiError(404, "CONCEPT_NOT_FOUND", "The selected curated concept was not found.")
    ocr_status, extracted_text = recognize_region(
        image["imageBytes"], payload.geometry.model_dump()
    )
    region = {
        "regionId": f"imr_{uuid.uuid4().hex}",
        "imageId": image_id,
        "geometry": payload.geometry.model_dump(),
        "ocrStatus": ocr_status,
        "extractedText": extracted_text,
        "diagramId": payload.diagram_id,
        "conceptId": payload.concept_id,
        "conceptName": concept.display_name if concept else None,
        "createdAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _database_call(
        create_learning_image_region,
        settings.database_path,
        region,
        user_id=current_user.user_id,
    )
    return {
        "regionId": region["regionId"],
        "geometry": region["geometry"],
        "ocrStatus": region["ocrStatus"],
        "extractedText": region["extractedText"],
        "diagramId": region["diagramId"],
        "conceptId": region["conceptId"],
        "conceptName": region["conceptName"],
        "citations": [],
    }


def _image_region_context(
    request: Request, image_id: str, region_id: str, current_user: AuthUser = Depends(require_auth)
) -> tuple[dict[str, Any], dict[str, Any], Any | None]:
    settings = _settings(request)
    image = _database_call(
        get_learning_image, settings.database_path, image_id, user_id=current_user.user_id
    )
    if image is None:
        raise ApiError(404, "IMAGE_NOT_FOUND", "The requested image was not found.")
    region = _database_call(
        get_learning_image_region,
        settings.database_path,
        image_id,
        region_id,
        user_id=current_user.user_id,
    )
    if region is None:
        raise ApiError(404, "IMAGE_REGION_NOT_FOUND", "The selected image region was not found.")
    concept = None
    if region["diagramId"] and region["conceptId"]:
        diagram = get_diagram(region["diagramId"])
        if diagram is not None:
            concept = next(
                (item for item in diagram.concepts if item.concept_id == region["conceptId"]), None
            )
    return image, region, concept


def _image_region_search(
    request: Request,
    region: dict[str, Any],
    *,
    question: str | None,
    limit: int,
    mode: str | None,
    current_user: AuthUser,
) -> dict[str, Any]:
    settings = _settings(request)
    if limit > settings.max_retrieval_results:
        raise ApiError(422, "INVALID_REQUEST", "The result limit exceeds the configured maximum.")
    terms = [question or "", (region["extractedText"] or "")[:1_000]]
    concept = None
    if region["diagramId"] and region["conceptId"]:
        diagram = get_diagram(region["diagramId"])
        if diagram:
            concept = next(
                (item for item in diagram.concepts if item.concept_id == region["conceptId"]), None
            )
            if concept:
                terms.append(concept.retrieval_query)
    query = " ".join(term for term in terms if term).strip()
    if not query:
        return {
            "results": [],
            "retrievalMode": mode or settings.retrieval_mode,
            "fallbackReason": "no_region_text_or_question",
        }
    return retrieve_with_mode(
        settings.database_path,
        query=query[:2_000],
        document_ids=None,
        limit=limit,
        mode=mode or settings.retrieval_mode,
        settings=settings,
        embeddings=_embedding_service(request),
        user_id=current_user.user_id,
    )


@router.post(
    "/images/{image_id}/regions/{region_id}/evidence",
    response_model=ImageEvidenceResponse,
    tags=["images"],
)
def image_region_evidence(
    request: Request,
    image_id: str,
    region_id: str,
    payload: DiagramEvidenceRequest | None = None,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    payload = payload or DiagramEvidenceRequest()
    _, region, concept = _image_region_context(request, image_id, region_id, current_user)
    search = _image_region_search(
        request, region, question=None, limit=payload.limit, mode=None, current_user=current_user
    )
    _database_call(
        replace_image_region_citations,
        _database_path(request),
        region_id,
        search["results"],
        user_id=current_user.user_id,
    )
    return {
        "status": "evidence_found" if search["results"] else "insufficient_evidence",
        "imageId": image_id,
        "regionId": region_id,
        "geometry": region["geometry"],
        "extractedText": region["extractedText"],
        "ocrStatus": region["ocrStatus"],
        "conceptName": concept.display_name if concept else None,
        "results": search["results"],
        "retrievalMode": search["retrievalMode"],
        "fallbackReason": search["fallbackReason"],
    }


@router.post(
    "/images/{image_id}/regions/{region_id}/tutor",
    response_model=ImageTutorResponse,
    tags=["images", "tutor"],
)
async def image_region_tutor(
    request: Request,
    image_id: str,
    region_id: str,
    payload: ImageTutorRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    _, region, concept = _image_region_context(request, image_id, region_id, current_user)
    search = _image_region_search(
        request,
        region,
        question=payload.question,
        limit=payload.limit,
        mode=payload.mode,
        current_user=current_user,
    )
    _database_call(
        replace_image_region_citations,
        _database_path(request),
        region_id,
        search["results"],
        user_id=current_user.user_id,
    )
    generated = await _grounded_tutor_response(
        _settings(request),
        question=payload.question,
        concept=concept.display_name if concept else None,
        evidence=search["results"],
        retrieval_mode=search["retrievalMode"],
        fallback_reason=search["fallbackReason"],
    )
    return {
        "question": payload.question,
        **generated,
        "imageId": image_id,
        "regionId": region_id,
        "geometry": region["geometry"],
        "extractedText": region["extractedText"],
        "ocrStatus": region["ocrStatus"],
        "conceptName": concept.display_name if concept else None,
    }


@router.post("/retrieve", response_model=RetrievalResponse, tags=["retrieval"])
def retrieve_evidence(
    request: Request, payload: RetrievalRequest, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    settings = _settings(request)
    if payload.limit > settings.max_retrieval_results:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"The result limit cannot exceed {settings.max_retrieval_results}.",
        )
    return retrieve_with_mode(
        settings.database_path,
        query=payload.query,
        document_ids=payload.document_ids,
        limit=payload.limit,
        mode=payload.mode or settings.retrieval_mode,
        settings=settings,
        embeddings=_embedding_service(request),
        user_id=current_user.user_id,
    )


@router.post("/search", response_model=UnifiedSearchResponse, tags=["search"])
def unified_search(
    request: Request, payload: UnifiedSearchRequest, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    """Unified search across documents, content resources, quizzes,
    learning objectives, and knowledge-graph concepts.

    Every resource type is searched with the authenticated user's
    ownership filter.  Results are ranked by relevance and returned
    with source provenance and snippets.
    """
    settings = _settings(request)
    if payload.limit > settings.max_retrieval_results:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"The result limit cannot exceed {settings.max_retrieval_results}.",
        )

    resource_types = payload.resource_types or [
        "document",
        "content_resource",
        "quiz",
        "learning_objective",
        "knowledge_concept",
    ]

    all_results: list[dict[str, Any]] = []
    retrieval_mode = "lexical"
    fallback_reason: str | None = None

    # --- Document passages (reuses existing FTS5/semantic retrieval) ---
    if "document" in resource_types:
        try:
            doc_search = retrieve_with_mode(
                settings.database_path,
                query=payload.query,
                document_ids=payload.document_ids,
                limit=min(payload.limit, settings.max_retrieval_results),
                mode=payload.mode or settings.retrieval_mode,
                settings=settings,
                embeddings=_embedding_service(request),
                user_id=current_user.user_id,
            )
            retrieval_mode = doc_search["retrievalMode"]
            fallback_reason = doc_search.get("fallbackReason")
            for item in doc_search["results"]:
                all_results.append(
                    {
                        "result_type": "document_passage",
                        "id": item["citationId"],
                        "title": item["displayFilename"],
                        "snippet": item["text"][:200],
                        "score": 1.0 / max(1, item["retrieval"].get("rank", 1)),
                        "source_type": "document",
                        "source_id": item["documentId"],
                        "source_title": item["displayFilename"],
                        "page_number": item["pageNumber"],
                        "citations": [item],
                        "created_at": None,
                        "updated_at": None,
                    }
                )
        except ApiError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise ApiError(
                503, "STORAGE_UNAVAILABLE", "Local search storage is unavailable."
            ) from exc

    # --- Content resource sections ---
    if "content_resource" in resource_types:
        try:
            sections, section_total = search_content_sections(
                settings.database_path,
                query=payload.query,
                user_id=current_user.user_id,
                limit=payload.limit,
                offset=payload.offset,
            )
            all_results.extend(sections)
        except (OSError, sqlite3.Error) as exc:
            raise ApiError(
                503, "STORAGE_UNAVAILABLE", "Local search storage is unavailable."
            ) from exc

    # --- Quiz questions ---
    if "quiz" in resource_types:
        try:
            questions, question_total = search_quiz_questions(
                settings.database_path,
                query=payload.query,
                user_id=current_user.user_id,
                limit=payload.limit,
                offset=payload.offset,
            )
            all_results.extend(questions)
        except (OSError, sqlite3.Error) as exc:
            raise ApiError(
                503, "STORAGE_UNAVAILABLE", "Local search storage is unavailable."
            ) from exc

    # --- Learning objectives ---
    if "learning_objective" in resource_types:
        try:
            objectives, objective_total = search_learning_objectives(
                settings.database_path,
                query=payload.query,
                user_id=current_user.user_id,
                limit=payload.limit,
                offset=payload.offset,
            )
            all_results.extend(objectives)
        except (OSError, sqlite3.Error) as exc:
            raise ApiError(
                503, "STORAGE_UNAVAILABLE", "Local search storage is unavailable."
            ) from exc

    # --- Knowledge graph concepts ---
    if "knowledge_concept" in resource_types:
        try:
            concepts, concept_total = search_knowledge_concepts(
                settings.database_path,
                query=payload.query,
                user_id=current_user.user_id,
                limit=payload.limit,
                offset=payload.offset,
            )
            all_results.extend(concepts)
        except (OSError, sqlite3.Error) as exc:
            raise ApiError(
                503, "STORAGE_UNAVAILABLE", "Local search storage is unavailable."
            ) from exc

    # --- Rank, paginate, and count ---
    all_results.sort(key=lambda r: (-r["score"], r["updated_at"] or "", r["id"]))
    total = len(all_results)
    paginated = all_results[payload.offset : payload.offset + payload.limit]

    by_type: dict[str, int] = {}
    for r in all_results:
        by_type[r["source_type"]] = by_type.get(r["source_type"], 0) + 1

    return {
        "results": paginated,
        "total": total,
        "limit": payload.limit,
        "offset": payload.offset,
        "query": payload.query,
        "retrieval_mode": retrieval_mode,
        "fallback_reason": fallback_reason,
        "by_type": by_type,
    }


@router.post("/answers", response_model=AnswerResponse, tags=["retrieval"])
def answer(
    request: Request, payload: AnswerRequest, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    settings = _settings(request)
    if payload.limit > settings.max_retrieval_results:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"The result limit cannot exceed {settings.max_retrieval_results}.",
        )
    search = retrieve_with_mode(
        settings.database_path,
        query=payload.question,
        document_ids=payload.document_ids,
        limit=payload.limit,
        mode=payload.mode or settings.retrieval_mode,
        settings=settings,
        embeddings=_embedding_service(request),
        user_id=current_user.user_id,
    )
    response = answer_from_evidence(payload.question, search["results"])
    response["retrievalMode"] = search["retrievalMode"]
    response["fallbackReason"] = search["fallbackReason"]
    return response


@router.get("/tutor/status", response_model=TutorStatusResponse, tags=["tutor"])
def tutor_status(request: Request) -> dict[str, Any]:
    settings = _settings(request)
    return {
        "configured": settings.tutor_configured,
        "modelId": settings.tutor_model_id if settings.tutor_configured else None,
    }


@router.post("/tutor/answers", response_model=TutorAnswerResponse, tags=["tutor"])
async def tutor_answer(
    request: Request, payload: TutorAnswerRequest, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    settings = _settings(request)
    if payload.limit > settings.max_retrieval_results:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"The result limit cannot exceed {settings.max_retrieval_results}.",
        )

    concept = None
    concept_name = None
    query = payload.question
    if payload.diagram_id is not None and payload.concept_id is not None:
        diagram = get_diagram(payload.diagram_id)
        if diagram is None:
            raise ApiError(404, "DIAGRAM_NOT_FOUND", "The requested diagram is not supported.")
        concept = next(
            (item for item in diagram.concepts if item.concept_id == payload.concept_id), None
        )
        if concept is None:
            raise ApiError(404, "CONCEPT_NOT_FOUND", "The selected concept is not supported.")
        concept_name = concept.display_name
        query = f"{payload.question} {concept.retrieval_query}"
    elif payload.graph_concept_id is not None:
        graph_concept = _database_call(
            get_knowledge_concept,
            settings.database_path,
            payload.graph_concept_id,
            user_id=current_user.user_id,
        )
        if graph_concept is None:
            raise ApiError(404, "CONCEPT_NOT_FOUND", "The requested graph concept was not found.")
        concept_name = graph_concept["name"]
        retrieval_query = " ".join([graph_concept["name"], *graph_concept["aliases"]])
        query = f"{payload.question} {retrieval_query}"

    search = retrieve_with_mode(
        settings.database_path,
        query=query,
        document_ids=payload.document_ids,
        limit=payload.limit,
        mode=payload.mode or settings.retrieval_mode,
        settings=settings,
        embeddings=_embedding_service(request),
        user_id=current_user.user_id,
    )
    generated = await _grounded_tutor_response(
        settings,
        question=payload.question,
        concept=concept_name,
        evidence=search["results"],
        retrieval_mode=search["retrievalMode"],
        fallback_reason=search["fallbackReason"],
    )
    return {"question": payload.question, **generated}


@router.get("/knowledge-graph", response_model=KnowledgeGraphConceptList, tags=["knowledge graph"])
def knowledge_graph(
    request: Request, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    return {
        "concepts": _database_call(
            list_knowledge_concepts, _database_path(request), user_id=current_user.user_id
        )
    }


@router.post(
    "/knowledge-graph/extract",
    response_model=KnowledgeGraphExtractResponse,
    tags=["knowledge graph"],
)
async def extract_knowledge_graph(
    request: Request,
    payload: KnowledgeGraphExtractRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    settings = _settings(request)
    try:
        chunks = knowledge_graph_chunks(
            settings.database_path,
            document_ids=payload.document_ids,
            limit=payload.chunk_limit + 1,
            after_chunk_id=payload.after_chunk_id,
            user_id=current_user.user_id,
        )
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if not chunks:
        return {"chunksProcessed": 0, "nextAfterChunkId": None, "concepts": []}
    has_more = len(chunks) > payload.chunk_limit
    chunks = chunks[: payload.chunk_limit]
    parsed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    try:
        for chunk in chunks:
            result = await asyncio.to_thread(extract_chunk, settings, chunk)
            parsed.append((chunk, result))
        for chunk, result in parsed:
            store_knowledge_extraction(
                settings.database_path, chunk, result, user_id=current_user.user_id
            )
    except TutorUnavailable as exc:
        raise ApiError(
            503,
            "TUTOR_MODEL_UNAVAILABLE",
            "Configure the optional local tutor to extract graph candidates.",
        ) from exc
    except InvalidTutorResponse as exc:
        raise ApiError(
            502,
            "GRAPH_INVALID_EXTRACTION",
            "The local model returned malformed or unsupported graph data.",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local graph storage is unavailable.") from exc
    return {
        "chunksProcessed": len(chunks),
        "nextAfterChunkId": chunks[-1]["chunk_id"] if has_more else None,
        "concepts": _database_call(
            list_knowledge_concepts, settings.database_path, user_id=current_user.user_id
        ),
    }


@router.get(
    "/knowledge-graph/concepts/{concept_id}",
    response_model=KnowledgeGraphConceptDetail,
    tags=["knowledge graph"],
)
def knowledge_graph_concept(
    request: Request, concept_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    result = _database_call(
        get_knowledge_concept, _database_path(request), concept_id, user_id=current_user.user_id
    )
    if result is None:
        raise ApiError(404, "CONCEPT_NOT_FOUND", "The requested graph concept was not found.")
    return result


@router.post(
    "/knowledge-graph/relationships/{relationship_id}/verify",
    tags=["knowledge graph"],
)
def verify_knowledge_graph_relationship(
    request: Request, relationship_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    verified = _database_call(
        verify_knowledge_relationship,
        _database_path(request),
        relationship_id,
        user_id=current_user.user_id,
    )
    if not verified:
        raise ApiError(404, "RELATIONSHIP_NOT_FOUND", "The candidate relationship was not found.")
    return {"relationshipId": relationship_id, "status": "human_verified"}


@router.post("/quizzes", response_model=QuizSetResponse, tags=["learning"])
async def create_quiz(
    request: Request, payload: QuizGenerateRequest, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    settings = _settings(request)
    if payload.limit > settings.max_retrieval_results:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"The evidence limit cannot exceed {settings.max_retrieval_results}.",
        )
    topic = " ".join(payload.topic.split())
    topic_id = hashlib.sha256(topic.casefold().encode("utf-8")).hexdigest()[:20]
    query = topic
    if payload.diagram_id is not None and payload.concept_id is not None:
        diagram = get_diagram(payload.diagram_id)
        if diagram is None:
            raise ApiError(404, "DIAGRAM_NOT_FOUND", "The requested diagram is not supported.")
        concept = next(
            (item for item in diagram.concepts if item.concept_id == payload.concept_id), None
        )
        if concept is None:
            raise ApiError(404, "CONCEPT_NOT_FOUND", "The selected concept is not supported.")
        query = f"{topic} {concept.retrieval_query}"

    search = retrieve_with_mode(
        settings.database_path,
        query=query,
        document_ids=payload.document_ids,
        limit=payload.limit,
        mode=payload.mode or settings.retrieval_mode,
        settings=settings,
        embeddings=_embedding_service(request),
        user_id=current_user.user_id,
    )
    if not search["results"]:
        return {
            "status": "insufficient_evidence",
            "quizId": None,
            "topicId": None,
            "topic": topic,
            "createdAt": None,
            "questions": [],
        }

    try:
        generated = await asyncio.to_thread(
            generate_quiz_questions,
            settings,
            topic=topic,
            count=payload.question_count,
            evidence=search["results"],
        )
    except TutorUnavailable as exc:
        raise ApiError(
            503,
            "TUTOR_MODEL_UNAVAILABLE",
            "The optional local tutor is not configured or could not be reached.",
        ) from exc
    except InvalidTutorResponse as exc:
        raise ApiError(
            502,
            "QUIZ_INVALID_RESPONSE",
            "The local model returned malformed or unsupported quiz questions.",
        ) from exc
    if not generated:
        return {
            "status": "insufficient_evidence",
            "quizId": None,
            "topicId": None,
            "topic": topic,
            "createdAt": None,
            "questions": [],
        }

    quiz_id = uuid.uuid4().hex
    created_at = datetime.now(UTC).isoformat()
    try:
        create_quiz_set(
            settings.database_path,
            {
                "quiz_id": quiz_id,
                "topic_id": topic_id,
                "topic": topic,
                "created_at": created_at,
                "questions": [
                    {
                        "question_id": item["questionId"],
                        "question_type": item["questionType"],
                        "prompt": item["prompt"],
                        "options": item["options"],
                        "answer_key": item["answerKey"],
                        "accepted_answers": item["acceptedAnswers"],
                        "explanation": item["explanation"],
                        "citations": item["citations"],
                    }
                    for item in generated
                ],
            },
            user_id=current_user.user_id,
        )
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503, "STORAGE_UNAVAILABLE", "Local learning storage is unavailable."
        ) from exc
    quiz = _database_call(
        get_quiz_set, settings.database_path, quiz_id, user_id=current_user.user_id
    )
    return {"status": "ready", **quiz}


@router.get("/quizzes/{quiz_id}", response_model=QuizSetResponse, tags=["learning"])
def read_quiz(
    request: Request,
    quiz_id: str,
    current_user: AuthUser = Depends(require_auth),
    review_incorrect: bool = False,
) -> dict[str, Any]:
    quiz = _database_call(
        get_quiz_set,
        _database_path(request),
        quiz_id,
        review_incorrect=review_incorrect,
        user_id=current_user.user_id,
    )
    if quiz is None:
        raise ApiError(404, "QUIZ_NOT_FOUND", "The requested quiz was not found.")
    return {"status": "ready", **quiz}


@router.post(
    "/quizzes/{quiz_id}/questions/{question_id}/attempts",
    response_model=QuizAttemptResponse,
    tags=["learning"],
)
def submit_quiz_answer(
    request: Request,
    quiz_id: str,
    question_id: str,
    payload: QuizAttemptRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    database_path = _database_path(request)
    question = _database_call(
        get_quiz_question, database_path, question_id, user_id=current_user.user_id
    )
    if question is None or question["quiz_id"] != quiz_id:
        raise ApiError(404, "QUIZ_QUESTION_NOT_FOUND", "The requested quiz question was not found.")
    try:
        outcome = evaluate_answer(question, payload.answer)
    except ValueError as exc:
        raise ApiError(422, "INVALID_ANSWER", str(exc)) from exc
    attempt_id = uuid.uuid4().hex
    _database_call(
        record_quiz_attempt,
        database_path,
        attempt_id=attempt_id,
        question_id=question_id,
        outcome=outcome,
        created_at=datetime.now(UTC).isoformat(),
        user_id=current_user.user_id,
    )
    quiz = _database_call(get_quiz_set, database_path, quiz_id, user_id=current_user.user_id)
    reviewed = next(item for item in quiz["questions"] if item["questionId"] == question_id)
    expected_answer = question["answer_key"]
    if question["question_type"] == "multiple_choice":
        expected_answer = next(
            item["text"] for item in question["options"] if item["id"] == expected_answer
        )
    return {
        "attemptId": attempt_id,
        "questionId": question_id,
        "outcome": outcome,
        "isCorrect": True if outcome == "correct" else False if outcome == "incorrect" else None,
        "expectedAnswer": expected_answer,
        "explanation": reviewed["explanation"],
        "citations": reviewed["citations"],
        "attemptCount": reviewed["attemptCount"],
    }


@router.get("/revision/queue", response_model=RevisionQueueResponse, tags=["learning"])
def revision_queue(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the due/overdue revision queue for the authenticated user.

    Combines quiz questions and flashcard sections that are due for review,
    ordered by due date (most overdue first). Includes source citations and
    provenance for each item.
    """
    settings = _settings(request)
    items, total = get_revision_queue(
        settings.database_path,
        user_id=current_user.user_id,
        limit=limit,
        offset=offset,
    )
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/revision/review/{item_type}/{item_id}", tags=["learning"])
async def submit_section_review(
    request: Request,
    item_type: str,
    item_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record a human review decision for a flashcard section or quiz question.

    Records the result (remembered/partially/forgotten), optional confidence
    rating, and schedules the next review using deterministic intervals.
    Returns the updated review record with the new due date.
    """
    settings = _settings(request)
    database_path = settings.database_path

    try:
        payload = await request.json()
    except Exception:
        raise ApiError(422, "INVALID_REQUEST", "Invalid request body.") from None

    result = payload.get("result")
    confidence_rating = payload.get("confidenceRating")

    if result not in {"remembered", "partially", "forgotten"}:
        raise ApiError(422, "INVALID_RESULT", "result must be 'remembered', 'partially', or 'forgotten'")
    if confidence_rating is not None and not (1 <= confidence_rating <= 5):
        raise ApiError(422, "INVALID_CONFIDENCE", "confidenceRating must be between 1 and 5")

    if item_type == "flashcard":
        updated = record_flashcard_review(
            database_path,
            section_id=item_id,
            result=result,
            confidence_rating=confidence_rating,
            user_id=current_user.user_id,
        )
    elif item_type == "quiz":
        updated = record_quiz_review(
            database_path,
            question_id=item_id,
            result=result,
            confidence_rating=confidence_rating,
            user_id=current_user.user_id,
        )
    else:
        raise ApiError(422, "INVALID_ITEM_TYPE", "itemType must be 'flashcard' or 'quiz'")

    if updated is None:
        raise ApiError(404, "ITEM_NOT_FOUND", "The requested item was not found or is not owned by you.")
    return updated


@router.get("/progress", response_model=LearningProgressResponse, tags=["learning"])
def learning_progress(
    request: Request, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    return _database_call(
        get_learning_progress, _database_path(request), user_id=current_user.user_id
    )


@router.get(
    "/learning-analytics",
    response_model=LearningAnalyticsResponse,
    tags=["learning"],
)
def learning_analytics(
    request: Request, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    now = datetime.now(UTC)
    data = _database_call(
        get_learning_analytics_data, _database_path(request), user_id=current_user.user_id
    )
    return build_analytics(
        **{
            "attempts": data["attempts"],
            "sessions": data["sessions"],
            "reviews": data["reviews"],
            "topic_sources": data["topicSources"],
            "concepts": data["concepts"],
            "now": now,
        }
    )


@router.post(
    "/study-sessions", response_model=StudySessionResponse, status_code=201, tags=["learning"]
)
def begin_study_session(
    request: Request, payload: StudySessionRequest, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    topic = " ".join(payload.topic.split())
    topic_id = hashlib.sha256(topic.casefold().encode("utf-8")).hexdigest()[:20]
    return _database_call(
        start_study_session,
        _database_path(request),
        session_id=uuid.uuid4().hex,
        topic_id=topic_id,
        topic=topic,
        started_at=datetime.now(UTC).isoformat(),
        user_id=current_user.user_id,
    )


@router.post(
    "/study-sessions/{session_id}/complete",
    response_model=StudySessionResponse,
    tags=["learning"],
)
def finish_study_session(
    request: Request, session_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    session = _database_call(
        complete_study_session,
        _database_path(request),
        session_id=session_id,
        completed_at=datetime.now(UTC).isoformat(),
        user_id=current_user.user_id,
    )
    if session is None:
        raise ApiError(404, "STUDY_SESSION_NOT_FOUND", "The study session was not found.")
    return session


@router.get(
    "/study-coach/plan",
    response_model=StudyCoachPlanResponse,
    tags=["learning"],
)
def study_coach_plan(
    request: Request, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    now = datetime.now(UTC)
    plan_date = now.date().isoformat()
    database_path = _database_path(request)
    data = _database_call(get_learning_analytics_data, database_path, user_id=current_user.user_id)
    analytics = build_analytics(
        attempts=data["attempts"],
        sessions=data["sessions"],
        reviews=data["reviews"],
        topic_sources=data["topicSources"],
        concepts=data["concepts"],
        now=now,
    )
    analytics["topicSources"] = data["topicSources"]
    candidates = build_daily_recommendations(
        analytics=data | analytics,
        now=now,
        user_id=current_user.user_id,
    )
    stored = _database_call(
        get_or_create_study_coach_plan,
        database_path,
        plan_date=plan_date,
        recommendations=candidates,
        updated_at=now.isoformat(),
        user_id=current_user.user_id,
    )
    live_topics = {topic["topicId"]: topic for topic in analytics["topics"]}
    recommendations = []
    for item in stored:
        topic = live_topics.get(item["topicId"])
        citations = data["topicSources"].get(item["topicId"], [])
        # Source deletion cascades learner activity; never display a stale plan without evidence.
        if topic is None or not citations:
            continue
        recommendations.append(
            StudyCoachRecommendation(
                **item,
                citations=citations,
                quizTopic=topic["topic"],
            ).model_dump(by_alias=True)
        )
    empty_reason = None
    if not recommendations:
        empty_reason = (
            "No quiz attempts are recorded yet. Complete a cited quiz to get a personalized plan."
            if not data["attempts"]
            else "No current source-backed topics are available for a study recommendation."
        )
    return {
        "planDate": plan_date,
        "recommendations": recommendations,
        "emptyReason": empty_reason,
        "methodology": (
            "Priority is deterministic: overdue reviews (+100 each), weak topic (+40), "
            "latest incorrect results (+10 each, capped at 3), ambiguous results (+5 each, "
            "capped at 3), graph neighbors (+1 each, capped at 5), and completed sessions "
            "today (-10 each, capped at 2). Every item links to stored quiz source citations."
        ),
    }


@router.patch(
    "/study-coach/recommendations/{recommendation_id}",
    response_model=StudyCoachRecommendation,
    tags=["learning"],
)
def decide_study_coach_recommendation(
    request: Request,
    recommendation_id: str,
    payload: StudyCoachActionRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    database_path = _database_path(request)
    current_plan = study_coach_plan(request, current_user)
    visible_recommendations = {
        row["recommendationId"]: row for row in current_plan["recommendations"]
    }
    current = visible_recommendations.get(recommendation_id)
    if current is None:
        raise ApiError(
            404,
            "RECOMMENDATION_NOT_FOUND",
            "The study recommendation is no longer available with current source evidence.",
        )
    item = _database_call(
        update_study_coach_recommendation,
        database_path,
        recommendation_id=recommendation_id,
        action=payload.action,
        updated_at=now.isoformat(),
        title=payload.title,
        estimated_minutes=payload.estimated_minutes,
        user_id=current_user.user_id,
    )
    if item is None:
        raise ApiError(404, "RECOMMENDATION_NOT_FOUND", "The study recommendation was not found.")
    citations = current["citations"]
    if not citations:
        raise ApiError(
            404,
            "RECOMMENDATION_EVIDENCE_REMOVED",
            "The source material for this recommendation is no longer available.",
        )
    return StudyCoachRecommendation(
        **item,
        citations=citations,
        quizTopic=item["topic"],
    ).model_dump(by_alias=True)


@router.post(
    "/study-coach/recommendations/{recommendation_id}/explanation",
    response_model=StudyCoachExplanationResponse,
    tags=["learning", "tutor"],
)
async def explain_study_coach_recommendation(
    request: Request, recommendation_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    plan = study_coach_plan(request, current_user)
    item = next(
        (
            recommendation
            for recommendation in plan["recommendations"]
            if recommendation["recommendationId"] == recommendation_id
        ),
        None,
    )
    if item is None:
        raise ApiError(404, "RECOMMENDATION_NOT_FOUND", "The study recommendation was not found.")
    citations = item["citations"]
    question = "Explain why this study topic is recommended today and what to focus on."
    result = await _grounded_tutor_response(
        _settings(request),
        question=question,
        concept=item["topic"],
        evidence=citations,
        retrieval_mode="lexical_fallback",
        fallback_reason="study_coach_uses_stored_quiz_citations",
    )
    return {
        "question": question,
        "status": result["status"],
        "answer": result["answer"],
        "citations": result["citations"],
    }


@router.get("/diagrams", response_model=DiagramsResponse, tags=["diagrams"])
def diagrams() -> dict[str, Any]:
    response = []
    for diagram in DIAGRAMS.values():
        concepts = {concept.concept_id: concept for concept in diagram.concepts}
        response.append(
            DiagramResponse(
                diagram_id=diagram.diagram_id,
                title=diagram.title,
                description=diagram.description,
                image_url=diagram.image_url,
                image_aspect_ratio=diagram.image_aspect_ratio,
                regions=[
                    DiagramRegionResponse(
                        region_id=region.region_id,
                        concept_id=region.concept_id,
                        display_name=concepts[region.concept_id].display_name,
                        aliases=list(concepts[region.concept_id].aliases),
                        geometry=DiagramGeometry(
                            x=region.x,
                            y=region.y,
                            width=region.width,
                            height=region.height,
                        ),
                    )
                    for region in diagram.regions
                ],
            )
        )
    return {"diagrams": response}


@router.post(
    "/diagrams/{diagram_id}/regions/{region_id}/evidence",
    response_model=DiagramEvidenceResponse,
    tags=["diagrams"],
)
def diagram_region_evidence(
    request: Request,
    diagram_id: str,
    region_id: str,
    payload: DiagramEvidenceRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    diagram = get_diagram(diagram_id)
    if diagram is None:
        raise ApiError(404, "DIAGRAM_NOT_FOUND", "The requested diagram is not supported.")
    resolved = resolve_region(diagram_id, region_id)
    if resolved is None:
        raise ApiError(
            404,
            "DIAGRAM_REGION_NOT_FOUND",
            "The selected diagram region was not found.",
        )

    _, _, concept = resolved
    settings = _settings(request)
    if payload.limit > settings.max_retrieval_results:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"The result limit cannot exceed {settings.max_retrieval_results}.",
        )
    result = retrieve_with_mode(
        settings.database_path,
        query=concept.retrieval_query,
        document_ids=None,
        limit=payload.limit,
        mode=settings.retrieval_mode,
        settings=settings,
        embeddings=_embedding_service(request),
        user_id=current_user.user_id,
    )
    citations = result["results"]
    return {
        "status": "evidence_found" if citations else "insufficient_evidence",
        "diagramId": diagram_id,
        "regionId": region_id,
        "concept": DiagramConceptResponse(
            concept_id=concept.concept_id,
            display_name=concept.display_name,
            aliases=list(concept.aliases),
        ),
        "results": citations,
        "retrievalMode": result["retrievalMode"],
        "fallbackReason": result["fallbackReason"],
    }


@router.get("/embeddings/status", response_model=EmbeddingStatusResponse, tags=["embeddings"])
def embedding_status(
    request: Request, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    service = _embedding_service(request)
    try:
        service.load_model()
    except ModelUnavailable:
        pass
    try:
        return service.status(_database_path(request), user_id=current_user.user_id)
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503, "STORAGE_UNAVAILABLE", "Local embedding storage is unavailable."
        ) from exc


@router.post(
    "/embeddings/rebuild",
    response_model=RebuildEmbeddingsResponse,
    tags=["embeddings"],
)
def rebuild_embeddings(
    request: Request,
    payload: Annotated[RebuildEmbeddingsRequest | None, Body()] = None,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    try:
        return _embedding_service(request).rebuild(
            _database_path(request),
            force=payload.force if payload else False,
            user_id=current_user.user_id,
        )
    except ModelUnavailable as exc:
        raise ApiError(
            503,
            "EMBEDDING_MODEL_UNAVAILABLE",
            "The local embedding model is unavailable. Install and cache the configured model.",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503, "STORAGE_UNAVAILABLE", "Local embedding storage is unavailable."
        ) from exc
    except ValueError as exc:
        raise ApiError(
            503, "EMBEDDING_FAILED", "The embedding index could not be rebuilt."
        ) from exc


def make_error_response(
    status_code: int,
    code: str,
    message: str,
    details: object = None,
    request_id: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "requestId": request_id or uuid.uuid4().hex,
                "details": details,
            }
        },
    )
