# ruff: noqa: B008
"""M86: Diagnostic assessment API routes.

Endpoints:
- POST /api/v1/diagnostic/sessions — start a diagnostic session
- GET /api/v1/diagnostic/sessions — list sessions
- GET /api/v1/diagnostic/sessions/{session_id} — get session state
- POST /api/v1/diagnostic/sessions/{session_id}/next-question — next adaptive question
- POST /api/v1/diagnostic/sessions/{session_id}/answers — submit an answer
- GET /api/v1/diagnostic/sessions/{session_id}/results — session results
- POST /api/v1/diagnostic/sessions/{session_id}/complete — complete session
- GET /api/v1/diagnostic/sessions/{session_id}/mastery — mastery mapping
- GET /api/v1/diagnostic/sessions/{session_id}/recommendations — follow-up recommendations
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.auth import AuthUser, require_auth
from app.config import Settings
from app.diagnostic_assessment import (
    classify_misconceptions,
    complete_diagnostic_session,
    evaluate_stopping,
    get_session_state,
    integrate_with_mastery,
    integrate_with_recommendations,
    select_concept_for_next_question,
    select_question_for_concept,
    start_diagnostic_session,
)
from app.errors import ApiError
from app.quiz import evaluate_answer
from app.schemas import (
    DiagnosticAnswerSubmitRequest,
    DiagnosticAnswerSubmitResponse,
    DiagnosticMasteryMappingResponse,
    DiagnosticNextQuestionRequest,
    DiagnosticNextQuestionResponse,
    DiagnosticRecommendationResponse,
    DiagnosticSessionCreateRequest,
    DiagnosticSessionListItem,
    DiagnosticSessionListResponse,
    DiagnosticSessionResponse,
    DiagnosticSessionResultResponse,
    QuestionVersionResponse,
)
from app.storage import (
    get_assessment_question,
    get_current_question_version,
    get_diagnostic_concept_results,
    get_diagnostic_misconceptions,
    get_diagnostic_session,
    get_knowledge_concept,
    list_diagnostic_sessions,
    list_knowledge_concepts,
    record_assessment_question_answer,
    record_diagnostic_concept_result,
    record_diagnostic_misconception,
    update_diagnostic_session,
)

router = APIRouter(prefix="/api/v1")


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _database_path(request: Request):
    return _settings(request).database_path


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


@router.post(
    "/diagnostic/sessions",
    response_model=DiagnosticSessionResponse,
    tags=["diagnostic"],
    status_code=201,
)
def start_diagnostic_session_route(
    request: Request,
    payload: DiagnosticSessionCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Start a new adaptive diagnostic assessment session for a topic."""
    database_path = _database_path(request)
    session = start_diagnostic_session(
        database_path,
        user_id=current_user.user_id,
        topic_id=payload.topicId,
        topic=payload.topic,
        assessment_id=payload.assessmentId,
        difficulty_level=payload.difficultyLevel,
        max_questions=payload.maxQuestions,
        min_evidence_questions=payload.minEvidenceQuestions,
        confidence_threshold=payload.confidenceThreshold,
    )
    return session


@router.get(
    "/diagnostic/sessions",
    response_model=DiagnosticSessionListResponse,
    tags=["diagnostic"],
)
def list_diagnostic_sessions_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    topic_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List diagnostic sessions for the authenticated user."""
    database_path = _database_path(request)
    sessions, total = list_diagnostic_sessions(
        database_path,
        user_id=current_user.user_id,
        topic_id=topic_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    items = [
        DiagnosticSessionListItem(
            sessionId=s["session_id"],
            topicId=s["topic_id"],
            topic=s["topic"],
            status=s["status"],
            startedAt=s["started_at"],
            completedAt=s["completed_at"],
            stoppingReason=s["stopping_reason"],
            questionsAsked=len(s["questions_asked"]),
        )
        for s in sessions
    ]
    return {
        "sessions": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/diagnostic/sessions/{session_id}",
    response_model=dict[str, Any],
    tags=["diagnostic"],
)
def get_diagnostic_session_route(
    request: Request,
    session_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the full diagnostic state for a session."""
    database_path = _database_path(request)
    state = get_session_state(
        database_path, session_id=session_id, user_id=current_user.user_id
    )
    if state is None:
        raise ApiError(404, "DIAGNOSTIC_SESSION_NOT_FOUND", "Diagnostic session was not found.")
    return state


@router.post(
    "/diagnostic/sessions/{session_id}/complete",
    response_model=dict[str, Any],
    tags=["diagnostic"],
)
def complete_diagnostic_session_route(
    request: Request,
    session_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Mark a diagnostic session as completed."""
    database_path = _database_path(request)
    result = complete_diagnostic_session(
        database_path, session_id=session_id, user_id=current_user.user_id
    )
    if result is None:
        raise ApiError(
            404,
            "DIAGNOSTIC_SESSION_NOT_FOUND",
            "Diagnostic session was not found or not in progress.",
        )
    return result


# ---------------------------------------------------------------------------
# Adaptive question flow
# ---------------------------------------------------------------------------


@router.post(
    "/diagnostic/sessions/{session_id}/next-question",
    response_model=DiagnosticNextQuestionResponse,
    tags=["diagnostic"],
)
def next_diagnostic_question_route(
    request: Request,
    session_id: str = Path(min_length=1, max_length=80),
    payload: DiagnosticNextQuestionRequest | None = Body(default=None),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Select the next question for a diagnostic session using adaptive rules.

    Picks the next concept to assess, then selects an unasked question at an
    appropriate difficulty. Returns stopping information when limits are reached.
    """
    database_path = _database_path(request)
    session = get_diagnostic_session(
        database_path, session_id=session_id, user_id=current_user.user_id
    )
    if session is None:
        raise ApiError(404, "DIAGNOSTIC_SESSION_NOT_FOUND", "Diagnostic session was not found.")
    if session["status"] != "in_progress":
        raise ApiError(
            409,
            "DIAGNOSTIC_SESSION_NOT_IN_PROGRESS",
            f"Session status is '{session['status']}'.",
        )

    concepts = list_knowledge_concepts(database_path, user_id=current_user.user_id) or []
    # Filter concepts to the session topic when a knowledge-graph concept matches.
    topic_concepts = [
        c
        for c in concepts
        if (c.get("canonical_name") or c.get("name") or "").casefold()
        == session["topic"].casefold()
    ]
    if not topic_concepts:
        # Fall back to all concepts so the diagnostic can still run.
        topic_concepts = concepts

    concept_results = get_diagnostic_concept_results(
        database_path, session_id=session_id, user_id=current_user.user_id
    )
    asked_ids = set(session["questions_asked"])

    stopping = evaluate_stopping(
        session=session,
        concept_results=concept_results,
        concepts_total=len(topic_concepts),
        questions_asked=len(asked_ids),
        max_questions=session["max_questions"],
        min_evidence_questions=session["min_evidence_questions"],
        confidence_threshold=session["confidence_threshold"],
    )
    if stopping.should_stop:
        return DiagnosticNextQuestionResponse(
            sessionId=session_id,
            status=session["status"],
            question=None,
            stoppingReason=stopping.reason,
            evidenceSufficient=stopping.evidence_sufficient,
            conceptsRemaining=stopping.concepts_remaining,
        )

    concept = select_concept_for_next_question(
        database_path=database_path,
        user_id=current_user.user_id,
        session=session,
        concepts=topic_concepts,
        concept_results=concept_results,
        asked_question_ids=asked_ids,
    )
    if concept is None:
        return DiagnosticNextQuestionResponse(
            sessionId=session_id,
            status=session["status"],
            question=None,
            stoppingReason="no_concepts_available",
            evidenceSufficient=stopping.evidence_sufficient,
            conceptsRemaining=stopping.concepts_remaining,
        )

    question = select_question_for_concept(
        database_path=database_path,
        user_id=current_user.user_id,
        concept=concept,
        asked_question_ids=asked_ids,
        current_difficulty=session.get("difficulty_level"),
    )
    if question is None:
        # No unasked question for this concept; mark concept as assessed with
        # insufficient evidence and move on.
        update_diagnostic_session(
            database_path,
            session_id=session_id,
            user_id=current_user.user_id,
            concepts_assessed=[*session["concepts_assessed"], concept["conceptId"]],
        )
        return DiagnosticNextQuestionResponse(
            sessionId=session_id,
            status=session["status"],
            question=None,
            stoppingReason="no_questions_for_concept",
            evidenceSufficient=stopping.evidence_sufficient,
            conceptsRemaining=stopping.concepts_remaining,
        )

    # Update session state: current concept + questions asked.
    update_diagnostic_session(
        database_path,
        session_id=session_id,
        user_id=current_user.user_id,
        current_concept_id=concept["conceptId"],
        questions_asked=[*asked_ids, question.question_id],
        concepts_assessed=[*session["concepts_assessed"], concept["conceptId"]],
    )
    return DiagnosticNextQuestionResponse(
        sessionId=session_id,
        status=session["status"],
        question=question,
        stoppingReason=None,
        evidenceSufficient=stopping.evidence_sufficient,
        conceptsRemaining=stopping.concepts_remaining,
    )


@router.post(
    "/diagnostic/sessions/{session_id}/answers",
    response_model=DiagnosticAnswerSubmitResponse,
    tags=["diagnostic"],
)
def submit_diagnostic_answer_route(
    request: Request,
    session_id: str = Path(min_length=1, max_length=80),
    payload: DiagnosticAnswerSubmitRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Submit an answer to a diagnostic question and score it server-side.

    The answer key is never sent to the client; scoring happens here and only
    the outcome is returned.
    """
    database_path = _database_path(request)
    session = get_diagnostic_session(
        database_path, session_id=session_id, user_id=current_user.user_id
    )
    if session is None:
        raise ApiError(404, "DIAGNOSTIC_SESSION_NOT_FOUND", "Diagnostic session was not found.")
    if session["status"] != "in_progress":
        raise ApiError(
            409,
            "DIAGNOSTIC_SESSION_NOT_IN_PROGRESS",
            f"Session status is '{session['status']}'.",
        )
    if session_id != payload.sessionId:
        raise ApiError(400, "SESSION_ID_MISMATCH", "Session ID in body does not match path.")

    question = get_assessment_question(
        database_path, question_id=payload.questionId, user_id=current_user.user_id
    )
    if question is None:
        raise ApiError(
            404, "DIAGNOSTIC_QUESTION_NOT_FOUND", "Diagnostic question was not found."
        )
    if payload.questionId not in session["questions_asked"]:
        raise ApiError(
            400,
            "QUESTION_NOT_ASKED",
            "This question was not presented in the current session.",
        )

    # Score server-side using the answer key (never exposed to client).
    question_type = question["questionType"]
    answer_key = question["answerKey"]
    accepted_answers = question["acceptedAnswers"]
    if question_type == "multiple_choice":
        option_ids = {o["id"] for o in (question.get("options") or [])}
        if payload.answer not in option_ids:
            raise ApiError(422, "INVALID_ANSWER", "Choose one of the available options.")
        outcome = "correct" if payload.answer == answer_key else "incorrect"
        score = 1.0 if outcome == "correct" else 0.0
    else:
        outcome = evaluate_answer(
            {
                "questionType": question_type,
                "answerKey": answer_key,
                "acceptedAnswers": accepted_answers,
            },
            payload.answer,
        )
        score = (
            1.0
            if outcome == "correct"
            else 0.5
            if outcome == "partial"
            else 0.0
            if outcome == "incorrect"
            else None
        )

    attempt_id = f"diagatt_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    try:
        record_assessment_question_answer(
            database_path,
            attempt_id=attempt_id,
            question_id=payload.questionId,
            user_id=current_user.user_id,
            question_type=question_type,
            submitted_answer=payload.answer,
            answer_key=answer_key,
            accepted_answers=accepted_answers,
            result=outcome,
            score=score,
            citations=question.get("citations"),
        )
    except ValueError as exc:
        raise ApiError(422, "INVALID_ANSWER", str(exc)) from exc

    # Recompute competency for this concept from session evidence so far.
    concept_id = question.get("concept_id")  # may be None
    concept_id = concept_id or session.get("current_concept_id")
    concept_name = None
    if concept_id:
        concept = get_knowledge_concept(
            database_path, concept_id=concept_id, user_id=current_user.user_id
        )
        if concept:
            concept_name = concept.get("canonical_name") or concept.get("name")

    # Gather all session question outcomes for the current concept.

    # Build per-concept result using the question outcomes recorded in this
    # session. We recompute from the current attempt plus prior attempts for
    # the same question.
    current_concept_id = session.get("current_concept_id") or concept_id
    competency_result: dict[str, Any] | None = None
    misconceptions: list[dict[str, Any]] = []
    if current_concept_id:
        # Collect outcomes for questions tied to this concept in this session.
        # We only have the current question's outcome here; prior concept
        # results are merged from stored diagnostic_concept_results.
        prior = get_diagnostic_concept_results(
            database_path, session_id=session_id, user_id=current_user.user_id
        )
        prior_for_concept = [
            r for r in prior if r["concept_id"] == current_concept_id
        ]
        total_questions = (prior_for_concept[0]["total_questions"] if prior_for_concept else 0) + 1
        correct_count = (prior_for_concept[0]["correct_count"] if prior_for_concept else 0) + (
            1 if outcome == "correct" else 0
        )
        incorrect_count = (prior_for_concept[0]["incorrect_count"] if prior_for_concept else 0) + (
            1 if outcome == "incorrect" else 0
        )
        ambiguous_count = (prior_for_concept[0]["ambiguous_count"] if prior_for_concept else 0) + (
            1 if outcome == "ambiguous" else 0
        )
        scored = correct_count + incorrect_count
        competency_score = (correct_count / scored) if scored else None
        evidence_count = scored
        if evidence_count >= 10:
            confidence = 0.90
        elif evidence_count >= 5:
            confidence = 0.75
        elif evidence_count >= 3:
            confidence = 0.60
        else:
            confidence = 0.40

        concept_record = record_diagnostic_concept_result(
            database_path,
            session_id=session_id,
            user_id=current_user.user_id,
            concept_id=current_concept_id,
            concept_name=concept_name or current_concept_id,
            competency_score=competency_score,
            confidence=confidence,
            evidence_question_ids=[
                *(prior_for_concept[0]["evidence_question_ids"] if prior_for_concept else []),
                payload.questionId,
            ],
            correct_count=correct_count,
            incorrect_count=incorrect_count,
            ambiguous_count=ambiguous_count,
            total_questions=total_questions,
        )

        # Classify misconceptions from outcomes so far.
        question_outcomes = [
            {"question_id": payload.questionId, "outcome": outcome},
        ]
        misconceptions = classify_misconceptions(
            database_path=database_path,
            user_id=current_user.user_id,
            concept_id=current_concept_id,
            concept_name=concept_name or current_concept_id,
            competency_result=concept_record,
            question_outcomes=question_outcomes,
        )
        for m in misconceptions:
            record_diagnostic_misconception(
                database_path,
                session_id=session_id,
                user_id=current_user.user_id,
                concept_id=m["concept_id"],
                concept_name=m["concept_name"],
                misconception_type=m["misconception_type"],
                description=m["description"],
                evidence_question_ids=m["evidence_question_ids"],
                severity=m["severity"],
            )

        competency_result = concept_record

    # Evaluate stopping after this answer.
    concept_results = get_diagnostic_concept_results(
        database_path, session_id=session_id, user_id=current_user.user_id
    )
    concepts = list_knowledge_concepts(database_path, user_id=current_user.user_id) or []
    topic_concepts = [
        c
        for c in concepts
        if (c.get("canonical_name") or c.get("name") or "").casefold()
        == session["topic"].casefold()
    ]
    if not topic_concepts:
        topic_concepts = concepts
    stopping = evaluate_stopping(
        session=session,
        concept_results=concept_results,
        concepts_total=len(topic_concepts),
        questions_asked=len(session["questions_asked"]),
        max_questions=session["max_questions"],
        min_evidence_questions=session["min_evidence_questions"],
        confidence_threshold=session["confidence_threshold"],
    )
    if stopping.should_stop and stopping.reason != "sufficient_evidence_for_assessed":
        update_diagnostic_session(
            database_path,
            session_id=session_id,
            user_id=current_user.user_id,
            status="completed",
            completed_at=now,
            stopping_reason=stopping.reason,
        )

    return DiagnosticAnswerSubmitResponse(
        attemptId=attempt_id,
        questionId=payload.questionId,
        outcome=outcome,
        isCorrect=True if outcome == "correct" else False if outcome == "incorrect" else None,
        competencyScore=(competency_result["competency_score"] if competency_result else None),
        confidence=(competency_result["confidence"] if competency_result else 0.0),
        evidenceSufficient=stopping.evidence_sufficient,
        conceptsRemaining=stopping.concepts_remaining,
        stoppingReason=stopping.reason,
    )


# ---------------------------------------------------------------------------
# Results & integration
# ---------------------------------------------------------------------------


@router.get(
    "/diagnostic/sessions/{session_id}/results",
    response_model=DiagnosticSessionResultResponse,
    tags=["diagnostic"],
)
def get_diagnostic_results_route(
    request: Request,
    session_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return evidence-backed diagnostic results for a session."""
    database_path = _database_path(request)
    session = get_diagnostic_session(
        database_path, session_id=session_id, user_id=current_user.user_id
    )
    if session is None:
        raise ApiError(404, "DIAGNOSTIC_SESSION_NOT_FOUND", "Diagnostic session was not found.")
    concept_results = get_diagnostic_concept_results(
        database_path, session_id=session_id, user_id=current_user.user_id
    )
    misconceptions = get_diagnostic_misconceptions(
        database_path, session_id=session_id, user_id=current_user.user_id
    )

    scored = [
        r for r in concept_results if r["competency_score"] is not None
    ]
    overall_competency = (
        sum(r["competency_score"] for r in scored) / len(scored) if scored else None
    )
    confidences = [r["confidence"] for r in concept_results]
    overall_confidence = (
        sum(confidences) / len(confidences) if confidences else 0.0
    )

    return {
        "session": session,
        "conceptResults": concept_results,
        "misconceptions": misconceptions,
        "overallCompetency": overall_competency,
        "overallConfidence": overall_confidence,
        "stoppingReason": session["stopping_reason"],
        "evidenceSufficient": len(concept_results) >= session["min_evidence_questions"],
    }


@router.get(
    "/diagnostic/sessions/{session_id}/mastery",
    response_model=DiagnosticMasteryMappingResponse,
    tags=["diagnostic"],
)
def get_diagnostic_mastery_route(
    request: Request,
    session_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Map diagnostic results to existing competency mastery (proposed, non-destructive)."""
    database_path = _database_path(request)
    session = get_diagnostic_session(
        database_path, session_id=session_id, user_id=current_user.user_id
    )
    if session is None:
        raise ApiError(404, "DIAGNOSTIC_SESSION_NOT_FOUND", "Diagnostic session was not found.")
    mapping = integrate_with_mastery(
        database_path, user_id=current_user.user_id, session_id=session_id
    )
    return mapping


@router.get(
    "/diagnostic/sessions/{session_id}/recommendations",
    response_model=DiagnosticRecommendationResponse,
    tags=["diagnostic"],
)
def get_diagnostic_recommendations_route(
    request: Request,
    session_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return follow-up recommendations from diagnostic results."""
    database_path = _database_path(request)
    session = get_diagnostic_session(
        database_path, session_id=session_id, user_id=current_user.user_id
    )
    if session is None:
        raise ApiError(404, "DIAGNOSTIC_SESSION_NOT_FOUND", "Diagnostic session was not found.")
    settings = _settings(request)
    result = integrate_with_recommendations(
        database_path,
        user_id=current_user.user_id,
        session_id=session_id,
        settings=settings,
    )
    return result


# ---------------------------------------------------------------------------
# Question versioning (read-only protection of answer keys)
# ---------------------------------------------------------------------------


@router.get(
    "/diagnostic/questions/{question_id}/version",
    response_model=QuestionVersionResponse,
    tags=["diagnostic"],
)
def get_question_version_route(
    request: Request,
    question_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the current version record for a question (hashes only).

    Answer keys are never exposed; only content hashes are returned so
    clients can detect question changes without learning the key.
    """
    database_path = _database_path(request)
    # Verify the question is owned by the user.
    question = get_assessment_question(
        database_path, question_id=question_id, user_id=current_user.user_id
    )
    if question is None:
        raise ApiError(404, "DIAGNOSTIC_QUESTION_NOT_FOUND", "Diagnostic question was not found.")
    version = get_current_question_version(database_path, question_id=question_id)
    if version is None:
        raise ApiError(
            404, "QUESTION_VERSION_NOT_FOUND", "No version record for this question."
        )
    return version
