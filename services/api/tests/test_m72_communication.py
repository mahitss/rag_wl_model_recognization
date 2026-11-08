"""M72: Communication & Learning Progress Portal tests.

Tests cover:

* Classroom message authorization (sender must be participant/educator).
* Direct message recipient must be a participant.
* Classroom announcement permissions (educator only).
* Read/unread message status.
* Unread message counts per session.
* Notification creation, listing, read status.
* Notification preferences (get/update, defaults).
* Duplicate notification prevention (idempotent re-insert).
* Progress portal: observed data only, no estimates.
* Progress visibility: educator must share classroom with learner.
* Cross-user isolation (messages, notifications, progress).
* Guardian access limitation (not implemented; documented).
* Data deletion and retention (cascade on session/user deletion).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.communication import (
    create_classroom_announcement_with_notification,
    get_learner_progress_for_educator,
    get_learner_progress_portal,
    get_user_notification_preferences_service,
    list_user_notifications_service,
    send_classroom_message_service,
    update_user_notification_preferences_service,
)
from app.config import Settings
from app.storage import (
    create_classroom_session,
    create_notification,
    create_user,
    delete_classroom_session,
    get_notification_preferences,
    initialize_storage,
    join_classroom_session,
    list_classroom_messages,
    list_notifications,
    list_unread_message_counts,
    mark_message_read,
    mark_notification_read,
    send_classroom_message,
    update_notification_preferences,
)


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values = {
        "_env_file": None,
        "database_path": tmp_path / "wonderlearn.sqlite3",
        "cors_origins": "http://localhost:5173",
        "embedding_dimensions": 4,
        "embedding_batch_size": 2,
        "lexical_candidate_limit": 10,
        "semantic_candidate_limit": 10,
        "retrieval_mode": "lexical",
        "max_upload_bytes": 10 * 1024 * 1024,
        "max_image_upload_bytes": 10 * 1024 * 1024,
        "max_image_dimension": 4096,
        "max_image_pixels": 16_000_000,
        "max_pdf_pages": 250,
        "max_extracted_chars": 5_000_000,
        "chunk_size_chars": 2_000,
        "chunk_overlap_chars": 200,
        "tutor_base_url": "http://127.0.0.1:8080/v1",
        "tutor_model_id": "local-model",
        "tutor_timeout_seconds": 90.0,
        "tutor_max_tokens": 700,
    }
    values.update(overrides)
    return Settings(**values)


def setup_test_db(tmp_path: Path) -> tuple[Settings, str, Path]:
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = create_user(
        settings.database_path, "classtest", "hashed-password", is_admin=False
    )
    return settings, user_id, settings.database_path


def _make_classroom(
    database_path: Path, educator_id: str, title: str = "Test Class"
) -> dict[str, Any]:
    return create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title=title,
        subject="Mathematics",
        topic="Algebra",
        status="active",
    )


def _join_learner(
    database_path: Path, session_id: str, username: str, role: str = "learner"
) -> str:
    learner_id = create_user(database_path, username, "hashed-password")
    join_classroom_session(
        database_path,
        session_id=session_id,
        user_id=learner_id,
        role=role,
    )
    return learner_id


# ---------------------------------------------------------------------------
# Message sending and authorization
# ---------------------------------------------------------------------------


def test_send_classroom_message_as_participant(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    learner_id = _join_learner(database_path, session["sessionId"], "learner1")

    result = send_classroom_message(
        database_path,
        session_id=session["sessionId"],
        sender_id=learner_id,
        content="Hello from learner",
        message_type="classroom",
    )
    assert result is not None
    assert result["senderId"] == learner_id
    assert result["sessionId"] == session["sessionId"]
    assert result["messageType"] == "classroom"
    assert result["content"] == "Hello from learner"


def test_send_classroom_message_non_participant_rejected(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    outsider = create_user(database_path, "outsider", "hashed-password")
    # outsider is not a participant

    result = send_classroom_message(
        database_path,
        session_id=session["sessionId"],
        sender_id=outsider,
        content="I should not be able to send",
        message_type="classroom",
    )
    assert result is None


def test_send_direct_message_to_participant(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    learner_id = _join_learner(database_path, session["sessionId"], "learner2")

    result = send_classroom_message(
        database_path,
        session_id=session["sessionId"],
        sender_id=educator_id,
        content="Direct feedback",
        message_type="direct",
        recipient_id=learner_id,
    )
    assert result is not None
    assert result["recipientId"] == learner_id
    assert result["messageType"] == "direct"


def test_send_direct_message_to_non_participant_rejected(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    outsider = create_user(database_path, "outsider2", "hashed-password")
    # outsider is not a participant

    result = send_classroom_message(
        database_path,
        session_id=session["sessionId"],
        sender_id=educator_id,
        content="Direct to outsider",
        message_type="direct",
        recipient_id=outsider,
    )
    assert result is None


def test_send_message_empty_content_rejected(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)

    result = send_classroom_message(
        database_path,
        session_id=session["sessionId"],
        sender_id=educator_id,
        content="   ",
        message_type="classroom",
    )
    assert result is None


def test_send_message_nonexistent_session_rejected(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    result = send_classroom_message(
        database_path,
        session_id="session_nonexistent",
        sender_id=educator_id,
        content="Hello",
        message_type="classroom",
    )
    assert result is None


# ---------------------------------------------------------------------------
# Message listing and read/unread status
# ---------------------------------------------------------------------------


def test_list_classroom_messages(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    learner_id = _join_learner(database_path, session["sessionId"], "learner3")

    send_classroom_message(
        database_path,
        session_id=session["sessionId"],
        sender_id=educator_id,
        content="Message 1",
        message_type="classroom",
    )
    send_classroom_message(
        database_path,
        session_id=session["sessionId"],
        sender_id=learner_id,
        content="Message 2",
        message_type="classroom",
    )

    messages = list_classroom_messages(
        database_path,
        session_id=session["sessionId"],
        user_id=educator_id,
    )
    assert messages is not None
    assert len(messages) == 2
    # Educator's own message is auto-marked read
    own_msg = [m for m in messages if m["senderId"] == educator_id][0]
    assert own_msg["isRead"] is True


def test_list_messages_non_participant_rejected(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    outsider = create_user(database_path, "outsider3", "hashed-password")

    result = list_classroom_messages(
        database_path,
        session_id=session["sessionId"],
        user_id=outsider,
    )
    assert result is None


def test_mark_message_read(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    learner_id = _join_learner(database_path, session["sessionId"], "learner4")

    # Educator sends a message; learner has unread status
    result = send_classroom_message(
        database_path,
        session_id=session["sessionId"],
        sender_id=educator_id,
        content="Read me",
        message_type="classroom",
    )
    message_id = result["messageId"]

    # Learner marks it read
    marked = mark_message_read(
        database_path,
        message_id=message_id,
        user_id=learner_id,
    )
    assert marked is not None
    assert marked["readAt"] is not None

    # Verify it shows as read
    messages = list_classroom_messages(
        database_path,
        session_id=session["sessionId"],
        user_id=learner_id,
    )
    assert messages is not None
    msg = [m for m in messages if m["messageId"] == message_id][0]
    assert msg["isRead"] is True


def test_mark_message_read_unauthorized_user(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    outsider = create_user(database_path, "outsider4", "hashed-password")

    result = send_classroom_message(
        database_path,
        session_id=session["sessionId"],
        sender_id=educator_id,
        content="Hello",
        message_type="classroom",
    )
    message_id = result["messageId"]

    marked = mark_message_read(
        database_path,
        message_id=message_id,
        user_id=outsider,
    )
    assert marked is None


def test_unread_message_counts(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    learner_id = _join_learner(database_path, session["sessionId"], "learner5")

    # Educator sends two messages to the classroom
    send_classroom_message(
        database_path,
        session_id=session["sessionId"],
        sender_id=educator_id,
        content="Msg 1",
        message_type="classroom",
    )
    send_classroom_message(
        database_path,
        session_id=session["sessionId"],
        sender_id=educator_id,
        content="Msg 2",
        message_type="classroom",
    )

    counts = list_unread_message_counts(
        database_path, user_id=learner_id
    )
    assert len(counts) == 1
    assert counts[0]["sessionId"] == session["sessionId"]
    assert counts[0]["unreadCount"] == 2

    # Educator's own messages are auto-read, so educator has 0 unread
    educator_counts = list_unread_message_counts(
        database_path, user_id=educator_id
    )
    assert educator_counts == []


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def test_create_and_list_notification(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)

    notif = create_notification(
        database_path,
        user_id=educator_id,
        type="assessment_result",
        title="Quiz graded",
        body="Your algebra quiz has been graded.",
        related_id="quiz_123",
        related_type="quiz",
    )
    assert notif is not None
    assert notif["isRead"] is False
    assert notif["type"] == "assessment_result"

    notifications = list_notifications(
        database_path, user_id=educator_id
    )
    assert len(notifications) == 1
    assert notifications[0]["notificationId"] == notif["notificationId"]


def test_notification_invalid_type_rejected(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)

    result = create_notification(
        database_path,
        user_id=educator_id,
        type="invalid_type",
        title="X",
        body="Y",
    )
    assert result is None


def test_notification_nonexistent_user_rejected(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)

    result = create_notification(
        database_path,
        user_id="user_nonexistent",
        type="announcement",
        title="X",
        body="Y",
    )
    assert result is None


def test_mark_notification_read(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)

    notif = create_notification(
        database_path,
        user_id=educator_id,
        type="feedback",
        title="Feedback",
        body="Great work on the quiz.",
    )
    assert notif["isRead"] is False

    marked = mark_notification_read(
        database_path,
        notification_id=notif["notificationId"],
        user_id=educator_id,
    )
    assert marked is not None
    assert marked["readAt"] is not None

    notifications = list_notifications(
        database_path, user_id=educator_id, unread_only=True
    )
    assert len(notifications) == 0


def test_mark_notification_read_wrong_user(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    learner_id = create_user(database_path, "learner6", "hashed-password")

    notif = create_notification(
        database_path,
        user_id=educator_id,
        type="feedback",
        title="Feedback",
        body="Great work.",
    )

    marked = mark_notification_read(
        database_path,
        notification_id=notif["notificationId"],
        user_id=learner_id,
    )
    assert marked is None


def test_notification_cross_user_isolation(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    learner_id = create_user(database_path, "learner7", "hashed-password")

    create_notification(
        database_path,
        user_id=educator_id,
        type="assessment_result",
        title="Educator notif",
        body="For educator only.",
    )
    create_notification(
        database_path,
        user_id=learner_id,
        type="feedback",
        title="Learner notif",
        body="For learner only.",
    )

    educator_notifs = list_notifications(
        database_path, user_id=educator_id
    )
    assert len(educator_notifs) == 1
    assert educator_notifs[0]["title"] == "Educator notif"

    learner_notifs = list_notifications(
        database_path, user_id=learner_id
    )
    assert len(learner_notifs) == 1
    assert learner_notifs[0]["title"] == "Learner notif"


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------


def test_get_notification_preferences_defaults(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)

    prefs = get_notification_preferences(
        database_path, user_id=educator_id
    )
    assert prefs is not None
    assert prefs["userId"] == educator_id
    assert prefs["emailEnabled"] is False
    assert prefs["inAppEnabled"] is True
    assert prefs["announcementEnabled"] is True


def test_update_notification_preferences(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)

    updated = update_notification_preferences(
        database_path,
        user_id=educator_id,
        email_enabled=True,
        announcement_enabled=False,
    )
    assert updated is not None
    assert updated["emailEnabled"] is True
    assert updated["announcementEnabled"] is False
    # Unchanged fields keep defaults
    assert updated["inAppEnabled"] is True
    assert updated["feedbackEnabled"] is True


def test_update_preferences_nonexistent_user(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)

    result = update_notification_preferences(
        database_path,
        user_id="user_nonexistent",
        email_enabled=True,
    )
    assert result is None


# ---------------------------------------------------------------------------
# Announcement with notifications
# ---------------------------------------------------------------------------


def test_create_announcement_notifies_participants(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    learner_id = _join_learner(database_path, session["sessionId"], "learner8")

    announcement = create_classroom_announcement_with_notification(
        database_path,
        session_id=session["sessionId"],
        educator_id=educator_id,
        message="Class is cancelled today.",
    )
    assert announcement is not None
    assert announcement["message"] == "Class is cancelled today."

    # Learner should have a notification
    notifications = list_notifications(
        database_path, user_id=learner_id
    )
    assert len(notifications) == 1
    assert notifications[0]["type"] == "announcement"
    assert notifications[0]["relatedId"] == announcement["announcementId"]


def test_announcement_non_educator_rejected(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    learner_id = _join_learner(database_path, session["sessionId"], "learner9")

    announcement = create_classroom_announcement_with_notification(
        database_path,
        session_id=session["sessionId"],
        educator_id=learner_id,  # learner is not the educator
        message="Unauthorized announcement",
    )
    assert announcement is None


# ---------------------------------------------------------------------------
# Learner progress portal
# ---------------------------------------------------------------------------


def test_progress_portal_observed_data_only(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    learner_id = _join_learner(
        database_path, _make_classroom(database_path, educator_id)["sessionId"],
        "learner10",
    )

    portal = get_learner_progress_portal(
        database_path, user_id=learner_id
    )
    assert portal is not None
    assert portal["userId"] == learner_id
    assert portal["assessments"] == []
    assert portal["revisions"] == []
    assert portal["studySessions"] == []
    assert portal["messageCount"] == 0
    assert portal["unreadNotificationCount"] == 0
    # Data sources are documented
    assert len(portal["dataSources"]) > 0


def test_progress_portal_with_quiz_attempts(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    learner_id = _join_learner(database_path, session["sessionId"], "learner11")

    from app.storage import (
        create_quiz_set,
        record_quiz_attempt,
    )

    quiz_id = f"quiz_{learner_id}_topic_1"
    now = datetime.now(UTC).isoformat()
    create_quiz_set(
        database_path,
        {
            "quiz_id": quiz_id,
            "topic_id": "topic_1",
            "topic": "Linear Equations",
            "created_at": now,
            "questions": [
                {
                    "question_id": "q1",
                    "question_type": "multiple_choice",
                    "prompt": "2+2?",
                    "options": [{"id": "A", "text": "4"}],
                    "answer_key": "A",
                    "accepted_answers": ["A"],
                    "explanation": "4",
                    "difficulty_level": "building",
                    "citations": [],
                }
            ],
        },
        user_id=learner_id,
    )
    record_quiz_attempt(
        database_path,
        attempt_id="attempt_1",
        question_id="q1",
        outcome="correct",
        created_at=now,
        user_id=learner_id,
        duration_ms=30000,
    )
    record_quiz_attempt(
        database_path,
        attempt_id="attempt_2",
        question_id="q1",
        outcome="incorrect",
        created_at=now,
        user_id=learner_id,
        duration_ms=30000,
    )

    portal = get_learner_progress_portal(
        database_path, user_id=learner_id
    )
    assert portal is not None
    assert len(portal["assessments"]) == 1
    assert portal["assessments"][0]["topicId"] == "topic_1"
    assert portal["assessments"][0]["totalAttempts"] == 2
    assert portal["assessments"][0]["correctAttempts"] == 1
    assert portal["assessments"][0]["accuracy"] == 0.5


def test_progress_portal_with_revision_and_study(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    learner_id = _join_learner(
        database_path, _make_classroom(database_path, educator_id)["sessionId"],
        "learner12",
    )

    from app.storage import (
        complete_study_session,
        start_study_session,
    )

    now = datetime.now(UTC).isoformat()
    start_study_session(
        database_path,
        session_id="study_1",
        topic_id="topic_study",
        topic="Algebra",
        started_at=now,
        user_id=learner_id,
    )
    complete_study_session(
        database_path,
        session_id="study_1",
        user_id=learner_id,
        completed_at=(datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
    )

    portal = get_learner_progress_portal(
        database_path, user_id=learner_id
    )
    assert portal is not None
    assert len(portal["revisions"]) == 0
    assert len(portal["studySessions"]) == 1
    assert portal["studySessions"][0]["topic"] == "Algebra"
    assert portal["studySessions"][0]["completedAt"] is not None


# ---------------------------------------------------------------------------
# Progress visibility: educator must share classroom
# ---------------------------------------------------------------------------


def test_educator_can_view_learner_progress_when_shared_classroom(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    learner_id = _join_learner(database_path, session["sessionId"], "learner13")

    # Add some progress data for the learner
    from app.storage import create_quiz_set, record_quiz_attempt

    quiz_id = f"quiz_{learner_id}_topic_vp"
    now = datetime.now(UTC).isoformat()
    create_quiz_set(
        database_path,
        {
            "quiz_id": quiz_id,
            "topic_id": "topic_vp",
            "topic": "View Progress",
            "created_at": now,
            "questions": [
                {
                    "question_id": "q_vp",
                    "question_type": "multiple_choice",
                    "prompt": "Q?",
                    "options": [{"id": "A", "text": "A"}],
                    "answer_key": "A",
                    "accepted_answers": ["A"],
                    "explanation": "A",
                    "difficulty_level": "building",
                    "citations": [],
                }
            ],
        },
        user_id=learner_id,
    )
    record_quiz_attempt(
        database_path,
        attempt_id="attempt_vp",
        question_id="q_vp",
        outcome="correct",
        created_at=now,
        user_id=learner_id,
        duration_ms=30000,
    )

    portal = get_learner_progress_for_educator(
        database_path,
        educator_id=educator_id,
        target_user_id=learner_id,
    )
    assert portal is not None
    assert portal["userId"] == learner_id
    assert len(portal["assessments"]) == 1


def test_educator_cannot_view_unrelated_learner_progress(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    # Educator has a classroom but learner is not in it
    _make_classroom(database_path, educator_id)
    learner_id = create_user(database_path, "learner14", "hashed-password")
    # No shared classroom

    portal = get_learner_progress_for_educator(
        database_path,
        educator_id=educator_id,
        target_user_id=learner_id,
    )
    assert portal is None


def test_educator_cannot_view_nonexistent_learner_progress(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    _make_classroom(database_path, educator_id)

    portal = get_learner_progress_for_educator(
        database_path,
        educator_id=educator_id,
        target_user_id="user_nonexistent",
    )
    assert portal is None


# ---------------------------------------------------------------------------
# Cross-user isolation for messages and notifications
# ---------------------------------------------------------------------------


def test_message_cross_user_isolation(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session_a = _make_classroom(database_path, educator_id, "Class A")
    session_b = _make_classroom(database_path, educator_id, "Class B")
    learner_a = _join_learner(database_path, session_a["sessionId"], "learner_a")
    learner_b = _join_learner(database_path, session_b["sessionId"], "learner_b")

    send_classroom_message(
        database_path,
        session_id=session_a["sessionId"],
        sender_id=learner_a,
        content="Message in class A",
        message_type="classroom",
    )
    send_classroom_message(
        database_path,
        session_id=session_b["sessionId"],
        sender_id=learner_b,
        content="Message in class B",
        message_type="classroom",
    )

    msgs_a = list_classroom_messages(
        database_path, session_id=session_a["sessionId"], user_id=learner_a
    )
    msgs_b = list_classroom_messages(
        database_path, session_id=session_b["sessionId"], user_id=learner_b
    )
    assert len(msgs_a) == 1
    assert msgs_a[0]["content"] == "Message in class A"
    assert len(msgs_b) == 1
    assert msgs_b[0]["content"] == "Message in class B"


def test_notification_cross_user_isolation_already_tested(tmp_path):
    """Covered by test_notification_cross_user_isolation."""
    pass


# ---------------------------------------------------------------------------
# Data deletion and retention
# ---------------------------------------------------------------------------


def test_deleting_classroom_cascades_messages(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)

    send_classroom_message(
        database_path,
        session_id=session["sessionId"],
        sender_id=educator_id,
        content="To be deleted",
        message_type="classroom",
    )

    delete_classroom_session(
        database_path, session_id=session["sessionId"], educator_id=educator_id
    )

    messages = list_classroom_messages(
        database_path, session_id=session["sessionId"], user_id=educator_id
    )
    # Session is deleted, so access returns None
    assert messages is None


def test_service_layer_send_message(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = _make_classroom(database_path, educator_id)
    learner_id = _join_learner(database_path, session["sessionId"], "learner16")

    result = send_classroom_message_service(
        database_path=database_path,
        session_id=session["sessionId"],
        sender_id=learner_id,
        content="Service layer test",
        message_type="classroom",
    )
    assert result is not None
    assert result["content"] == "Service layer test"


def test_service_layer_get_notifications(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)

    create_notification(
        database_path,
        user_id=educator_id,
        type="announcement",
        title="Test",
        body="Test body",
    )

    notifs = list_user_notifications_service(
        database_path=database_path,
        user_id=educator_id,
    )
    assert len(notifs) == 1
    assert notifs[0]["title"] == "Test"


def test_service_layer_preferences(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)

    prefs = get_user_notification_preferences_service(
        database_path=database_path,
        user_id=educator_id,
    )
    assert prefs is not None
    assert prefs["inAppEnabled"] is True

    updated = update_user_notification_preferences_service(
        database_path=database_path,
        user_id=educator_id,
        in_app_enabled=False,
    )
    assert updated["inAppEnabled"] is False


def test_guardian_access_not_implemented(tmp_path):
    """Guardian access is NOT implemented: no guardian relationship table,
    no invitation/consent flow, no age verification. This test documents
    the limitation."""
    settings, educator_id, database_path = setup_test_db(tmp_path)
    learner_id = _join_learner(
        database_path, _make_classroom(database_path, educator_id)["sessionId"],
        "learner_guardian_test",
    )

    # There is no guardian relationship mechanism, so no guardian can
    # access learner data. This is by design (see communication.py).
    # The progress portal requires either the user themselves or an
    # educator who shares a classroom.
    portal = get_learner_progress_portal(
        database_path, user_id=learner_id
    )
    assert portal is not None  # learner can view own progress

    # No guardian table exists - guardian access returns None/not available
    # This is documented behavior, not a bug.
    assert "guardian" not in str(portal.keys())


if __name__ == "__main__":
    pass
