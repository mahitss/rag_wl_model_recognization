"""M69: AI Lesson Planner & Teaching Studio API routes.

Endpoints:

* POST   /api/v1/lessons                                      - Create lesson plan
* GET    /api/v1/lessons                                      - List lesson plans
* GET    /api/v1/lessons/{lesson_plan_id}                     - Get one lesson plan
* PATCH  /api/v1/lessons/{lesson_plan_id}                     - Update lesson plan
* DELETE /api/v1/lessons/{lesson_plan_id}                     - Delete lesson plan
* POST   /api/v1/lessons/{lesson_plan_id}/sections            - Create section
* GET    /api/v1/lessons/{lesson_plan_id}/sections            - List sections
* POST   /api/v1/lessons/generate                             - AI generate lesson plan
* POST   /api/v1/lessons/sessions                             - Create lesson session
* GET    /api/v1/lessons/sessions                             - List lesson sessions
* GET    /api/v1/lessons/sessions/{session_id}                - Get one session
* PATCH  /api/v1/lessons/sessions/{session_id}                - Update session
* POST   /api/v1/lessons/sessions/{session_id}/attendance     - Record attendance
* GET    /api/v1/lessons/sessions/{session_id}/attendance     - List attendance
* POST   /api/v1/lessons/sessions/{session_id}/questions      - Record learner question
* GET    /api/v1/lessons/sessions/{session_id}/questions      - List questions

All endpoints require authentication and lesson/session ownership (where applicable).
"""
# ruff: noqa: B008,E501

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request

from app.auth import AuthUser, require_auth
from app.config import Settings
from app.content_studio import ContentGenerationError, generate_content
from app.errors import ApiError
from app.retrieval.service import retrieve_with_mode
from app.schemas import (
    LessonAttendanceRecord,
    LessonPlanCreateRequest,
    LessonPlanGenerateRequest,
    LessonPlanGenerateResponse,
    LessonPlanSectionCreateRequest,
    LessonPlanUpdateRequest,
    LessonQuestionRecord,
    LessonSessionCreateRequest,
    LessonSessionUpdateRequest,
)
from app.tutor import TutorUnavailable
from app.storage import (
    create_lesson_plan,
    create_lesson_plan_section,
    create_lesson_session,
    delete_lesson_plan,
    get_lesson_plan,
    get_lesson_session,
    list_lesson_attendance,
    list_lesson_plan_sections,
    list_lesson_plans,
    list_lesson_questions,
    list_lesson_sessions,
    record_lesson_attendance,
    record_lesson_question,
    update_lesson_plan,
    update_lesson_session,
)

router = APIRouter(prefix="/api/v1", tags=["lesson planner", "teaching studio"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Lesson plan CRUD
# ---------------------------------------------------------------------------


@router.post("/lessons", response_model=dict[str, Any], status_code=201)
def create_lesson_plan_route(
    request: Request,
    payload: LessonPlanCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new lesson plan."""
    plan = create_lesson_plan(
        _database_path(request),
        user_id=current_user.user_id,
        classroom_id=payload.classroomId,
        title=payload.title,
        subject=payload.subject,
        topic=payload.topic,
        student_level=payload.studentLevel,
        duration_minutes=payload.durationMinutes,
        status=payload.status,
        learning_objectives=payload.learningObjectives,
        prerequisite_concepts=payload.prerequisiteConcepts,
        source_material_refs=payload.sourceMaterialRefs,
    )
    if plan is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid lesson plan input.")
    return plan


@router.get("/lessons", response_model=dict[str, Any])
def list_lesson_plans_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = Query(None),
    classroom_id: str | None = Query(None),
) -> dict[str, Any]:
    """List lesson plans owned by the educator."""
    plans = list_lesson_plans(
        _database_path(request),
        user_id=current_user.user_id,
        status=status,
        classroom_id=classroom_id,
    )
    return {"lessonPlans": plans}


@router.get("/lessons/{lesson_plan_id}", response_model=dict[str, Any])
def get_lesson_plan_route(
    request: Request,
    lesson_plan_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one lesson plan if owned by the educator."""
    plan = get_lesson_plan(
        _database_path(request),
        lesson_plan_id=lesson_plan_id,
        user_id=current_user.user_id,
    )
    if plan is None:
        raise ApiError(404, "LESSON_PLAN_NOT_FOUND", "Lesson plan not found or access denied.")
    return plan


@router.patch("/lessons/{lesson_plan_id}", response_model=dict[str, Any])
def update_lesson_plan_route(
    request: Request,
    lesson_plan_id: str,
    payload: LessonPlanUpdateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a lesson plan. Owner only."""
    updated = update_lesson_plan(
        _database_path(request),
        lesson_plan_id=lesson_plan_id,
        user_id=current_user.user_id,
        title=payload.title,
        subject=payload.subject,
        topic=payload.topic,
        student_level=payload.studentLevel,
        duration_minutes=payload.durationMinutes,
        status=payload.status,
        learning_objectives=payload.learningObjectives,
        prerequisite_concepts=payload.prerequisiteConcepts,
        source_material_refs=payload.sourceMaterialRefs,
    )
    if updated is None:
        raise ApiError(404, "LESSON_PLAN_NOT_FOUND", "Lesson plan not found or access denied.")
    return updated


@router.delete("/lessons/{lesson_plan_id}", response_model=dict[str, Any])
def delete_lesson_plan_route(
    request: Request,
    lesson_plan_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a lesson plan. Owner only."""
    deleted = delete_lesson_plan(
        _database_path(request),
        lesson_plan_id=lesson_plan_id,
        user_id=current_user.user_id,
    )
    if not deleted:
        raise ApiError(404, "LESSON_PLAN_NOT_FOUND", "Lesson plan not found or access denied.")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Lesson plan sections
# ---------------------------------------------------------------------------


@router.post(
    "/lessons/{lesson_plan_id}/sections",
    response_model=dict[str, Any],
    status_code=201,
)
def create_lesson_plan_section_route(
    request: Request,
    lesson_plan_id: str,
    payload: LessonPlanSectionCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a section within a lesson plan. Owner only."""
    section = create_lesson_plan_section(
        _database_path(request),
        lesson_plan_id=lesson_plan_id,
        user_id=current_user.user_id,
        section_type=payload.sectionType,
        section_title=payload.sectionTitle,
        content=payload.content,
        citations=payload.citations,
        position=payload.position,
        order_index=payload.orderIndex,
    )
    if section is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid section input.")
    return section


@router.get("/lessons/{lesson_plan_id}/sections", response_model=dict[str, Any])
def list_lesson_plan_sections_route(
    request: Request,
    lesson_plan_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List sections for a lesson plan. Owner only."""
    sections = list_lesson_plan_sections(
        _database_path(request),
        lesson_plan_id=lesson_plan_id,
        user_id=current_user.user_id,
    )
    if sections is None:
        raise ApiError(404, "LESSON_PLAN_NOT_FOUND", "Lesson plan not found or access denied.")
    return {"sections": sections}


# ---------------------------------------------------------------------------
# AI lesson plan generation
# ---------------------------------------------------------------------------


@router.post(
    "/lessons/generate",
    response_model=dict[str, Any],
    status_code=201,
)
def generate_lesson_plan_route(
    request: Request,
    payload: LessonPlanGenerateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """AI-generate a lesson plan from retrieved evidence.

    Retrieves source passages, generates a structured lesson plan using the
    local tutor, and persists it as a draft lesson plan. Every section carries
    source citations and is labelled unverified until educator review.
    """
    settings = _settings(request)
    embeddings = request.app.state.embedding_service
    database_path = _database_path(request)

    topic = " ".join(payload.topic.split())
    if not topic:
        raise ApiError(422, "INVALID_TOPIC", "The topic must contain at least one searchable term.")

    # Retrieve evidence scoped to the user's owned documents / workspace.
    try:
        search = retrieve_with_mode(
            database_path,
            query=topic,
            document_ids=payload.documentIds,
            limit=payload.limit,
            mode=payload.mode,
            settings=settings,
            embeddings=embeddings,
            user_id=current_user.user_id,
        )
    except (OSError, Exception) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage or retrieval is unavailable.") from exc

    evidence = search["results"]
    source_refs = [
        {
            "type": "document",
            "documentId": item["documentId"],
            "displayFilename": item["displayFilename"],
            "pageNumber": item["pageNumber"],
            "chunkIndex": item["chunkIndex"],
            "retrieval": item["retrieval"],
        }
        for item in evidence
    ]

    # Build an evidence pack suitable for the content studio generator.
    evidence_pack = [
        {"label": f"E{index}", "text": item["text"]}
        for index, item in enumerate(evidence, start=1)
    ]

    if not evidence_pack:
        return {
            "status": "insufficient_evidence",
            "lessonPlanId": None,
            "topic": topic,
            "evidenceGaps": [
                "No supporting passages were found for this topic."
            ],
            "retrievalMode": settings.retrieval_mode,
        }

    # Generate teaching content for each standard lesson-plan section.
    section_types = [
        ("introduction", "brief"),
        ("concept_explanation", "standard"),
        ("teaching_sequence", "standard"),
        ("example_activity", "brief"),
        ("practice_question", "brief"),
        ("assessment_checkpoint", "brief"),
        ("conclusion", "brief"),
        ("follow_up_revision", "brief"),
    ]

    generated_sections: list[dict[str, Any]] = []
    evidence_gaps: list[str] = []
    abstained = False

    for section_type, detail_level in section_types:
        try:
            generated = generate_content(
                settings,
                topic=f"{topic} - {section_type.replace('_', ' ')}",
                format="concept_outline",
                detail_level=detail_level,
                evidence=evidence_pack,
            )
        except ContentGenerationError:
            generated_sections.append({
                "sectionType": section_type,
                "sectionTitle": section_type.replace("_", " ").title(),
                "content": "Content could not be generated for this section.",
                "citations": [],
            })
            continue
        except TutorUnavailable:
            generated_sections.append({
                "sectionType": section_type,
                "sectionTitle": section_type.replace("_", " ").title(),
                "content": "Local tutor unavailable; section requires manual authoring.",
                "citations": [],
            })
            continue

        if generated.get("abstain"):
            abstained = True
            evidence_gaps.extend(generated.get("evidenceGaps", []))
            generated_sections.append({
                "sectionType": section_type,
                "sectionTitle": section_type.replace("_", " ").title(),
                "content": "Insufficient evidence to generate this section.",
                "citations": [],
            })
        else:
            for section in generated.get("sections", []):
                generated_sections.append({
                    "sectionType": section_type,
                    "sectionTitle": section.get("sectionTitle", section_type.replace("_", " ").title()),
                    "content": section.get("content", ""),
                    "citations": section.get("citations", []),
                })

    # Persist as a draft lesson plan.
    title = f"{subject} Lesson: {topic}"
    plan = create_lesson_plan(
        _database_path(request),
        user_id=current_user.user_id,
        classroom_id=None,
        title=title,
        subject=payload.subject,
        topic=topic,
        student_level=payload.studentLevel,
        duration_minutes=payload.durationMinutes,
        status="draft",
        learning_objectives=None,
        prerequisite_concepts=None,
        source_material_refs=source_refs,
    )
    if plan is None:
        raise ApiError(422, "INVALID_REQUEST", "Failed to persist generated lesson plan.")

    lesson_plan_id = plan["lessonPlanId"]

    # Persist generated sections.
    position = 0
    for section in generated_sections:
        create_lesson_plan_section(
            _database_path(request),
            lesson_plan_id=lesson_plan_id,
            user_id=current_user.user_id,
            section_type=section["sectionType"],
            section_title=section["sectionTitle"],
            content=section["content"],
            citations=section.get("citations", []),
            position=position,
            order_index=position,
        )
        position += 1

    return {
        "lessonPlanId": lesson_plan_id,
        "userId": current_user.user_id,
        "title": title,
        "subject": payload.subject,
        "topic": topic,
        "studentLevel": payload.studentLevel,
        "durationMinutes": payload.durationMinutes,
        "sections": generated_sections,
        "evidenceGaps": evidence_gaps,
        "status": "draft",
        "createdAt": plan["createdAt"],
    }


# ---------------------------------------------------------------------------
# Lesson sessions
# ---------------------------------------------------------------------------


@router.post(
    "/lessons/sessions",
    response_model=dict[str, Any],
    status_code=201,
)
def create_lesson_session_route(
    request: Request,
    payload: LessonSessionCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a scheduled or in-progress lesson session."""
    session = create_lesson_session(
        _database_path(request),
        lesson_plan_id=payload.lessonPlanId,
        user_id=current_user.user_id,
        classroom_id=payload.classroomId,
        title=payload.title,
        scheduled_at=payload.scheduledAt,
        status=payload.status,
    )
    if session is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid session input or lesson plan not found/owned.")
    return session


@router.get("/lessons/sessions", response_model=dict[str, Any])
def list_lesson_sessions_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    lesson_plan_id: str | None = Query(None),
    classroom_id: str | None = Query(None),
    status: str | None = Query(None),
) -> dict[str, Any]:
    """List lesson sessions."""
    sessions = list_lesson_sessions(
        _database_path(request),
        lesson_plan_id=lesson_plan_id,
        user_id=current_user.user_id,
        classroom_id=classroom_id,
        status=status,
    )
    return {"sessions": sessions}


@router.get("/lessons/sessions/{session_id}", response_model=dict[str, Any])
def get_lesson_session_route(
    request: Request,
    session_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one lesson session if owned by the educator."""
    session = get_lesson_session(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if session is None:
        raise ApiError(404, "LESSON_SESSION_NOT_FOUND", "Lesson session not found or access denied.")
    return session


@router.patch("/lessons/sessions/{session_id}", response_model=dict[str, Any])
def update_lesson_session_route(
    request: Request,
    session_id: str,
    payload: LessonSessionUpdateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a lesson session. Owner only."""
    updated = update_lesson_session(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
        title=payload.title,
        scheduled_at=payload.scheduledAt,
        status=payload.status,
        started_at=payload.startedAt,
        completed_at=payload.completedAt,
    )
    if updated is None:
        raise ApiError(404, "LESSON_SESSION_NOT_FOUND", "Lesson session not found or access denied.")
    return updated


# ---------------------------------------------------------------------------
# Lesson attendance
# ---------------------------------------------------------------------------


@router.post(
    "/lessons/sessions/{session_id}/attendance",
    response_model=dict[str, Any],
    status_code=201,
)
def record_lesson_attendance_route(
    request: Request,
    session_id: str,
    payload: LessonAttendanceRecord = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record learner attendance/participation in a lesson session."""
    attendance = record_lesson_attendance(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
        joined_at=payload.joinedAt,
        left_at=payload.leftAt,
        participation_events=payload.participationEvents,
    )
    if attendance is None:
        raise ApiError(404, "LESSON_SESSION_NOT_FOUND", "Lesson session not found.")
    return attendance


@router.get(
    "/lessons/sessions/{session_id}/attendance",
    response_model=dict[str, Any],
)
def list_lesson_attendance_route(
    request: Request,
    session_id: str,
    current_user: AuthUser = Depends(require_auth),
    user_id: str | None = Query(None),
) -> dict[str, Any]:
    """List attendance records for a session."""
    attendance = list_lesson_attendance(
        _database_path(request),
        session_id=session_id,
        user_id=user_id,
    )
    if attendance is None:
        raise ApiError(404, "LESSON_SESSION_NOT_FOUND", "Lesson session not found.")
    return {"attendance": attendance}


# ---------------------------------------------------------------------------
# Lesson questions
# ---------------------------------------------------------------------------


@router.post(
    "/lessons/sessions/{session_id}/questions",
    response_model=dict[str, Any],
    status_code=201,
)
def record_lesson_question_route(
    request: Request,
    session_id: str,
    payload: LessonQuestionRecord = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record a learner question during a lesson session."""
    question = record_lesson_question(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
        question_text=payload.questionText,
        answered_at=payload.answeredAt,
        answer_text=payload.answerText,
    )
    if question is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid question input or session not found.")
    return question


@router.get(
    "/lessons/sessions/{session_id}/questions",
    response_model=dict[str, Any],
)
def list_lesson_questions_route(
    request: Request,
    session_id: str,
    current_user: AuthUser = Depends(require_auth),
    user_id: str | None = Query(None),
) -> dict[str, Any]:
    """List learner questions for a session."""
    questions = list_lesson_questions(
        _database_path(request),
        session_id=session_id,
        user_id=user_id,
    )
    if questions is None:
        raise ApiError(404, "LESSON_SESSION_NOT_FOUND", "Lesson session not found.")
    return {"questions": questions}
