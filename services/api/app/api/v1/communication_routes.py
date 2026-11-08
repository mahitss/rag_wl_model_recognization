"""M72: Communication & Learning Progress Portal API routes.

Endpoints:

* POST   /api/v1/classrooms/{session_id}/messages        - Send classroom message
* GET    /api/v1/classrooms/{session_id}/messages        - List classroom messages
* PATCH  /api/v1/messages/{message_id}/read              - Mark message read
* GET    /api/v1/messages/unread-counts                  - Unread counts per session
* POST   /api/v1/classrooms/{session_id}/announcements   - Create announcement + notify
* GET    /api/v1/notifications                           - List notifications
* PATCH  /api/v1/notifications/{notification_id}/read    - Mark notification read
* GET    /api/v1/notification-preferences                - Get preferences
* PATCH  /api/v1/notification-preferences                - Update preferences
* GET    /api/v1/progress                                - Learner progress portal (self)
* GET    /api/v1/progress/{user_id}                      - Educator view of learner progress

All endpoints require authentication. Message and progress access is
scoped to classroom participation (educator or participant).
Guardian access is NOT implemented (see communication.py for rationale).

Real-time transport: HTTP polling only. No WebSocket infrastructure exists
in this codebase; clients poll list endpoints for updates.
"""
# ruff: noqa: B008

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.auth import AuthUser, require_auth
from app.communication import (
    get_classroom_messages_service,
    get_learner_progress_for_educator,
    get_learner_progress_portal,
    get_unread_message_counts_service,
    get_user_notification_preferences_service,
    list_user_notifications_service,
    mark_classroom_message_read_service,
    mark_user_notification_read_service,
    send_classroom_message_service,
    update_user_notification_preferences_service,
)
from app.errors import ApiError
from app.schemas import (
    ClassroomMessageCreateRequest,
    NotificationPreferencesUpdateRequest,
)

router = APIRouter(prefix="/api/v1", tags=["communication"])


def _database_path(request: Request) -> Path:
    return request.app.state.settings.database_path


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Classroom messages
# ---------------------------------------------------------------------------


@router.post(
    "/classrooms/{session_id}/messages",
    response_model=dict[str, Any],
    status_code=201,
)
def send_classroom_message_route(
    request: Request,
    session_id: str = Path(...),
    payload: ClassroomMessageCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Send a classroom message. Sender must be a participant or educator."""
    message = send_classroom_message_service(
        database_path=_database_path(request),
        session_id=session_id,
        sender_id=current_user.user_id,
        content=payload.content,
        message_type=payload.messageType,
        recipient_id=payload.recipientId,
    )
    if message is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid message input or you are not a participant of this session.",
        )
    return message


@router.get(
    "/classrooms/{session_id}/messages",
    response_model=dict[str, Any],
)
def list_classroom_messages_route(
    request: Request,
    session_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List classroom messages for a session."""
    messages = get_classroom_messages_service(
        database_path=_database_path(request),
        session_id=session_id,
        user_id=current_user.user_id,
        limit=limit,
        offset=offset,
    )
    if messages is None:
        raise ApiError(
            404,
            "CLASSROOM_NOT_FOUND",
            "Classroom session not found or access denied.",
        )
    return {"messages": messages}


@router.patch(
    "/messages/{message_id}/read",
    response_model=dict[str, Any],
)
def mark_message_read_route(
    request: Request,
    message_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Mark a classroom message as read."""
    result = mark_classroom_message_read_service(
        database_path=_database_path(request),
        message_id=message_id,
        user_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            404,
            "MESSAGE_NOT_FOUND",
            "Message not found or you are not authorized to read it.",
        )
    return result


@router.get(
    "/messages/unread-counts",
    response_model=dict[str, Any],
)
def get_unread_message_counts_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List unread message counts per session for the current user."""
    counts = get_unread_message_counts_service(
        database_path=_database_path(request),
        user_id=current_user.user_id,
    )
    return {"unreadCounts": counts}


# ---------------------------------------------------------------------------
# Classroom announcements (with notifications)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@router.get(
    "/notifications",
    response_model=dict[str, Any],
)
def list_notifications_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    unread_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List notifications for the current user."""
    notifications = list_user_notifications_service(
        database_path=_database_path(request),
        user_id=current_user.user_id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )
    return {"notifications": notifications}


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=dict[str, Any],
)
def mark_notification_read_route(
    request: Request,
    notification_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Mark a notification as read."""
    result = mark_user_notification_read_service(
        database_path=_database_path(request),
        notification_id=notification_id,
        user_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            404,
            "NOTIFICATION_NOT_FOUND",
            "Notification not found or you do not own it.",
        )
    return result


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------


@router.get(
    "/notification-preferences",
    response_model=dict[str, Any],
)
def get_notification_preferences_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get notification preferences for the current user."""
    prefs = get_user_notification_preferences_service(
        database_path=_database_path(request),
        user_id=current_user.user_id,
    )
    if prefs is None:
        raise ApiError(
            404,
            "USER_NOT_FOUND",
            "User not found.",
        )
    return prefs


@router.patch(
    "/notification-preferences",
    response_model=dict[str, Any],
)
def update_notification_preferences_route(
    request: Request,
    payload: NotificationPreferencesUpdateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update notification preferences for the current user."""
    prefs = update_user_notification_preferences_service(
        database_path=_database_path(request),
        user_id=current_user.user_id,
        email_enabled=payload.emailEnabled,
        in_app_enabled=payload.inAppEnabled,
        announcement_enabled=payload.announcementEnabled,
        assignment_enabled=payload.assignmentEnabled,
        feedback_enabled=payload.feedbackEnabled,
        assessment_enabled=payload.assessmentEnabled,
        progress_update_enabled=payload.progressUpdateEnabled,
    )
    if prefs is None:
        raise ApiError(
            404,
            "USER_NOT_FOUND",
            "User not found.",
        )
    return prefs


# ---------------------------------------------------------------------------
# Learner progress portal
# ---------------------------------------------------------------------------


@router.get(
    "/progress",
    response_model=dict[str, Any],
)
def get_learner_progress_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the learner progress portal for the current user."""
    portal = get_learner_progress_portal(
        database_path=_database_path(request),
        user_id=current_user.user_id,
    )
    return portal


@router.get(
    "/progress/{target_user_id}",
    response_model=dict[str, Any],
)
def get_learner_progress_for_educator_route(
    request: Request,
    target_user_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return a learner's progress portal for an educator.

    Authorization: educator must share a classroom session with the target
    learner.
    """
    portal = get_learner_progress_for_educator(
        database_path=_database_path(request),
        educator_id=current_user.user_id,
        target_user_id=target_user_id,
    )
    if portal is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not authorized to view this learner's progress.",
        )
    return portal
