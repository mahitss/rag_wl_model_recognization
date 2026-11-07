"""M63: Collaborative AI Course Builder & Curriculum Generator API routes.

Endpoints:

* POST   /api/v1/courses                                      - Create a course
* GET    /api/v1/courses                                      - List courses
* GET    /api/v1/courses/{course_id}                          - Get one course
* PATCH  /api/v1/courses/{course_id}                          - Update a course
* DELETE /api/v1/courses/{course_id}                          - Delete a course
* POST   /api/v1/courses/{course_id}/modules                  - Create module
* GET    /api/v1/courses/{course_id}/modules                  - List modules
* POST   /api/v1/courses/{course_id}/modules/{module_id}/topics - Create topic
* GET    /api/v1/courses/{course_id}/modules/{module_id}/topics - List topics
* POST   /api/v1/courses/{course_id}/objectives               - Create objective
* GET    /api/v1/courses/{course_id}/objectives               - List objectives
* POST   /api/v1/courses/{course_id}/assessments              - Create assessment
* GET    /api/v1/courses/{course_id}/assessments              - List assessments
* POST   /api/v1/courses/{course_id}/progress                 - Create/update progress
* GET    /api/v1/courses/{course_id}/progress                 - Get progress
* PATCH  /api/v1/courses/{course_id}/progress/{topic_id}      - Update progress
* POST   /api/v1/courses/build                                - Build course from evidence (AI)
* GET    /api/v1/courses/{course_id}/curriculum               - Get curriculum
* GET    /api/v1/courses/{course_id}/next-topic               - Get next topic
* GET    /api/v1/courses/{course_id}/recommendations          - Get recommendations
* POST   /api/v1/courses/{course_id}/content                  - Generate content

All endpoints require authentication and course ownership (where applicable).
"""
# ruff: noqa: B008,E501

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request

from app.ai_course import (
    build_course_from_evidence,
    build_curriculum,
    get_adaptive_course_recommendations,
    get_next_topic,
)
from app.auth import AuthUser, require_auth
from app.config import Settings
from app.errors import ApiError
from app.storage import (
    create_course,
    create_course_assessment,
    create_course_module,
    create_course_objective,
    create_course_progress,
    create_course_topic,
    delete_course,
    get_course,
    get_course_progress,
    list_course_assessments,
    list_course_modules,
    list_course_objectives,
    list_course_topics,
    list_courses,
    update_course,
    update_course_progress,
)

router = APIRouter(prefix="/api/v1/courses", tags=["courses"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _course_settings(request: Request) -> Settings:
    return request.app.state.settings


def _check_membership_or_404(
    database_path: str, course_id: str, user_id: str
) -> None:
    course = get_course(database_path, course_id=course_id, user_id=user_id)
    if course is None:
        raise ApiError(404, "COURSE_NOT_FOUND", "Course not found or access denied.")


# ---------------------------------------------------------------------------
# Course CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=dict[str, Any], status_code=201)
def create_course_route(
    request: Request,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new course."""
    title = payload.get("title", "")
    description = payload.get("description", "")
    space_id = payload.get("spaceId")
    source_type = payload.get("sourceType", "document")
    difficulty_level = payload.get("difficultyLevel")
    estimated_workload_minutes = payload.get("estimatedWorkloadMinutes")

    if not title:
        raise ApiError(422, "INVALID_REQUEST", "title is required.")

    course = create_course(
        _database_path(request),
        user_id=current_user.user_id,
        space_id=space_id,
        title=title,
        description=description,
        source_type=source_type,
        source_refs=payload.get("sourceRefs", []),
        difficulty_level=difficulty_level,
        estimated_workload_minutes=estimated_workload_minutes,
    )
    if course is None:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "You are not a member of the specified space, or invalid input.",
        )
    return course


@router.get("", response_model=dict[str, Any])
def list_courses_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = Query(None),
) -> dict[str, Any]:
    """List courses owned by the user or shared via a space they belong to."""
    if status is not None and status not in {"draft", "active", "completed", "archived"}:
        raise ApiError(422, "INVALID_STATUS", "Invalid status.")
    courses = list_courses(
        _database_path(request),
        user_id=current_user.user_id,
        status=status,
    )
    return {"courses": courses}


@router.get("/{course_id}", response_model=dict[str, Any])
def get_course_route(
    request: Request,
    course_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one course if the user owns it or is a member of its space."""
    course = get_course(
        _database_path(request), course_id=course_id, user_id=current_user.user_id
    )
    if course is None:
        raise ApiError(404, "COURSE_NOT_FOUND", "Course not found or access denied.")
    return course


@router.patch("/{course_id}", response_model=dict[str, Any])
def update_course_route(
    request: Request,
    course_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a course. Owner only."""
    updated = update_course(
        _database_path(request),
        course_id=course_id,
        user_id=current_user.user_id,
        title=payload.get("title"),
        description=payload.get("description"),
        status=payload.get("status"),
        difficulty_level=payload.get("difficultyLevel"),
        estimated_workload_minutes=payload.get("estimatedWorkloadMinutes"),
    )
    if updated is None:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Course not found or you are not the owner.",
        )
    return updated


@router.delete("/{course_id}")
def delete_course_route(
    request: Request,
    course_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a course. Owner only."""
    deleted = delete_course(
        _database_path(request),
        course_id=course_id,
        user_id=current_user.user_id,
    )
    if not deleted:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Course not found or you are not the owner.",
        )
    return {"courseId": course_id, "deleted": True}


# ---------------------------------------------------------------------------
# Course modules
# ---------------------------------------------------------------------------


@router.post("/{course_id}/modules", response_model=dict[str, Any], status_code=201)
def create_module_route(
    request: Request,
    course_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a module within a course. Owner only."""
    module = create_course_module(
        _database_path(request),
        course_id=course_id,
        user_id=current_user.user_id,
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        order_index=payload.get("orderIndex", 0),
    )
    if module is None:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Course not found, you are not the owner, or invalid input.",
        )
    return module


@router.get("/{course_id}/modules", response_model=dict[str, Any])
def list_modules_route(
    request: Request,
    course_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List modules for a course. Owner only."""
    modules = list_course_modules(
        _database_path(request),
        course_id=course_id,
        user_id=current_user.user_id,
    )
    if modules is None:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Course not found or you are not the owner.",
        )
    return {"modules": modules}


# ---------------------------------------------------------------------------
# Course topics
# ---------------------------------------------------------------------------


@router.post(
    "/{course_id}/modules/{module_id}/topics",
    response_model=dict[str, Any],
    status_code=201,
)
def create_topic_route(
    request: Request,
    course_id: str,
    module_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a topic within a module. Owner only."""
    topic = create_course_topic(
        _database_path(request),
        module_id=module_id,
        user_id=current_user.user_id,
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        order_index=payload.get("orderIndex", 0),
        prerequisite_topic_ids=payload.get("prerequisiteTopicIds", []),
        difficulty_level=payload.get("difficultyLevel"),
        estimated_minutes=payload.get("estimatedMinutes"),
    )
    if topic is None:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Module not found, you are not the owner, or invalid input.",
        )
    return topic


@router.get("/{course_id}/modules/{module_id}/topics", response_model=dict[str, Any])
def list_topics_route(
    request: Request,
    course_id: str,
    module_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List topics within a module. Owner only."""
    topics = list_course_topics(
        _database_path(request),
        module_id=module_id,
        user_id=current_user.user_id,
    )
    if topics is None:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Module not found or you are not the owner.",
        )
    return {"topics": topics}


# ---------------------------------------------------------------------------
# Course objectives
# ---------------------------------------------------------------------------


@router.post("/{course_id}/objectives", response_model=dict[str, Any], status_code=201)
def create_objective_route(
    request: Request,
    course_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a learning objective within a course. Owner only."""
    objective = create_course_objective(
        _database_path(request),
        course_id=course_id,
        user_id=current_user.user_id,
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        order_index=payload.get("orderIndex", 0),
    )
    if objective is None:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Course not found, you are not the owner, or invalid input.",
        )
    return objective


@router.get("/{course_id}/objectives", response_model=dict[str, Any])
def list_objectives_route(
    request: Request,
    course_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List objectives for a course. Owner only."""
    objectives = list_course_objectives(
        _database_path(request),
        course_id=course_id,
        user_id=current_user.user_id,
    )
    if objectives is None:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Course not found or you are not the owner.",
        )
    return {"objectives": objectives}


# ---------------------------------------------------------------------------
# Course assessments
# ---------------------------------------------------------------------------


@router.post("/{course_id}/assessments", response_model=dict[str, Any], status_code=201)
def create_assessment_route(
    request: Request,
    course_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a course assessment. Owner only."""
    assessment = create_course_assessment(
        _database_path(request),
        course_id=course_id,
        user_id=current_user.user_id,
        topic_id=payload.get("topicId"),
        assessment_type=payload.get("assessmentType", "quiz"),
        quiz_id=payload.get("quizId"),
    )
    if assessment is None:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Course not found, you are not the owner, or invalid assessment type.",
        )
    return assessment


@router.get("/{course_id}/assessments", response_model=dict[str, Any])
def list_assessments_route(
    request: Request,
    course_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List assessments for a course. Owner only."""
    assessments = list_course_assessments(
        _database_path(request),
        course_id=course_id,
        user_id=current_user.user_id,
    )
    if assessments is None:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Course not found or you are not the owner.",
        )
    return {"assessments": assessments}


# ---------------------------------------------------------------------------
# Course progress
# ---------------------------------------------------------------------------


@router.post("/{course_id}/progress", response_model=dict[str, Any], status_code=201)
def create_progress_route(
    request: Request,
    course_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create or update progress for a course topic. Owner only."""
    progress = create_course_progress(
        _database_path(request),
        course_id=course_id,
        user_id=current_user.user_id,
        topic_id=payload.get("topicId"),
        status=payload.get("status", "not_started"),
        mastery_score=payload.get("masteryScore"),
        completed_at=payload.get("completedAt"),
    )
    if progress is None:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Course not found, you are not the owner, or invalid input.",
        )
    return progress


@router.get("/{course_id}/progress", response_model=dict[str, Any])
def list_progress_route(
    request: Request,
    course_id: str,
    current_user: AuthUser = Depends(require_auth),
    topicId: str | None = Query(None),
) -> dict[str, Any]:
    """List progress for a course. Owner only."""
    progress = get_course_progress(
        _database_path(request),
        course_id=course_id,
        user_id=current_user.user_id,
        topic_id=topicId,
    )
    if progress is None:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Course not found or you are not the owner.",
        )
    if isinstance(progress, list):
        return {"progress": progress}
    return {"progress": [progress]}


@router.patch("/{course_id}/progress/{topic_id}", response_model=dict[str, Any])
def update_progress_route(
    request: Request,
    course_id: str,
    topic_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update progress for a course topic. Owner only."""
    progress = update_course_progress(
        _database_path(request),
        course_id=course_id,
        user_id=current_user.user_id,
        topic_id=topic_id,
        status=payload.get("status"),
        mastery_score=payload.get("masteryScore"),
        completed_at=payload.get("completedAt"),
    )
    if progress is None:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Course not found, you are not the owner, or invalid input.",
        )
    return progress


# ---------------------------------------------------------------------------
# AI-powered course building
# ---------------------------------------------------------------------------


@router.post("/build", response_model=dict[str, Any], status_code=201)
async def build_course_route(
    request: Request,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Build a course from retrieved evidence using AI.

    The caller supplies a topic and optional document IDs. The system retrieves
    supporting passages, generates course content via the content studio, and
    persists a course with modules and topics.
    """
    settings = _course_settings(request)
    embeddings = request.app.state.embedding_service
    topic = payload.get("topic", "")
    if not topic:
        raise ApiError(422, "INVALID_REQUEST", "topic is required.")

    course = build_course_from_evidence(
        settings,
        _database_path(request),
        user_id=current_user.user_id,
        space_id=payload.get("spaceId"),
        topic=topic,
        document_ids=payload.get("documentIds"),
        limit=payload.get("limit", 10),
        mode=payload.get("mode"),
        difficulty_level=payload.get("difficultyLevel"),
        embeddings=embeddings,
    )
    if course.get("courseId") is None:
        return {
            "courseId": None,
            "userId": current_user.user_id,
            "spaceId": payload.get("spaceId"),
            "title": course.get("title", topic),
            "description": course.get("description", ""),
            "sourceType": "document",
            "sourceRefs": course.get("sourceRefs", []),
            "status": course.get("status", "insufficient_evidence"),
            "difficultyLevel": payload.get("difficultyLevel"),
            "estimatedWorkloadMinutes": None,
            "createdAt": "",
            "updatedAt": "",
            "modules": course.get("modules", []),
        }
    return course


@router.get("/{course_id}/curriculum", response_model=dict[str, Any])
def get_curriculum_route(
    request: Request,
    course_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the full curriculum for a course."""
    curriculum = build_curriculum(
        _database_path(request),
        course_id=course_id,
        user_id=current_user.user_id,
    )
    if curriculum is None:
        raise ApiError(
            403,
            "COURSE_FORBIDDEN",
            "Course not found or you are not the owner.",
        )
    return curriculum


@router.get("/{course_id}/next-topic", response_model=dict[str, Any])
def get_next_topic_route(
    request: Request,
    course_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the next topic to study in a course."""
    next_topic = get_next_topic(
        _database_path(request),
        course_id=course_id,
        user_id=current_user.user_id,
    )
    if next_topic is None:
        return {
            "moduleId": None,
            "moduleTitle": None,
            "topicId": None,
            "title": None,
            "description": None,
            "difficultyLevel": None,
            "estimatedMinutes": None,
            "prerequisitesMet": None,
        }
    return next_topic


@router.get("/{course_id}/recommendations", response_model=dict[str, Any])
def get_recommendations_route(
    request: Request,
    course_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return adaptive recommendations for a course."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    recommendations = get_adaptive_course_recommendations(
        _database_path(request),
        user_id=current_user.user_id,
        course_id=course_id,
        now=now,
        limit=5,
    )
    return {"recommendations": recommendations}


# ---------------------------------------------------------------------------
# AI-powered course content generation
# ---------------------------------------------------------------------------


@router.post("/{course_id}/content", response_model=dict[str, Any])
async def generate_course_content_route(
    request: Request,
    course_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Generate content for a course topic using AI.

    Grounded in the course's source material with citations preserved.
    """
    course = get_course(
        _database_path(request), course_id=course_id, user_id=current_user.user_id
    )
    if course is None:
        raise ApiError(404, "COURSE_NOT_FOUND", "Course not found or access denied.")
    topic_id = payload.get("topicId", "")
    topic_title = payload.get("topicTitle", "")

    settings = _course_settings(request)
    embeddings = request.app.state.embedding_service

    # Retrieve evidence for the topic within the course's source documents.
    from app.retrieval.service import retrieve_with_mode

    search = retrieve_with_mode(
        _database_path(request),
        query=topic_title or course["title"],
        document_ids=None,
        limit=10,
        mode=settings.retrieval_mode,
        settings=settings,
        embeddings=embeddings,
        user_id=current_user.user_id,
    )
    evidence = search["results"]

    from app.content_studio import generate_content

    try:
        generated = await __import__("asyncio").to_thread(
            generate_content,
            settings,
            topic=topic_title or course["title"],
            format="concept_outline",
            detail_level="comprehensive",
            evidence=evidence,
        )
    except Exception:
        return {
            "courseId": course_id,
            "topicId": topic_id,
            "sectionId": None,
            "title": topic_title or course["title"],
            "content": "",
            "citations": [],
            "status": "insufficient_evidence",
        }

    if generated.get("abstain"):
        return {
            "courseId": course_id,
            "topicId": topic_id,
            "sectionId": None,
            "title": topic_title or course["title"],
            "content": "",
            "citations": [],
            "status": "insufficient_evidence",
        }

    sections = generated.get("sections", [])
    section = sections[0] if sections else {}
    return {
        "courseId": course_id,
        "topicId": topic_id,
        "sectionId": None,
        "title": topic_title or course["title"],
        "content": section.get("content", ""),
        "citations": section.get("citations", []),
        "status": "generated",
    }
