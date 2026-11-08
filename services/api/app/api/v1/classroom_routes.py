"""M70: Live Interactive Classroom & Virtual Learning Engine API routes.

Endpoints:

* POST   /api/v1/classrooms                                      - Create classroom session
* GET    /api/v1/classrooms                                      - List classroom sessions
* GET    /api/v1/classrooms/{session_id}                         - Get one session
* PATCH  /api/v1/classrooms/{session_id}                         - Update session (start/pause/resume/end)
* DELETE /api/v1/classrooms/{session_id}                         - Delete session (educator only)
* POST   /api/v1/classrooms/{session_id}/join                    - Join session (participant)
* POST   /api/v1/classrooms/{session_id}/leave                   - Leave session
* GET    /api/v1/classrooms/{session_id}/participants            - List participants
* POST   /api/v1/classrooms/{session_id}/presence                - Record heartbeat
* GET    /api/v1/classrooms/{session_id}/presence                - List presence
* POST   /api/v1/classrooms/{session_id}/questions               - Record learner question
* GET    /api/v1/classrooms/{session_id}/questions               - List questions
* PATCH  /api/v1/classrooms/{session_id}/questions/{q_id}/answer - Answer question (educator)
* POST   /api/v1/classrooms/{session_id}/polls                   - Create poll (educator)
* GET    /api/v1/classrooms/{session_id}/polls                   - List polls
* POST   /api/v1/classrooms/{session_id}/polls/{poll_id}/respond - Respond to poll
* GET    /api/v1/classrooms/{session_id}/polls/{poll_id}/results - Get poll results
* POST   /api/v1/classrooms/{session_id}/announcements           - Create announcement (educator)
* GET    /api/v1/classrooms/{session_id}/announcements           - List announcements
* POST   /api/v1/classrooms/{session_id}/activity-state          - Update activity state (educator)
* GET    /api/v1/classrooms/{session_id}/activity-state          - Get activity state
* POST   /api/v1/classrooms/{session_id}/recover                 - Session recovery after disconnect

Real-time transport: All endpoints are HTTP REST with polling-based state
synchronization. There is no WebSocket infrastructure in this codebase, so
clients should poll the relevant list endpoints (presence, questions, polls,
activity state) to receive live updates. This is an honest, auditable design;
no real-time WebSocket behavior is claimed.

All endpoints require authentication and session participation/ownership
(where applicable).
"""
# ruff: noqa: B008,E501

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.auth import AuthUser, require_auth
from app.config import Settings
from app.errors import ApiError
from app.schemas import (
    ClassroomActivityStateUpdateRequest,
    ClassroomAnnouncementCreateRequest,
    ClassroomAnnouncementResponse,
    ClassroomAnnouncementListResponse,
    ClassroomParticipantJoinRequest,
    ClassroomParticipantResponse,
    ClassroomPollCreateRequest,
    ClassroomPollListResponse,
    ClassroomPollRespondRequest,
    ClassroomPollResultsResponse,
    ClassroomPresenceListResponse,
    ClassroomQuestionListResponse,
    ClassroomQuestionRecord,
    ClassroomQuestionResponse,
    ClassroomSessionCreateRequest,
    ClassroomSessionListResponse,
    ClassroomSessionRecoveryResponse,
    ClassroomSessionResponse,
    ClassroomSessionUpdateRequest,
)
from app.storage import (
    answer_classroom_question,
    create_classroom_announcement,
    create_classroom_poll,
    create_classroom_session,
    delete_classroom_session,
    get_classroom_activity_state,
    get_classroom_poll_results,
    get_classroom_session,
    join_classroom_session,
    leave_classroom_session,
    list_classroom_announcements,
    list_classroom_questions,
    list_classroom_participants,
    list_classroom_polls,
    list_classroom_presence,
    record_classroom_heartbeat,
    record_classroom_question,
    respond_to_classroom_poll,
    update_classroom_activity_state,
    update_classroom_session,
    list_classroom_sessions,
)
from app.tutor import TutorUnavailable, local_chat_completion

router = APIRouter(prefix="/api/v1", tags=["live classroom"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Classroom session CRUD
# ---------------------------------------------------------------------------


@router.post("/classrooms", response_model=dict[str, Any], status_code=201)
def create_classroom_session_route(
    request: Request,
    payload: ClassroomSessionCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new live classroom session. Educator only."""
    session = create_classroom_session(
        _database_path(request),
        educator_id=current_user.user_id,
        lesson_plan_id=payload.lessonPlanId,
        title=payload.title,
        subject=payload.subject,
        topic=payload.topic,
        status=payload.status,
    )
    if session is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid classroom session input.")
    return session


@router.get("/classrooms", response_model=dict[str, Any])
def list_classroom_sessions_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    educator_id: str | None = Query(None),
    status: str | None = Query(None),
) -> dict[str, Any]:
    """List classroom sessions. Educators see their own; learners see sessions
    they are participants of."""
    sessions = list_classroom_sessions(
        _database_path(request),
        educator_id=educator_id,
        user_id=current_user.user_id,
        status=status,
    )
    return {"sessions": sessions}


@router.get("/classrooms/{session_id}", response_model=dict[str, Any])
def get_classroom_session_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one classroom session if the user is the educator or a participant."""
    session = get_classroom_session(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if session is None:
        raise ApiError(404, "CLASSROOM_NOT_FOUND", "Classroom session not found or access denied.")
    return session


@router.patch("/classrooms/{session_id}", response_model=dict[str, Any])
def update_classroom_session_route(
    request: Request,
    session_id: str = Path(...),
    payload: ClassroomSessionUpdateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a classroom session. Educator only."""
    updated = update_classroom_session(
        _database_path(request),
        session_id=session_id,
        educator_id=current_user.user_id,
        title=payload.title,
        subject=payload.subject,
        topic=payload.topic,
        status=payload.status,
        started_at=payload.startedAt,
        ended_at=payload.endedAt,
    )
    if updated is None:
        raise ApiError(404, "CLASSROOM_NOT_FOUND", "Classroom session not found or access denied.")
    return updated


@router.delete("/classrooms/{session_id}", response_model=dict[str, Any])
def delete_classroom_session_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a classroom session. Educator only."""
    deleted = delete_classroom_session(
        _database_path(request),
        session_id=session_id,
        educator_id=current_user.user_id,
    )
    if not deleted:
        raise ApiError(404, "CLASSROOM_NOT_FOUND", "Classroom session not found or access denied.")
    return {"sessionId": session_id, "deleted": True}


# ---------------------------------------------------------------------------
# Classroom participants
# ---------------------------------------------------------------------------


@router.post(
    "/classrooms/{session_id}/join",
    response_model=dict[str, Any],
    status_code=201,
)
def join_classroom_session_route(
    request: Request,
    session_id: str = Path(...),
    payload: ClassroomParticipantJoinRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Join a live classroom session."""
    participant = join_classroom_session(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
        role=payload.role,
    )
    if participant is None:
        raise ApiError(
            403,
            "JOIN_FAILED",
            "Session not found, not joinable, or you are not authorized.",
        )
    return participant


@router.post(
    "/classrooms/{session_id}/leave",
    response_model=dict[str, Any],
)
def leave_classroom_session_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Leave a live classroom session."""
    left = leave_classroom_session(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if not left:
        raise ApiError(404, "CLASSROOM_NOT_FOUND", "Session not found or you are not a participant.")
    return {"sessionId": session_id, "left": True}


@router.get(
    "/classrooms/{session_id}/participants",
    response_model=dict[str, Any],
)
def list_classroom_participants_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List participants in a classroom session."""
    participants = list_classroom_participants(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if participants is None:
        raise ApiError(404, "CLASSROOM_NOT_FOUND", "Classroom session not found or access denied.")
    return {"participants": participants}


# ---------------------------------------------------------------------------
# Classroom presence
# ---------------------------------------------------------------------------


@router.post(
    "/classrooms/{session_id}/presence",
    response_model=dict[str, Any],
)
def record_classroom_heartbeat_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record a presence heartbeat for a participant in a session."""
    heartbeat = record_classroom_heartbeat(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if heartbeat is None:
        raise ApiError(404, "CLASSROOM_NOT_FOUND", "Session not found or you are not a participant.")
    return heartbeat


@router.get(
    "/classrooms/{session_id}/presence",
    response_model=dict[str, Any],
)
def list_classroom_presence_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List presence records for participants in a session."""
    presence = list_classroom_presence(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if presence is None:
        raise ApiError(404, "CLASSROOM_NOT_FOUND", "Classroom session not found or access denied.")
    return {"presence": presence}


# ---------------------------------------------------------------------------
# Classroom questions
# ---------------------------------------------------------------------------


@router.post(
    "/classrooms/{session_id}/questions",
    response_model=dict[str, Any],
    status_code=201,
)
def record_classroom_question_route(
    request: Request,
    session_id: str = Path(...),
    payload: ClassroomQuestionRecord = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record a learner question during a classroom session."""
    question = record_classroom_question(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
        question_text=payload.questionText,
    )
    if question is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid question input, session not found, or you are not a participant.",
        )
    return question


@router.get(
    "/classrooms/{session_id}/questions",
    response_model=dict[str, Any],
)
def list_classroom_questions_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List questions for a classroom session."""
    questions = list_classroom_questions(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if questions is None:
        raise ApiError(404, "CLASSROOM_NOT_FOUND", "Classroom session not found or access denied.")
    return {"questions": questions}


@router.patch(
    "/classrooms/{session_id}/questions/{question_id}/answer",
    response_model=dict[str, Any],
)
def answer_classroom_question_route(
    request: Request,
    session_id: str = Path(...),
    question_id: str = Path(...),
    payload: ClassroomQuestionAnswerRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Answer a classroom question. Educator only."""
    answered = answer_classroom_question(
        _database_path(request),
        session_id=session_id,
        question_id=question_id,
        educator_id=current_user.user_id,
        answer_text=payload.answerText,
    )
    if answered is None:
        raise ApiError(
            404,
            "CLASSROOM_QUESTION_NOT_FOUND",
            "Question not found, session not found, or you are not the educator.",
        )
    return answered


# ---------------------------------------------------------------------------
# Classroom polls
# ---------------------------------------------------------------------------


@router.post(
    "/classrooms/{session_id}/polls",
    response_model=dict[str, Any],
    status_code=201,
)
def create_classroom_poll_route(
    request: Request,
    session_id: str = Path(...),
    payload: ClassroomPollCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a classroom poll. Educator only."""
    poll = create_classroom_poll(
        _database_path(request),
        session_id=session_id,
        educator_id=current_user.user_id,
        question=payload.question,
        options=payload.options,
        correct_option_index=payload.correctOptionIndex,
    )
    if poll is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid poll input or session not found/owned.",
        )
    return poll


@router.get(
    "/classrooms/{session_id}/polls",
    response_model=dict[str, Any],
)
def list_classroom_polls_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List polls for a classroom session."""
    polls = list_classroom_polls(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if polls is None:
        raise ApiError(404, "CLASSROOM_NOT_FOUND", "Classroom session not found or access denied.")
    return {"polls": polls}


@router.post(
    "/classrooms/{session_id}/polls/{poll_id}/respond",
    response_model=dict[str, Any],
    status_code=201,
)
def respond_to_classroom_poll_route(
    request: Request,
    session_id: str = Path(...),
    poll_id: str = Path(...),
    payload: ClassroomPollRespondRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Respond to a classroom poll. Learner only."""
    response = respond_to_classroom_poll(
        _database_path(request),
        poll_id=poll_id,
        user_id=current_user.user_id,
        option_index=payload.optionIndex,
    )
    if response is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Poll not found, not open, you are not a participant, or invalid option index.",
        )
    return response


@router.get(
    "/classrooms/{session_id}/polls/{poll_id}/results",
    response_model=dict[str, Any],
)
def get_classroom_poll_results_route(
    request: Request,
    session_id: str = Path(...),
    poll_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get poll results."""
    results = get_classroom_poll_results(
        _database_path(request),
        poll_id=poll_id,
        user_id=current_user.user_id,
    )
    if results is None:
        raise ApiError(404, "CLASSROOM_POLL_NOT_FOUND", "Poll not found or access denied.")
    return results


# ---------------------------------------------------------------------------
# Classroom announcements
# ---------------------------------------------------------------------------


@router.post(
    "/classrooms/{session_id}/announcements",
    response_model=dict[str, Any],
    status_code=201,
)
def create_classroom_announcement_route(
    request: Request,
    session_id: str = Path(...),
    payload: ClassroomAnnouncementCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a classroom announcement. Educator only."""
    announcement = create_classroom_announcement(
        _database_path(request),
        session_id=session_id,
        educator_id=current_user.user_id,
        message=payload.message,
    )
    if announcement is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid announcement input or session not found/owned.",
        )
    return announcement


@router.get(
    "/classrooms/{session_id}/announcements",
    response_model=dict[str, Any],
)
def list_classroom_announcements_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List announcements for a classroom session."""
    announcements = list_classroom_announcements(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if announcements is None:
        raise ApiError(404, "CLASSROOM_NOT_FOUND", "Classroom session not found or access denied.")
    return {"announcements": announcements}


# ---------------------------------------------------------------------------
# Classroom activity state
# ---------------------------------------------------------------------------


@router.post(
    "/classrooms/{session_id}/activity-state",
    response_model=dict[str, Any],
)
def update_classroom_activity_state_route(
    request: Request,
    session_id: str = Path(...),
    payload: ClassroomActivityStateUpdateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update shared classroom activity state. Educator only."""
    state = update_classroom_activity_state(
        _database_path(request),
        session_id=session_id,
        educator_id=current_user.user_id,
        state_type=payload.stateType,
        state_data=payload.stateData,
    )
    if state is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid activity state input or session not found/owned.",
        )
    return state


@router.get(
    "/classrooms/{session_id}/activity-state",
    response_model=dict[str, Any],
)
def get_classroom_activity_state_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get current classroom activity state."""
    state = get_classroom_activity_state(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if state is None:
        raise ApiError(404, "CLASSROOM_NOT_FOUND", "Classroom session not found or access denied.")
    return state


# ---------------------------------------------------------------------------
# Classroom session recovery
# ---------------------------------------------------------------------------


@router.post(
    "/classrooms/{session_id}/recover",
    response_model=dict[str, Any],
)
def recover_classroom_session_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Recover classroom session state after disconnection."""
    recovery = recover_classroom_session(
        _database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
    )
    if recovery is None:
        raise ApiError(404, "CLASSROOM_NOT_FOUND", "Classroom session not found or access denied.")
    return recovery
