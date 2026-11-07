"""M62: Collaborative AI Study Rooms API routes.

Endpoints:

* POST   /api/v1/rooms                                      - Create a study room
* GET    /api/v1/rooms                                      - List rooms for a group
* GET    /api/v1/rooms/{room_id}                            - Get one room
* PATCH  /api/v1/rooms/{room_id}                            - Update a room
* DELETE /api/v1/rooms/{room_id}                            - Delete a room
* POST   /api/v1/rooms/{room_id}/join                       - Join room
* POST   /api/v1/rooms/{room_id}/leave                      - Leave room
* GET    /api/v1/rooms/{room_id}/participants               - List participants

* POST   /api/v1/rooms/{room_id}/ai/explain                 - AI explain concept
* POST   /api/v1/rooms/{room_id}/ai/quiz                    - AI generate group quiz
* POST   /api/v1/rooms/{room_id}/ai/hint                    - AI provide hint
* POST   /api/v1/rooms/{room_id}/ai/explain-answer          - AI explain incorrect answer
* POST   /api/v1/rooms/{room_id}/ai/summarize               - AI summarize discussion

* POST   /api/v1/rooms/{room_id}/activities                 - Record activity
* GET    /api/v1/rooms/{room_id}/activities                 - List activities
* GET    /api/v1/rooms/{room_id}/summary                    - Get session summary

All endpoints require authentication and group/room membership (where applicable).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.api.v1.routes import _database_path, _settings
from app.auth import AuthUser, require_auth
from app.config import Settings
from app.errors import ApiError
from app.storage import (
    create_group_revision_item,
    create_group_study_goal,
    create_shared_note,
    create_study_group_invitation,
    create_study_room,
    create_study_session,
    create_study_space,
    delete_shared_note,
    delete_study_room,
    get_shared_note,
    get_study_room,
    get_study_room_summary,
    get_study_session,
    get_study_space,
    get_study_space_join_code,
    leave_study_room,
    list_group_revision_items,
    list_group_study_goals,
    list_shared_notes,
    list_study_room_activities,
    list_study_room_participants,
    list_study_rooms,
    list_study_sessions,
    list_study_spaces,
    list_workspace_members,
    record_study_room_activity,
    regenerate_study_space_join_code,
    join_study_room,
    revoke_study_group_invitation,
    set_workspace_member_role,
    update_group_revision_item,
    update_group_study_goal_status,
    update_shared_note,
    update_study_room,
)

router = APIRouter(prefix="/api/v1/rooms", tags=["study rooms"])


def _check_membership_or_404(
    database_path: str, room_id: str, user_id: str
) -> None:
    result = get_study_room(database_path, room_id=room_id, user_id=user_id)
    if result is None:
        raise ApiError(404, "ROOM_NOT_FOUND", "Study room not found or access denied.")


# ---------------------------------------------------------------------------
# Room CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=dict[str, Any], status_code=201)
def create_room_route(
    request: Request,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new study room for a group. Caller must be a group member."""
    space_id = payload.get("spaceId")
    title = payload.get("title", "")
    topic = payload.get("topic", "")
    objective = payload.get("objective")
    status = payload.get("status", "scheduled")

    if not space_id or not title or not topic:
        raise ApiError(422, "INVALID_REQUEST", "spaceId, title, and topic are required.")

    room = create_study_room(
        _database_path(request),
        space_id=space_id,
        created_by=current_user.user_id,
        title=title,
        topic=topic,
        objective=objective,
        status=status,
    )
    if room is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    return room


@router.get("", response_model=dict[str, Any])
def list_rooms_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    spaceId: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List study rooms for a group. Caller must be a group member."""
    if spaceId is None:
        raise ApiError(422, "INVALID_REQUEST", "spaceId is required.")
    result = list_study_rooms(
        _database_path(request),
        space_id=spaceId,
        user_id=current_user.user_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    if result is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    rooms, total = result
    return {"rooms": rooms, "total": total, "limit": limit, "offset": offset}


@router.get("/{room_id}", response_model=dict[str, Any])
def get_room_route(
    request: Request,
    room_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one study room if the current user is a member."""
    room = get_study_room(_database_path(request), room_id=room_id, user_id=current_user.user_id)
    if room is None:
        raise ApiError(404, "ROOM_NOT_FOUND", "Study room was not found.")
    return room


@router.patch("/{room_id}", response_model=dict[str, Any])
def update_room_route(
    request: Request,
    room_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a study room. Host or owner/moderator only."""
    room = update_study_room(
        _database_path(request),
        room_id=room_id,
        actor_user_id=current_user.user_id,
        title=payload.get("title"),
        topic=payload.get("topic"),
        objective=payload.get("objective"),
        status=payload.get("status"),
    )
    if room is None:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "Room not found, or you lack permission to update it.",
        )
    return room


@router.delete("/{room_id}")
def delete_room_route(
    request: Request,
    room_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a study room. Host or owner only."""
    ok = delete_study_room(
        _database_path(request), room_id=room_id, actor_user_id=current_user.user_id
    )
    if not ok:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "Room not found, or you lack permission to delete it.",
        )
    return {"roomId": room_id, "deleted": True}


# ---------------------------------------------------------------------------
# Room participants
# ---------------------------------------------------------------------------


@router.post("/{room_id}/join")
def join_room_route(
    request: Request,
    room_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Join an active study room."""
    ok = join_study_room(_database_path(request), room_id=room_id, user_id=current_user.user_id)
    if not ok:
        raise ApiError(
            400,
            "JOIN_FAILED",
            "Room not found, not active, or you are not a group member.",
        )
    return {"roomId": room_id, "joined": True}


@router.post("/{room_id}/leave")
def leave_room_route(
    request: Request,
    room_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Leave a study room."""
    ok = leave_study_room(_database_path(request), room_id=room_id, user_id=current_user.user_id)
    if not ok:
        raise ApiError(
            400,
            "LEAVE_FAILED",
            "Room not found, you are not a participant, or you are the host.",
        )
    return {"roomId": room_id, "left": True}


@router.get("/{room_id}/participants", response_model=list[dict[str, Any]])
def list_participants_route(
    request: Request,
    room_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> Any:
    """List participants of a study room."""
    participants = list_study_room_participants(
        _database_path(request), room_id=room_id, user_id=current_user.user_id
    )
    if participants is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    return participants


# ---------------------------------------------------------------------------
# AI-powered room actions
# ---------------------------------------------------------------------------


@router.post("/{room_id}/ai/explain", response_model=dict[str, Any])
async def ai_explain_route(
    request: Request,
    room_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """AI explain a concept to the group, grounded in shared resources."""
    room = get_study_room(_database_path(request), room_id=room_id, user_id=current_user.user_id)
    if room is None:
        raise ApiError(404, "ROOM_NOT_FOUND", "Study room was not found.")
    concept = payload.get("concept", "")
    if not concept:
        raise ApiError(422, "INVALID_REQUEST", "concept is required.")

    # Retrieve evidence from shared notes and group sources.
    from app.companion import run_companion
    from app.storage import list_shared_notes

    notes_result = list_shared_notes(
        _database_path(request), space_id=room["spaceId"], user_id=current_user.user_id,
        limit=10, offset=0,
    )
    evidence = []
    if notes_result:
        notes, _ = notes_result
        for note in notes:
            evidence.append({
                "citationId": note["noteId"],
                "documentId": note["noteId"],
                "displayFilename": note["title"],
                "pageNumber": 1,
                "chunkIndex": 0,
                "charStart": 0,
                "charEnd": len(note.get("content", "")),
                "text": note.get("content", ""),
                "retrieval": {"method": "shared_note", "rank": 1},
            })

    from app.retrieval.service import answer_from_evidence
    from app.tutor import TutorUnavailable, generate_grounded_answer
    from app.config import Settings as _Settings
    settings = _settings(request)

    try:
        generated = await __import__("asyncio").to_thread(
            generate_grounded_answer,
            settings,
            question=f"Explain this concept for a study group: {concept}",
            concept=concept,
            evidence=evidence,
        )
    except TutorUnavailable:
        extractive = answer_from_evidence(concept, evidence)
        return {
            "content": extractive["answer"],
            "citations": extractive["citations"],
            "status": extractive["status"],
            "metadata": {"source": "extractive"},
        }
    except Exception:
        return {
            "content": "I couldn't form a grounded explanation from the available shared materials.",
            "citations": [],
            "status": "insufficient_evidence",
            "metadata": {},
        }

    citations = [
        {
            "citationId": c["citationId"],
            "documentId": c["documentId"],
            "displayFilename": c["displayFilename"],
            "pageNumber": c["pageNumber"],
            "chunkIndex": c["chunkIndex"],
            "charStart": c["charStart"],
            "charEnd": c["charEnd"],
            "text": c["text"],
            "retrieval": c["retrieval"],
        }
        for c in generated["citations"]
    ]
    return {
        "content": generated["answer"],
        "citations": citations,
        "status": generated["status"],
        "metadata": {"source": "grounded_tutor"},
    }


@router.post("/{room_id}/ai/quiz", response_model=dict[str, Any])
async def ai_quiz_route(
    request: Request,
    room_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """AI generate a group quiz grounded in shared resources."""
    room = get_study_room(_database_path(request), room_id=room_id, user_id=current_user.user_id)
    if room is None:
        raise ApiError(404, "ROOM_NOT_FOUND", "Study room was not found.")
    topic = payload.get("topic", room["topic"])
    question_count = min(int(payload.get("questionCount", 5)), 10)

    from app.companion import run_companion
    from app.storage import list_shared_notes
    from app.quiz import generate_quiz_questions

    notes_result = list_shared_notes(
        _database_path(request), space_id=room["spaceId"], user_id=current_user.user_id,
        limit=10, offset=0,
    )
    evidence = []
    if notes_result:
        notes, _ = notes_result
        for note in notes:
            evidence.append({
                "citationId": note["noteId"],
                "documentId": note["noteId"],
                "displayFilename": note["title"],
                "pageNumber": 1,
                "chunkIndex": 0,
                "charStart": 0,
                "charEnd": len(note.get("content", "")),
                "text": note.get("content", ""),
                "retrieval": {"method": "shared_note", "rank": 1},
            })

    from app.tutor import TutorUnavailable
    settings = _settings(request)

    try:
        generated = await __import__("asyncio").to_thread(
            generate_quiz_questions,
            settings,
            topic=topic,
            count=question_count,
            evidence=evidence,
        )
    except TutorUnavailable:
        return {
            "quizId": None,
            "topic": topic,
            "questions": [],
            "status": "model_unavailable",
            "metadata": {},
        }
    except Exception:
        return {
            "quizId": None,
            "topic": topic,
            "questions": [],
            "status": "error",
            "metadata": {},
        }

    if not generated:
        return {
            "quizId": None,
            "topic": topic,
            "questions": [],
            "status": "insufficient_evidence",
            "metadata": {},
        }

    quiz_id = f"room_quiz_{secrets.token_urlsafe(8)}"
    created_at = __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()

    # Record activity.
    from app.storage import record_study_room_activity
    record_study_room_activity(
        _database_path(request),
        room_id=room_id,
        user_id=current_user.user_id,
        activity_type="quiz_challenge",
        topic=topic,
        question=f"AI-generated {question_count}-question quiz",
        answer=None,
        result=None,
        metadata={"quizId": quiz_id, "questionCount": len(generated)},
    )

    return {
        "quizId": quiz_id,
        "topic": topic,
        "questions": generated,
        "status": "ready",
        "metadata": {"createdAt": created_at},
    }


@router.post("/{room_id}/ai/hint", response_model=dict[str, Any])
async def ai_hint_route(
    request: Request,
    room_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """AI provide a hint for a question without revealing the answer."""
    room = get_study_room(_database_path(request), room_id=room_id, user_id=current_user.user_id)
    if room is None:
        raise ApiError(404, "ROOM_NOT_FOUND", "Study room was not found.")
    question = payload.get("question", "")
    if not question:
        raise ApiError(422, "INVALID_REQUEST", "question is required.")

    from app.retrieval.service import answer_from_evidence
    from app.storage import list_shared_notes

    notes_result = list_shared_notes(
        _database_path(request), space_id=room["spaceId"], user_id=current_user.user_id,
        limit=5, offset=0,
    )
    evidence = []
    if notes_result:
        notes, _ = notes_result
        for note in notes:
            evidence.append({
                "citationId": note["noteId"],
                "text": note.get("content", ""),
            })

    # Generate a hint (not the full answer) using grounded tutor.
    from app.tutor import TutorUnavailable, generate_grounded_answer
    settings = _settings(request)

    try:
        generated = await __import__("asyncio").to_thread(
            generate_grounded_answer,
            settings,
            question=f"Give a hint (not the full answer) for this question: {question}",
            concept=None,
            evidence=evidence,
        )
    except TutorUnavailable:
        extractive = answer_from_evidence(question, evidence)
        return {
            "content": extractive["answer"],
            "citations": extractive["citations"],
            "status": extractive["status"],
        }
    except Exception:
        return {
            "content": "I couldn't generate a hint from the available materials.",
            "citations": [],
            "status": "insufficient_evidence",
        }

    citations = [
        {
            "citationId": c["citationId"],
            "documentId": c["documentId"],
            "displayFilename": c["displayFilename"],
            "pageNumber": c["pageNumber"],
            "chunkIndex": c["chunkIndex"],
            "charStart": c["charStart"],
            "charEnd": c["charEnd"],
            "text": c["text"],
            "retrieval": c["retrieval"],
        }
        for c in generated["citations"]
    ]
    return {
        "content": generated["answer"],
        "citations": citations,
        "status": generated["status"],
    }


@router.post("/{room_id}/ai/explain-answer", response_model=dict[str, Any])
async def ai_explain_answer_route(
    request: Request,
    room_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """AI explain why an answer is incorrect, grounded in shared resources."""
    room = get_study_room(_database_path(request), room_id=room_id, user_id=current_user.user_id)
    if room is None:
        raise ApiError(404, "ROOM_NOT_FOUND", "Study room was not found.")
    question = payload.get("question", "")
    submitted = payload.get("submittedAnswer", "")
    if not question or not submitted:
        raise ApiError(422, "INVALID_REQUEST", "question and submittedAnswer are required.")

    from app.retrieval.service import answer_from_evidence
    from app.storage import list_shared_notes
    from app.tutor import TutorUnavailable, generate_grounded_answer

    notes_result = list_shared_notes(
        _database_path(request), space_id=room["spaceId"], user_id=current_user.user_id,
        limit=5, offset=0,
    )
    evidence = []
    if notes_result:
        notes, _ = notes_result
        for note in notes:
            evidence.append({
                "citationId": note["noteId"],
                "text": note.get("content", ""),
            })

    settings = _settings(request)
    try:
        generated = await __import__("asyncio").to_thread(
            generate_grounded_answer,
            settings,
            question=f"The user answered '{submitted}' to: {question}. Explain why this is incorrect and provide the correct answer.",
            concept=None,
            evidence=evidence,
        )
    except TutorUnavailable:
        extractive = answer_from_evidence(question, evidence)
        return {
            "content": extractive["answer"],
            "citations": extractive["citations"],
            "status": extractive["status"],
        }
    except Exception:
        return {
            "content": "I couldn't explain the answer from the available materials.",
            "citations": [],
            "status": "insufficient_evidence",
        }

    citations = [
        {
            "citationId": c["citationId"],
            "documentId": c["documentId"],
            "displayFilename": c["displayFilename"],
            "pageNumber": c["pageNumber"],
            "chunkIndex": c["chunkIndex"],
            "charStart": c["charStart"],
            "charEnd": c["charEnd"],
            "text": c["text"],
            "retrieval": c["retrieval"],
        }
        for c in generated["citations"]
    ]
    return {
        "content": generated["answer"],
        "citations": citations,
        "status": generated["status"],
    }


@router.post("/{room_id}/ai/summarize", response_model=dict[str, Any])
async def ai_summarize_route(
    request: Request,
    room_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """AI summarize a study room session."""
    room = get_study_room(_database_path(request), room_id=room_id, user_id=current_user.user_id)
    if room is None:
        raise ApiError(404, "ROOM_NOT_FOUND", "Study room was not found.")

    from app.storage import list_study_room_activities, create_study_room_summary
    from app.tutor import TutorUnavailable, generate_grounded_answer
    settings = _settings(request)

    activities_result = list_study_room_activities(
        _database_path(request), room_id=room_id, user_id=current_user.user_id,
        limit=100, offset=0,
    )
    activities = []
    topics_covered = set()
    questions_attempted = 0
    common_mistakes = []
    unresolved_questions = []

    if activities_result:
        acts, _ = activities_result
        for act in acts:
            topics_covered.add(act.get("activityType", ""))
            if act.get("question"):
                questions_attempted += 1
                unresolved_questions.append(act["question"])
            if act.get("result") in {"incorrect", "wrong"}:
                common_mistakes.append(act.get("question", ""))

    # Build evidence from activities for the summary.
    evidence = []
    for act in activities:
        if act.get("answer"):
            evidence.append({
                "citationId": act["activityId"],
                "text": act["answer"],
            })

    # Generate AI summary.
    try:
        generated = await __import__("asyncio").to_thread(
            generate_grounded_answer,
            settings,
            question=f"Summarize this study session covering: {', '.join(topics_covered)}. Identify common mistakes, concepts needing revision, unresolved questions, and follow-up recommendations.",
            concept=None,
            evidence=evidence,
        )
    except TutorUnavailable:
        return {
            "summaryId": None,
            "roomId": room_id,
            "topicsCovered": list(topics_covered),
            "questionsAttempted": questions_attempted,
            "commonMistakes": common_mistakes,
            "conceptsNeedingRevision": [],
            "unresolvedQuestions": unresolved_questions,
            "followUpRecommendations": ["Review the unresolved questions listed above."],
            "aiGenerated": False,
            "createdAt": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
        }
    except Exception:
        return {
            "summaryId": None,
            "roomId": room_id,
            "topicsCovered": list(topics_covered),
            "questionsAttempted": questions_attempted,
            "commonMistakes": common_mistakes,
            "conceptsNeedingRevision": [],
            "unresolvedQuestions": unresolved_questions,
            "followUpRecommendations": ["Review the session materials."],
            "aiGenerated": False,
            "createdAt": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
        }

    summary = create_study_room_summary(
        _database_path(request),
        room_id=room_id,
        topics_covered=list(topics_covered),
        questions_attempted=questions_attempted,
        common_mistakes=common_mistakes,
        concepts_needing_revision=[generated["answer"][:200]],
        unresolved_questions=unresolved_questions[:5],
        follow_up_recommendations=["Review concepts marked for revision."],
        ai_generated=True,
    )
    if summary is None:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Could not save summary.")

    return summary


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


@router.post("/{room_id}/activities", response_model=dict[str, Any], status_code=201)
def record_activity_route(
    request: Request,
    room_id: str,
    payload: dict[str, Any] = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record an activity in a study room."""
    activity = record_study_room_activity(
        _database_path(request),
        room_id=room_id,
        user_id=current_user.user_id,
        activity_type=payload.get("activityType", ""),
        topic=payload.get("topic"),
        question=payload.get("question"),
        answer=payload.get("answer"),
        result=payload.get("result"),
        metadata=payload.get("metadata"),
    )
    if activity is None:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "Room not found, you are not a participant, or invalid activity type.",
        )
    return activity


@router.get("/{room_id}/activities", response_model=dict[str, Any])
def list_activities_route(
    request: Request,
    room_id: str,
    current_user: AuthUser = Depends(require_auth),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List activities for a study room."""
    result = list_study_room_activities(
        _database_path(request),
        room_id=room_id,
        user_id=current_user.user_id,
        limit=limit,
        offset=offset,
    )
    if result is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    activities, total = result
    return {"activities": activities, "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# Session summary
# ---------------------------------------------------------------------------


@router.get("/{room_id}/summary", response_model=dict[str, Any])
def get_summary_route(
    request: Request,
    room_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get the session summary for a study room."""
    summary = get_study_room_summary(
        _database_path(request), room_id=room_id, user_id=current_user.user_id
    )
    if summary is None:
        raise ApiError(404, "SUMMARY_NOT_FOUND", "No summary found for this room.")
    return summary
