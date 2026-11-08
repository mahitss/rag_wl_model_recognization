"""M66: AI Examination Simulator & Exam Preparation Engine API routes.

Endpoints:

* POST   /api/v1/exams                                      - Create exam
* GET    /api/v1/exams                                      - List exams
* GET    /api/v1/exams/{exam_id}                            - Get one exam
* PATCH  /api/v1/exams/{exam_id}                            - Update exam
* DELETE /api/v1/exams/{exam_id}                            - Delete exam
* POST   /api/v1/exams/{exam_id}/sections                   - Create section
* GET    /api/v1/exams/{exam_id}/sections                   - List sections
* POST   /api/v1/exams/{exam_id}/questions                  - Create question
* GET    /api/v1/exams/{exam_id}/questions                  - List questions
* POST   /api/v1/exams/{exam_id}/attempts                   - Start attempt
* GET    /api/v1/exams/{exam_id}/attempts                   - List attempts
* GET    /api/v1/exam-attempts/{attempt_id}                 - Get one attempt
* POST   /api/v1/exam-attempts/{attempt_id}/answers         - Record answer
* GET    /api/v1/exam-attempts/{attempt_id}/answers         - List answers
* POST   /api/v1/exam-results                               - Record result
* GET    /api/v1/exam-results/{result_id}                   - Get one result
* GET    /api/v1/exam-results                               - List results
* GET    /api/v1/exams/{exam_id}/readiness                  - Exam readiness

All endpoints require authentication and ownership (where applicable).
"""
# ruff: noqa: B008,E501

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request

from app.auth import AuthUser, require_auth
from app.errors import ApiError
from app.storage import (
    create_exam,
    create_exam_question,
    create_exam_section,
    delete_exam,
    get_exam,
    get_exam_attempt,
    get_exam_result,
    list_exam_answers,
    list_exam_attempts,
    list_exam_questions,
    list_exam_results,
    list_exam_sections,
    list_exams,
    record_exam_answer,
    record_exam_attempt,
    record_exam_result,
    update_exam,
)

router = APIRouter(prefix="/api/v1", tags=["exams", "exam-preparation"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Exam CRUD
# ---------------------------------------------------------------------------


@router.post("/exams", response_model=dict[str, Any], status_code=201)
def create_exam_route(
    request: Request,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new examination."""
    exam = create_exam(
        _database_path(request),
        user_id=current_user.user_id,
        course_id=payload.get("courseId"),
        title=payload.get("title", ""),
        subject=payload.get("subject", ""),
        topic=payload.get("topic", ""),
        description=payload.get("description"),
        duration_minutes=payload.get("durationMinutes", 0),
        total_marks=payload.get("totalMarks", 0),
        question_count=payload.get("questionCount", 0),
        passing_score=payload.get("passingScore", 0.6),
        negative_marking=payload.get("negativeMarking", 0.0),
        instructions=payload.get("instructions"),
        difficulty_distribution=payload.get("difficultyDistribution"),
        status=payload.get("status", "draft"),
    )
    if exam is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid exam input.")
    return exam


@router.get("/exams", response_model=dict[str, Any])
def list_exams_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = Query(None),
    course_id: str | None = Query(None),
) -> dict[str, Any]:
    """List exams owned by the user."""
    exams = list_exams(
        _database_path(request),
        user_id=current_user.user_id,
        status=status,
        course_id=course_id,
    )
    return {"exams": exams}


@router.get("/exams/{exam_id}", response_model=dict[str, Any])
def get_exam_route(
    request: Request,
    exam_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one exam if owned by the user."""
    exam = get_exam(
        _database_path(request), exam_id=exam_id, user_id=current_user.user_id
    )
    if exam is None:
        raise ApiError(404, "EXAM_NOT_FOUND", "Exam not found or access denied.")
    return exam


@router.patch("/exams/{exam_id}", response_model=dict[str, Any])
def update_exam_route(
    request: Request,
    exam_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update an exam. Owner only."""
    updated = update_exam(
        _database_path(request),
        exam_id=exam_id,
        user_id=current_user.user_id,
        title=payload.get("title"),
        subject=payload.get("subject"),
        topic=payload.get("topic"),
        description=payload.get("description"),
        duration_minutes=payload.get("durationMinutes"),
        total_marks=payload.get("totalMarks"),
        question_count=payload.get("questionCount"),
        passing_score=payload.get("passingScore"),
        negative_marking=payload.get("negativeMarking"),
        instructions=payload.get("instructions"),
        difficulty_distribution=payload.get("difficultyDistribution"),
        status=payload.get("status"),
    )
    if updated is None:
        raise ApiError(404, "EXAM_NOT_FOUND", "Exam not found or access denied.")
    return updated


@router.delete("/exams/{exam_id}", response_model=dict[str, Any])
def delete_exam_route(
    request: Request,
    exam_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete an exam. Owner only."""
    deleted = delete_exam(
        _database_path(request),
        exam_id=exam_id,
        user_id=current_user.user_id,
    )
    if not deleted:
        raise ApiError(404, "EXAM_NOT_FOUND", "Exam not found or access denied.")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Exam sections
# ---------------------------------------------------------------------------


@router.post(
    "/exams/{exam_id}/sections",
    response_model=dict[str, Any],
    status_code=201,
)
def create_exam_section_route(
    request: Request,
    exam_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a section within an exam. Owner only."""
    section = create_exam_section(
        _database_path(request),
        exam_id=exam_id,
        user_id=current_user.user_id,
        title=payload.get("title", ""),
        description=payload.get("description"),
        question_count=payload.get("questionCount", 0),
        marks=payload.get("marks", 0),
        position=payload.get("position", 0),
    )
    if section is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid section input.")
    return section


@router.get("/exams/{exam_id}/sections", response_model=dict[str, Any])
def list_exam_sections_route(
    request: Request,
    exam_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List sections for an exam. Owner only."""
    sections = list_exam_sections(
        _database_path(request), exam_id=exam_id, user_id=current_user.user_id
    )
    if sections is None:
        raise ApiError(404, "EXAM_NOT_FOUND", "Exam not found or access denied.")
    return {"sections": sections}


# ---------------------------------------------------------------------------
# Exam questions
# ---------------------------------------------------------------------------


@router.post(
    "/exams/{exam_id}/questions",
    response_model=dict[str, Any],
    status_code=201,
)
def create_exam_question_route(
    request: Request,
    exam_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a question within an exam. Owner only."""
    question = create_exam_question(
        _database_path(request),
        exam_id=exam_id,
        user_id=current_user.user_id,
        section_id=payload.get("sectionId"),
        source_question_id=payload.get("sourceQuestionId"),
        question_type=payload.get("questionType", ""),
        prompt=payload.get("prompt", ""),
        answer_key=payload.get("answerKey"),
        accepted_answers=payload.get("acceptedAnswers"),
        explanation=payload.get("explanation"),
        marks=payload.get("marks", 1),
        difficulty_level=payload.get("difficultyLevel"),
        position=payload.get("position", 0),
        citations=payload.get("citations"),
    )
    if question is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid question input.")
    return question


@router.get("/exams/{exam_id}/questions", response_model=dict[str, Any])
def list_exam_questions_route(
    request: Request,
    exam_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List questions for an exam. Owner only."""
    questions = list_exam_questions(
        _database_path(request), exam_id=exam_id, user_id=current_user.user_id
    )
    if questions is None:
        raise ApiError(404, "EXAM_NOT_FOUND", "Exam not found or access denied.")
    return {"questions": questions}


# ---------------------------------------------------------------------------
# Exam attempts
# ---------------------------------------------------------------------------


@router.post(
    "/exams/{exam_id}/attempts",
    response_model=dict[str, Any],
    status_code=201,
)
def record_exam_attempt_route(
    request: Request,
    exam_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Start an exam attempt. Owner only."""
    attempt = record_exam_attempt(
        _database_path(request),
        exam_id=exam_id,
        user_id=current_user.user_id,
        status=payload.get("status", "in_progress"),
    )
    if attempt is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid attempt input.")
    return attempt


@router.get("/exams/{exam_id}/attempts", response_model=dict[str, Any])
def list_exam_attempts_route(
    request: Request,
    exam_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List attempts for an exam. Owner only."""
    attempts = list_exam_attempts(
        _database_path(request),
        exam_id=exam_id,
        user_id=current_user.user_id,
    )
    return {"attempts": attempts}


@router.get("/exam-attempts/{attempt_id}", response_model=dict[str, Any])
def get_exam_attempt_route(
    request: Request,
    attempt_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one exam attempt if owned by the user."""
    attempt = get_exam_attempt(
        _database_path(request), attempt_id=attempt_id, user_id=current_user.user_id
    )
    if attempt is None:
        raise ApiError(404, "EXAM_ATTEMPT_NOT_FOUND", "Attempt not found or access denied.")
    return attempt


@router.post(
    "/exam-attempts/{attempt_id}/answers",
    response_model=dict[str, Any],
    status_code=201,
)
def record_exam_answer_route(
    request: Request,
    attempt_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record an answer to an exam question. Owner only."""
    answer = record_exam_answer(
        _database_path(request),
        attempt_id=attempt_id,
        exam_question_id=payload.get("examQuestionId", ""),
        user_id=current_user.user_id,
        submitted_answer=payload.get("submittedAnswer"),
        marks_obtained=payload.get("marksObtained", 0.0),
        outcome=payload.get("outcome", "unanswered"),
        is_marked_for_review=payload.get("isMarkedForReview", False),
        time_spent_ms=payload.get("timeSpentMs"),
    )
    if answer is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid answer input, attempt or question not found.",
        )
    return answer


@router.get(
    "/exam-attempts/{attempt_id}/answers",
    response_model=dict[str, Any],
)
def list_exam_answers_route(
    request: Request,
    attempt_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List answers for an exam attempt. Owner only."""
    answers = list_exam_answers(
        _database_path(request), attempt_id=attempt_id, user_id=current_user.user_id
    )
    if answers is None:
        raise ApiError(404, "EXAM_ATTEMPT_NOT_FOUND", "Attempt not found or access denied.")
    return {"answers": answers}


# ---------------------------------------------------------------------------
# Exam results
# ---------------------------------------------------------------------------


@router.post("/exam-results", response_model=dict[str, Any], status_code=201)
def record_exam_result_route(
    request: Request,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record final exam result. Owner only."""
    result = record_exam_result(
        _database_path(request),
        attempt_id=payload.get("attemptId", ""),
        exam_id=payload.get("examId", ""),
        user_id=current_user.user_id,
        total_marks=payload.get("totalMarks", 0),
        marks_obtained=payload.get("marksObtained", 0.0),
        percentage=payload.get("percentage", 0.0),
        passed=payload.get("passed", False),
        correct_count=payload.get("correctCount", 0),
        incorrect_count=payload.get("incorrectCount", 0),
        unanswered_count=payload.get("unansweredCount", 0),
        partial_count=payload.get("partialCount", 0),
        time_spent_ms=payload.get("timeSpentMs"),
        topic_wise=payload.get("topicWise"),
        competency_wise=payload.get("competencyWise"),
        difficulty_wise=payload.get("difficultyWise"),
    )
    if result is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid result input.")
    return result


@router.get("/exam-results/{result_id}", response_model=dict[str, Any])
def get_exam_result_route(
    request: Request,
    result_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one exam result if owned by the user."""
    result = get_exam_result(
        _database_path(request), result_id=result_id, user_id=current_user.user_id
    )
    if result is None:
        raise ApiError(404, "EXAM_RESULT_NOT_FOUND", "Result not found or access denied.")
    return result


@router.get("/exam-results", response_model=dict[str, Any])
def list_exam_results_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    exam_id: str | None = Query(None),
) -> dict[str, Any]:
    """List exam results for the user."""
    results = list_exam_results(
        _database_path(request), user_id=current_user.user_id, exam_id=exam_id
    )
    return {"results": results}


# ---------------------------------------------------------------------------
# Exam readiness
# ---------------------------------------------------------------------------


@router.get("/exams/{exam_id}/readiness", response_model=dict[str, Any])
def get_exam_readiness_route(
    request: Request,
    exam_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return exam readiness based on actual evidence (M64/M65 integration)."""
    from app.gap_detection import list_user_gaps
    from app.storage import get_competency_mastery, list_competencies

    exam = get_exam(
        _database_path(request), exam_id=exam_id, user_id=current_user.user_id
    )
    if exam is None:
        raise ApiError(404, "EXAM_NOT_FOUND", "Exam not found or access denied.")

    user_id = current_user.user_id
    topic = exam["topic"]

    # Get gaps for this topic
    gaps_result = list_user_gaps(
        database_path=str(_database_path(request)), user_id=user_id
    )
    topic_gaps = [
        g for g in gaps_result["gaps"]
        if g.get("topic") == topic or g.get("topicId") == topic
    ]

    # Get competency mastery for linked competencies
    competencies = list_competencies(
        str(_database_path(request)), user_id=user_id
    )
    readiness_factors = []
    weak_topics = []
    total_mastery = 0.0
    mastery_count = 0

    for comp in competencies:
        mastery = get_competency_mastery(
            str(_database_path(request)),
            competency_id=comp["competencyId"],
            user_id=user_id,
        )
        if mastery is not None:
            total_mastery += mastery["masteryScore"]
            mastery_count += 1
            if mastery["masteryScore"] < 0.60:
                weak_topics.append(comp["title"])

    avg_mastery = (total_mastery / mastery_count) if mastery_count > 0 else None

    # Compute readiness score (0-1)
    if avg_mastery is not None:
        readiness_score = avg_mastery
    else:
        readiness_score = 0.0

    # Penalize for critical gaps
    critical_gaps = sum(1 for g in topic_gaps if g["severity"] == "critical")
    if critical_gaps > 0:
        readiness_score = max(0.0, readiness_score - 0.1 * critical_gaps)

    readiness_score = round(readiness_score, 2)

    if readiness_score >= 0.80:
        readiness_level = "ready"
    elif readiness_score >= 0.60:
        readiness_level = "moderately_ready"
    elif readiness_score >= 0.40:
        readiness_level = "partially_ready"
    else:
        readiness_level = "not_ready"

    recommended_actions = []
    if critical_gaps > 0:
        recommended_actions.append("Address critical knowledge gaps before attempting the exam.")
    if weak_topics:
        recommended_actions.append(f"Review weak topics: {', '.join(weak_topics[:5])}.")
    if avg_mastery is None or avg_mastery < 0.60:
        recommended_actions.append("Complete more practice assessments to build evidence.")
    if not recommended_actions:
        recommended_actions.append("You appear ready. Take a practice exam to confirm.")

    return {
        "examId": exam_id,
        "userId": user_id,
        "readinessScore": readiness_score,
        "readinessLevel": readiness_level,
        "evidenceBased": mastery_count > 0,
        "weakTopics": weak_topics,
        "recommendedActions": recommended_actions,
        "calculatedAt": _now().isoformat(),
    }
