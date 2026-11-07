"""M64: Collaborative AI Assessment & Competency Management API routes.

Endpoints:

* POST   /api/v1/assessments                                      - Create assessment
* GET    /api/v1/assessments                                      - List assessments
* GET    /api/v1/assessments/{assessment_id}                      - Get one assessment
* PATCH  /api/v1/assessments/{assessment_id}                      - Update assessment
* DELETE /api/v1/assessments/{assessment_id}                      - Delete assessment
* POST   /api/v1/assessments/{assessment_id}/questions            - Create question
* GET    /api/v1/assessments/{assessment_id}/questions            - List questions
* GET    /api/v1/assessments/{assessment_id}/questions/{q_id}     - Get one question
* POST   /api/v1/assessments/{assessment_id}/attempts             - Start attempt
* GET    /api/v1/assessments/{assessment_id}/attempts             - List attempts
* GET    /api/v1/attempts/{attempt_id}                            - Get one attempt
* POST   /api/v1/attempts/{attempt_id}/questions/{q_id}/answers   - Record answer
* GET    /api/v1/attempts/{attempt_id}/answers                    - List answers
* POST   /api/v1/competencies                                     - Create competency
* GET    /api/v1/competencies                                     - List competencies
* GET    /api/v1/competencies/{competency_id}                     - Get one competency
* PATCH  /api/v1/competencies/{competency_id}                     - Update competency
* DELETE /api/v1/competencies/{competency_id}                     - Delete competency
* POST   /api/v1/competencies/{competency_id}/prerequisites       - Add prerequisite
* GET    /api/v1/competencies/{competency_id}/prerequisites       - List prerequisites
* POST   /api/v1/objectives/{objective_id}/competencies           - Link objective->competency
* POST   /api/v1/questions/{question_id}/competencies             - Link question->competency
* GET    /api/v1/competencies/{competency_id}/mastery             - Get mastery
* PATCH  /api/v1/competencies/{competency_id}/mastery             - Update mastery

All endpoints require authentication and ownership (where applicable).
"""
# ruff: noqa: B008,E501

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request

from app.auth import AuthUser, require_auth
from app.errors import ApiError
from app.storage import (
    add_competency_prerequisite,
    create_assessment,
    create_assessment_question,
    create_competency,
    delete_assessment,
    delete_competency,
    get_assessment,
    get_assessment_attempt,
    get_assessment_question,
    get_competency,
    link_objective_competency,
    link_question_competency,
    list_assessment_attempts,
    list_assessment_questions,
    list_assessments,
    list_competencies,
    list_competency_prerequisites,
    record_assessment_attempt,
    record_assessment_question_answer,
    update_assessment,
    update_competency,
    update_competency_mastery,
)

router = APIRouter(prefix="/api/v1", tags=["assessments", "competencies"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


# ---------------------------------------------------------------------------
# Assessment CRUD
# ---------------------------------------------------------------------------


@router.post("/assessments", response_model=dict[str, Any], status_code=201)
def create_assessment_route(
    request: Request,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new assessment."""
    assessment = create_assessment(
        _database_path(request),
        user_id=current_user.user_id,
        course_id=payload.get("courseId"),
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        assessment_type=payload.get("assessmentType", "custom"),
        status=payload.get("status", "draft"),
    )
    if assessment is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid assessment input or course not found/owned.",
        )
    return assessment


@router.get("/assessments", response_model=dict[str, Any])
def list_assessments_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    course_id: str | None = Query(None),
    status: str | None = Query(None),
) -> dict[str, Any]:
    """List assessments owned by the user."""
    assessments = list_assessments(
        _database_path(request),
        user_id=current_user.user_id,
        course_id=course_id,
        status=status,
    )
    return {"assessments": assessments}


@router.get("/assessments/{assessment_id}", response_model=dict[str, Any])
def get_assessment_route(
    request: Request,
    assessment_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one assessment if the user owns it."""
    assessment = get_assessment(
        _database_path(request),
        assessment_id=assessment_id,
        user_id=current_user.user_id,
    )
    if assessment is None:
        raise ApiError(404, "ASSESSMENT_NOT_FOUND", "Assessment not found or access denied.")
    return assessment


@router.patch("/assessments/{assessment_id}", response_model=dict[str, Any])
def update_assessment_route(
    request: Request,
    assessment_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update an assessment. Owner only."""
    updated = update_assessment(
        _database_path(request),
        assessment_id=assessment_id,
        user_id=current_user.user_id,
        title=payload.get("title"),
        description=payload.get("description"),
        status=payload.get("status"),
    )
    if updated is None:
        raise ApiError(404, "ASSESSMENT_NOT_FOUND", "Assessment not found or access denied.")
    return updated


@router.delete("/assessments/{assessment_id}", response_model=dict[str, Any])
def delete_assessment_route(
    request: Request,
    assessment_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete an assessment. Owner only."""
    deleted = delete_assessment(
        _database_path(request),
        assessment_id=assessment_id,
        user_id=current_user.user_id,
    )
    if not deleted:
        raise ApiError(404, "ASSESSMENT_NOT_FOUND", "Assessment not found or access denied.")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Assessment questions
# ---------------------------------------------------------------------------


@router.post(
    "/assessments/{assessment_id}/questions",
    response_model=dict[str, Any],
    status_code=201,
)
def create_assessment_question_route(
    request: Request,
    assessment_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a question within an assessment. Owner only."""
    question = create_assessment_question(
        _database_path(request),
        assessment_id=assessment_id,
        user_id=current_user.user_id,
        question_type=payload.get("questionType", ""),
        prompt=payload.get("prompt", ""),
        answer_key=payload.get("answerKey", ""),
        accepted_answers=payload.get("acceptedAnswers"),
        explanation=payload.get("explanation"),
        difficulty_level=payload.get("difficultyLevel"),
        citations=payload.get("citations"),
    )
    if question is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid question input or assessment not found/owned.",
        )
    return question


@router.get(
    "/assessments/{assessment_id}/questions",
    response_model=dict[str, Any],
)
def list_assessment_questions_route(
    request: Request,
    assessment_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List questions for an assessment. Owner only."""
    questions = list_assessment_questions(
        _database_path(request),
        assessment_id=assessment_id,
        user_id=current_user.user_id,
    )
    if questions is None:
        raise ApiError(404, "ASSESSMENT_NOT_FOUND", "Assessment not found or access denied.")
    return {"questions": questions}


@router.get(
    "/assessments/{assessment_id}/questions/{question_id}",
    response_model=dict[str, Any],
)
def get_assessment_question_route(
    request: Request,
    assessment_id: str,
    question_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one question if the user owns its assessment."""
    question = get_assessment_question(
        _database_path(request),
        question_id=question_id,
        user_id=current_user.user_id,
    )
    if question is None or question["assessmentId"] != assessment_id:
        raise ApiError(404, "QUESTION_NOT_FOUND", "Question not found or access denied.")
    return question


# ---------------------------------------------------------------------------
# Assessment attempts
# ---------------------------------------------------------------------------


@router.post(
    "/assessments/{assessment_id}/attempts",
    response_model=dict[str, Any],
    status_code=201,
)
def record_assessment_attempt_route(
    request: Request,
    assessment_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Start or update an assessment attempt. Owner only."""
    attempt = record_assessment_attempt(
        _database_path(request),
        assessment_id=assessment_id,
        user_id=current_user.user_id,
        status=payload.get("status", "in_progress"),
    )
    if attempt is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid attempt input or assessment not found/owned.",
        )
    return attempt


@router.get(
    "/assessments/{assessment_id}/attempts",
    response_model=dict[str, Any],
)
def list_assessment_attempts_route(
    request: Request,
    assessment_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List attempts for an assessment. Owner only."""
    attempts = list_assessment_attempts(
        _database_path(request),
        assessment_id=assessment_id,
        user_id=current_user.user_id,
    )
    return {"attempts": attempts}


@router.get("/attempts/{attempt_id}", response_model=dict[str, Any])
def get_assessment_attempt_route(
    request: Request,
    attempt_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one attempt if owned by the user."""
    attempt = get_assessment_attempt(
        _database_path(request),
        attempt_id=attempt_id,
        user_id=current_user.user_id,
    )
    if attempt is None:
        raise ApiError(404, "ATTEMPT_NOT_FOUND", "Attempt not found or access denied.")
    return attempt


@router.post(
    "/attempts/{attempt_id}/questions/{question_id}/answers",
    response_model=dict[str, Any],
    status_code=201,
)
def record_assessment_question_answer_route(
    request: Request,
    attempt_id: str,
    question_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record an answer to a question in an attempt. Owner only."""
    answer = record_assessment_question_answer(
        _database_path(request),
        attempt_id=attempt_id,
        question_id=question_id,
        user_id=current_user.user_id,
        question_type=payload.get("questionType", ""),
        submitted_answer=payload.get("submittedAnswer", ""),
        answer_key=payload.get("answerKey"),
        accepted_answers=payload.get("acceptedAnswers"),
        explanation=payload.get("explanation"),
        result=payload.get("result"),
        score=payload.get("score"),
        citations=payload.get("citations"),
    )
    if answer is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid answer input, attempt or question not found, or access denied.",
        )
    return answer


@router.get(
    "/attempts/{attempt_id}/answers",
    response_model=dict[str, Any],
)
def list_assessment_question_answers_route(
    request: Request,
    attempt_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List question attempt results for an attempt. Owner only."""
    answers = list_assessment_question_attempts(
        _database_path(request),
        attempt_id=attempt_id,
        user_id=current_user.user_id,
    )
    if answers is None:
        raise ApiError(404, "ATTEMPT_NOT_FOUND", "Attempt not found or access denied.")
    return {"answers": answers}


# ---------------------------------------------------------------------------
# Competencies
# ---------------------------------------------------------------------------


@router.post("/competencies", response_model=dict[str, Any], status_code=201)
def create_competency_route(
    request: Request,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a competency."""
    competency = create_competency(
        _database_path(request),
        user_id=current_user.user_id,
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        parent_competency_id=payload.get("parentCompetencyId"),
        level=payload.get("level", 0),
    )
    if competency is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid competency input.")
    return competency


@router.get("/competencies", response_model=dict[str, Any])
def list_competencies_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List competencies owned by the user."""
    competencies = list_competencies(
        _database_path(request), user_id=current_user.user_id
    )
    return {"competencies": competencies}


@router.get("/competencies/{competency_id}", response_model=dict[str, Any])
def get_competency_route(
    request: Request,
    competency_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one competency if owned by the user."""
    competency = get_competency(
        _database_path(request),
        competency_id=competency_id,
        user_id=current_user.user_id,
    )
    if competency is None:
        raise ApiError(404, "COMPETENCY_NOT_FOUND", "Competency not found or access denied.")
    return competency


@router.patch("/competencies/{competency_id}", response_model=dict[str, Any])
def update_competency_route(
    request: Request,
    competency_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a competency. Owner only."""
    updated = update_competency(
        _database_path(request),
        competency_id=competency_id,
        user_id=current_user.user_id,
        title=payload.get("title"),
        description=payload.get("description"),
        parent_competency_id=payload.get("parentCompetencyId"),
        level=payload.get("level"),
    )
    if updated is None:
        raise ApiError(404, "COMPETENCY_NOT_FOUND", "Competency not found or access denied.")
    return updated


@router.delete("/competencies/{competency_id}", response_model=dict[str, Any])
def delete_competency_route(
    request: Request,
    competency_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a competency. Owner only."""
    deleted = delete_competency(
        _database_path(request),
        competency_id=competency_id,
        user_id=current_user.user_id,
    )
    if not deleted:
        raise ApiError(404, "COMPETENCY_NOT_FOUND", "Competency not found or access denied.")
    return {"status": "deleted"}


@router.post(
    "/competencies/{competency_id}/prerequisites",
    response_model=dict[str, Any],
    status_code=201,
)
def add_competency_prerequisite_route(
    request: Request,
    competency_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Add a prerequisite relationship. Owner only."""
    ok = add_competency_prerequisite(
        _database_path(request),
        competency_id=competency_id,
        prerequisite_competency_id=payload.get("prerequisiteCompetencyId", ""),
        user_id=current_user.user_id,
    )
    if not ok:
        raise ApiError(
            400,
            "INVALID_REQUEST",
            "Cannot add prerequisite (self-reference, not found, not owned, or already linked).",
        )
    return {"status": "linked"}


@router.get(
    "/competencies/{competency_id}/prerequisites",
    response_model=dict[str, Any],
)
def list_competency_prerequisites_route(
    request: Request,
    competency_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List prerequisite competencies. Owner only."""
    prereqs = list_competency_prerequisites(
        _database_path(request),
        competency_id=competency_id,
        user_id=current_user.user_id,
    )
    if prereqs is None:
        raise ApiError(404, "COMPETENCY_NOT_FOUND", "Competency not found or access denied.")
    return {"prerequisites": prereqs}


@router.post(
    "/objectives/{objective_id}/competencies",
    response_model=dict[str, Any],
    status_code=201,
)
def link_objective_competency_route(
    request: Request,
    objective_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Link a learning objective to a competency. Owner only."""
    ok = link_objective_competency(
        _database_path(request),
        objective_id=objective_id,
        competency_id=payload.get("competencyId", ""),
        user_id=current_user.user_id,
    )
    if not ok:
        raise ApiError(
            400,
            "INVALID_REQUEST",
            "Cannot link objective to competency (not found, not owned, or already linked).",
        )
    return {"status": "linked"}


@router.post(
    "/questions/{question_id}/competencies",
    response_model=dict[str, Any],
    status_code=201,
)
def link_question_competency_route(
    request: Request,
    question_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Link an assessment question to a competency. Owner only."""
    ok = link_question_competency(
        _database_path(request),
        question_id=question_id,
        competency_id=payload.get("competencyId", ""),
        user_id=current_user.user_id,
    )
    if not ok:
        raise ApiError(
            400,
            "INVALID_REQUEST",
            "Cannot link question to competency (not found, not owned, or already linked).",
        )
    return {"status": "linked"}


@router.get(
    "/competencies/{competency_id}/mastery",
    response_model=dict[str, Any],
)
def get_competency_mastery_route(
    request: Request,
    competency_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get mastery level for a competency. Owner only."""
    mastery = get_competency_mastery(
        _database_path(request),
        competency_id=competency_id,
        user_id=current_user.user_id,
    )
    if mastery is None:
        raise ApiError(404, "MASTERY_NOT_FOUND", "Mastery record not found.")
    return mastery


@router.patch(
    "/competencies/{competency_id}/mastery",
    response_model=dict[str, Any],
)
def update_competency_mastery_route(
    request: Request,
    competency_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update mastery level for a competency. Owner only."""
    mastery = update_competency_mastery(
        _database_path(request),
        competency_id=competency_id,
        user_id=current_user.user_id,
        mastery_score=payload.get("masteryScore", 0.0),
    )
    if mastery is None:
        raise ApiError(404, "MASTERY_NOT_FOUND", "Competency not found or access denied.")
    return mastery
