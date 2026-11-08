"""M72: Communication & Learning Progress Portal service layer.

Provides:

* Classroom messaging with authorization (educator/participant only).
* Read/unread message status tracking.
* In-app notifications with preferences and duplicate prevention.
* Learner progress portal aggregating observed records only.
* Guardian access is NOT implemented: the existing account architecture has
  no guardian/parent relationship table, no invitation/consent flow, and no
  age verification. Per the M72 specification, when guardian access cannot
  be safely established, the limitation is documented and guardian access is
  not enabled.

All functions are user-scoped and enforce server-side authorization.
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.storage import (
    connect_database,
    create_classroom_announcement,
    create_notification,
    get_classroom_session,
    get_notification_preferences,
    get_user_by_id,
    list_notifications,
    list_quiz_attempts_for_user,
    list_study_sessions_for_user,
    list_topic_reviews_for_user,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


# ---------------------------------------------------------------------------
# Classroom messaging
# ---------------------------------------------------------------------------


def send_classroom_message_service(
    database_path: Path,
    *,
    session_id: str,
    sender_id: str,
    content: str,
    message_type: str = "classroom",
    recipient_id: str | None = None,
    create_notification_for_recipients: bool = True,
) -> dict[str, Any] | None:
    """Send a classroom message and optionally create notifications.

    Authorization: sender must be a participant or educator of the session.
    Direct messages require the recipient to also be a participant.
    """
    from app.storage import send_classroom_message as _send

    result = _send(
        database_path=database_path,
        session_id=session_id,
        sender_id=sender_id,
        content=content,
        message_type=message_type,
        recipient_id=recipient_id,
    )
    if result is None:
        return None

    # Create notifications for recipients (excluding sender).
    if create_notification_for_recipients:
        session = get_classroom_session(
            database_path, session_id=session_id, user_id=sender_id
        )
        if session is not None:
            prefs = get_notification_preferences(
                database_path, user_id=sender_id
            )
            announcement_enabled = (
                prefs["announcementEnabled"] if prefs else True
            )

            if message_type == "direct" and recipient_id is not None:
                recipient_prefs = get_notification_preferences(
                    database_path, user_id=recipient_id
                )
                recipient_in_app = (
                    recipient_prefs["inAppEnabled"]
                    if recipient_prefs
                    else True
                )
                if recipient_in_app:
                    create_notification(
                        database_path,
                        user_id=recipient_id,
                        type="feedback",
                        title="New direct message",
                        body=(
                            "You received a new direct message from a "
                            "classroom participant."
                        ),
                        related_id=result["messageId"],
                        related_type="message",
                    )
            else:
                # Classroom broadcast: notify all other participants.
                with closing(connect_database(database_path)) as conn:
                    participants = conn.execute(
                        """
                        SELECT user_id FROM classroom_participants
                        WHERE session_id = ? AND user_id != ?
                        """,
                        (session_id, sender_id),
                    ).fetchall()
                for p in participants:
                    pid = p["user_id"]
                    pid_prefs = get_notification_preferences(
                        database_path, user_id=pid
                    )
                    pid_in_app = (
                        pid_prefs["inAppEnabled"] if pid_prefs else True
                    )
                    if pid_in_app and announcement_enabled:
                        create_notification(
                            database_path,
                            user_id=pid,
                            type="announcement",
                            title="New classroom message",
                            body=(
                                f"A new message was posted in classroom "
                                f"{session_id}."
                            ),
                            related_id=result["messageId"],
                            related_type="message",
                        )
    return result


def get_classroom_messages_service(
    database_path: Path,
    *,
    session_id: str,
    user_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]] | None:
    """List classroom messages for a session with authorization."""
    from app.storage import list_classroom_messages as _list

    return _list(
        database_path=database_path,
        session_id=session_id,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )


def mark_classroom_message_read_service(
    database_path: Path,
    *,
    message_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Mark a classroom message as read for a user."""
    from app.storage import mark_message_read as _mark

    return _mark(
        database_path=database_path,
        message_id=message_id,
        user_id=user_id,
    )


def get_unread_message_counts_service(
    database_path: Path, *, user_id: str
) -> list[dict[str, Any]]:
    """List unread message counts per session for a user."""
    from app.storage import list_unread_message_counts as _list

    return _list(database_path=database_path, user_id=user_id)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def create_classroom_announcement_with_notification(
    database_path: Path,
    *,
    session_id: str,
    educator_id: str,
    message: str,
) -> dict[str, Any] | None:
    """Create a classroom announcement and notify all participants.

    Reuses the existing M70 announcement storage function and adds
    in-app notifications for each participant (excluding the educator).
    """
    announcement = create_classroom_announcement(
        database_path=database_path,
        session_id=session_id,
        educator_id=educator_id,
        message=message,
    )
    if announcement is None:
        return None

    session = get_classroom_session(
        database_path, session_id=session_id, user_id=educator_id
    )
    if session is None:
        return announcement

    with closing(connect_database(database_path)) as conn:
        participants = conn.execute(
            """
            SELECT user_id FROM classroom_participants
            WHERE session_id = ? AND user_id != ?
            """,
            (session_id, educator_id),
        ).fetchall()

    for p in participants:
        pid = p["user_id"]
        prefs = get_notification_preferences(database_path, user_id=pid)
        in_app = prefs["inAppEnabled"] if prefs else True
        announcement_enabled = (
            prefs["announcementEnabled"] if prefs else True
        )
        if in_app and announcement_enabled:
            create_notification(
                database_path,
                user_id=pid,
                type="announcement",
                title="New classroom announcement",
                body=message[:200],
                related_id=announcement["announcementId"],
                related_type="announcement",
                expires_in_days=30,
            )
    return announcement


def list_user_notifications_service(
    database_path: Path,
    *,
    user_id: str,
    unread_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List notifications for a user."""
    from app.storage import list_notifications as _list

    return _list(
        database_path=database_path,
        user_id=user_id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )


def mark_user_notification_read_service(
    database_path: Path,
    *,
    notification_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Mark a notification as read for a user."""
    from app.storage import mark_notification_read as _mark

    return _mark(
        database_path=database_path,
        notification_id=notification_id,
        user_id=user_id,
    )


def get_user_notification_preferences_service(
    database_path: Path, *, user_id: str
) -> dict[str, Any] | None:
    """Get notification preferences for a user."""
    from app.storage import get_notification_preferences as _get

    return _get(database_path=database_path, user_id=user_id)


def update_user_notification_preferences_service(
    database_path: Path,
    *,
    user_id: str,
    email_enabled: bool | None = None,
    in_app_enabled: bool | None = None,
    announcement_enabled: bool | None = None,
    assignment_enabled: bool | None = None,
    feedback_enabled: bool | None = None,
    assessment_enabled: bool | None = None,
    progress_update_enabled: bool | None = None,
) -> dict[str, Any] | None:
    """Update notification preferences for a user."""
    from app.storage import update_notification_preferences as _update

    return _update(
        database_path=database_path,
        user_id=user_id,
        email_enabled=email_enabled,
        in_app_enabled=in_app_enabled,
        announcement_enabled=announcement_enabled,
        assignment_enabled=assignment_enabled,
        feedback_enabled=feedback_enabled,
        assessment_enabled=assessment_enabled,
        progress_update_enabled=progress_update_enabled,
    )


# ---------------------------------------------------------------------------
# Learner progress portal
# ---------------------------------------------------------------------------


def get_learner_progress_portal(
    database_path: Path, *, user_id: str
) -> dict[str, Any]:
    """Return the learner progress portal for a user.

    Aggregates observed records only - no estimates or fabrications.
    Distinguishes observed performance from estimates.
    """
    # Quiz attempts (assessment results)
    attempts = list_quiz_attempts_for_user(database_path, user_id=user_id)

    # Build assessment summary by topic
    topic_data: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        tid = attempt["topicId"]
        if tid not in topic_data:
            topic_data[tid] = {
                "topicId": tid,
                "topic": attempt["topic"],
                "totalAttempts": 0,
                "correctAttempts": 0,
                "lastUpdated": attempt["createdAt"],
            }
        td = topic_data[tid]
        td["totalAttempts"] += 1
        if attempt["outcome"] == "correct":
            td["correctAttempts"] += 1
        if attempt["createdAt"] > td["lastUpdated"]:
            td["lastUpdated"] = attempt["createdAt"]

    assessments: list[dict[str, Any]] = []
    for tid, td in topic_data.items():
        scored = td["correctAttempts"]
        accuracy = scored / td["totalAttempts"] if td["totalAttempts"] > 0 else None
        assessments.append({
            "topicId": tid,
            "topic": td["topic"],
            "totalAttempts": td["totalAttempts"],
            "correctAttempts": td["correctAttempts"],
            "accuracy": accuracy,
            "lastUpdated": td["lastUpdated"],
        })
    assessments.sort(key=lambda a: a["topic"])

    # Revision activity (topic reviews)
    reviews = list_topic_reviews_for_user(database_path, user_id=user_id)
    revisions: list[dict[str, Any]] = []
    for r in reviews:
        revisions.append({
            "topicId": r["topicId"],
            "topic": r["topic"],
            "lastOutcome": r["lastOutcome"],
            "attemptedAt": r["attemptedAt"],
            "dueAt": r["dueAt"],
        })
    revisions.sort(key=lambda r: r["dueAt"] or "")

    # Study sessions
    sessions = list_study_sessions_for_user(database_path, user_id=user_id)
    study_sessions: list[dict[str, Any]] = []
    for s in sessions:
        study_sessions.append({
            "sessionId": s["sessionId"],
            "topicId": s["topicId"],
            "topic": s["topic"],
            "startedAt": s["startedAt"],
            "completedAt": s["completedAt"],
        })
    study_sessions.sort(key=lambda s: s["completedAt"] or s["startedAt"], reverse=True)

    # Unread notification count
    notifications = list_notifications(
        database_path, user_id=user_id, unread_only=True
    )
    unread_notification_count = len(notifications)

    # Unread message counts
    from app.storage import list_unread_message_counts as _list_unread

    unread_counts = _list_unread(
        database_path, user_id=user_id
    )
    message_count = sum(uc["unreadCount"] for uc in unread_counts)

    return {
        "userId": user_id,
        "assignments": [],  # No assignment table exists; placeholder for M73
        "assessments": assessments,
        "revisions": revisions,
        "studySessions": study_sessions,
        "messageCount": message_count,
        "unreadNotificationCount": unread_notification_count,
        "dataSources": [
            "quiz_attempts (assessment results)",
            "topic_review_items (revision activity)",
            "study_sessions (study activity)",
            "notifications (unread count)",
            "classroom_messages (unread count)",
        ],
    }


def get_learner_progress_for_educator(
    database_path: Path, *, educator_id: str, target_user_id: str
) -> dict[str, Any] | None:
    """Return a learner's progress portal for an educator.

    Authorization: educator must share a classroom session with the target
    learner. Returns None if not authorized.
    """
    # Check if educator and target share a classroom session.
    # Educator owns the session OR both are participants.
    with closing(connect_database(database_path)) as conn:
        shared = conn.execute(
            """
            SELECT 1 FROM classroom_sessions cs
            LEFT JOIN classroom_participants lp
              ON cs.session_id = lp.session_id AND lp.user_id = ?
            WHERE cs.educator_id = ? AND lp.user_id IS NOT NULL
            LIMIT 1
            """,
            (target_user_id, educator_id),
        ).fetchone()
    if shared is None:
        return None

    # Target user must exist.
    target = get_user_by_id(database_path, target_user_id)
    if target is None:
        return None

    return get_learner_progress_portal(
        database_path, user_id=target_user_id
    )
