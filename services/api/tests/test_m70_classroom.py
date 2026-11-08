"""M70: Live Interactive Classroom & Virtual Learning Environment tests.

Tests cover:

* Session lifecycle (create, list, get, update, delete)
* Participant join/leave and authorization
* Presence heartbeat and listing
* Learner questions and educator answers
* Polls (create, list, respond, results)
* Announcements (create, list)
* Activity state (update, get)
* Session recovery after disconnect
* User isolation (cross-educator/learner data leakage)
* Empty state handling
* Invalid input validation
* Status transitions (scheduled -> active -> paused -> ended)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import Settings
from app.storage import (
    answer_classroom_question,
    create_classroom_announcement,
    create_classroom_poll,
    create_classroom_session,
    create_user,
    delete_classroom_session,
    get_classroom_activity_state,
    get_classroom_poll_results,
    get_classroom_session,
    initialize_storage,
    join_classroom_session,
    leave_classroom_session,
    list_classroom_announcements,
    list_classroom_participants,
    list_classroom_polls,
    list_classroom_presence,
    list_classroom_questions,
    list_classroom_sessions,
    record_classroom_heartbeat,
    record_classroom_question,
    recover_classroom_session,
    respond_to_classroom_poll,
    update_classroom_activity_state,
    update_classroom_session,
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
    user_id = create_user(settings.database_path, "classtest", "hashed-password", is_admin=False)
    return settings, user_id, settings.database_path


# ---------------------------------------------------------------------------
# Classroom session CRUD tests
# ---------------------------------------------------------------------------


def test_create_classroom_session(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path,
        educator_id=user_id,
        lesson_plan_id=None,
        title="Algebra Live Class",
        subject="Mathematics",
        topic="Linear Equations",
        status="scheduled",
    )

    assert session is not None
    assert session["sessionId"].startswith("classroom_")
    assert session["educatorId"] == user_id
    assert session["title"] == "Algebra Live Class"
    assert session["subject"] == "Mathematics"
    assert session["topic"] == "Linear Equations"
    assert session["status"] == "scheduled"
    assert session["startedAt"] is None
    assert session["endedAt"] is None


def test_create_classroom_session_invalid_inputs(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    # Empty title
    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="", subject="Math", topic="Algebra", status="scheduled",
    )
    assert session is None

    # Invalid status
    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Class", subject="Math", topic="Algebra", status="invalid_status",
    )
    assert session is None


def test_get_classroom_session(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Algebra", subject="Math", topic="Linear Equations",
        status="scheduled",
    )

    fetched = get_classroom_session(
        database_path, session_id=session["sessionId"], user_id=user_id
    )
    assert fetched is not None
    assert fetched["sessionId"] == session["sessionId"]
    assert fetched["title"] == "Algebra"


def test_list_classroom_sessions(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Session 1", subject="Math", topic="Algebra", status="scheduled",
    )
    create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Session 2", subject="Math", topic="Geometry", status="active",
    )

    sessions = list_classroom_sessions(database_path, educator_id=user_id)
    assert len(sessions) == 2
    titles = {s["title"] for s in sessions}
    assert titles == {"Session 1", "Session 2"}


def test_list_classroom_sessions_filter_status(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Scheduled", subject="Math", topic="Algebra", status="scheduled",
    )
    create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Active", subject="Math", topic="Geometry", status="active",
    )

    scheduled = list_classroom_sessions(database_path, educator_id=user_id, status="scheduled")
    assert len(scheduled) == 1
    assert scheduled[0]["title"] == "Scheduled"


def test_update_classroom_session(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Old Title", subject="Math", topic="Algebra",
        status="scheduled",
    )

    updated = update_classroom_session(
        database_path,
        session_id=session["sessionId"],
        educator_id=user_id,
        title="New Title",
        status="active",
        started_at=datetime.now(UTC).isoformat(),
    )
    assert updated is not None
    assert updated["title"] == "New Title"
    assert updated["status"] == "active"
    assert updated["subject"] == "Math"  # unchanged


def test_delete_classroom_session(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="To Delete", subject="Math", topic="Algebra",
        status="scheduled",
    )

    deleted = delete_classroom_session(
        database_path, session_id=session["sessionId"], educator_id=user_id
    )
    assert deleted is True

    fetched = get_classroom_session(
        database_path, session_id=session["sessionId"], user_id=user_id
    )
    assert fetched is None


# ---------------------------------------------------------------------------
# Classroom participant tests
# ---------------------------------------------------------------------------


def test_join_and_leave_classroom_session(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Class", subject="Math", topic="Algebra",
        status="scheduled",
    )

    # Learner joins
    participant = join_classroom_session(
        database_path,
        session_id=session["sessionId"],
        user_id=user_id,
        role="learner",
    )
    assert participant is not None
    assert participant["participantId"].startswith("participant_")
    assert participant["userId"] == user_id
    assert participant["role"] == "learner"
    assert participant["isConnected"] is True

    # List participants
    participants = list_classroom_participants(
        database_path, session_id=session["sessionId"], user_id=user_id
    )
    assert participants is not None
    assert len(participants) == 1

    # Leave
    left = leave_classroom_session(
        database_path, session_id=session["sessionId"], user_id=user_id
    )
    assert left is True

    # Verify no longer connected
    participants = list_classroom_participants(
        database_path, session_id=session["sessionId"], user_id=user_id
    )
    assert participants[0]["isConnected"] is False


def test_join_classroom_session_invalid_status(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Ended Class", subject="Math", topic="Algebra",
        status="ended",
    )

    participant = join_classroom_session(
        database_path,
        session_id=session["sessionId"],
        user_id=user_id,
        role="learner",
    )
    assert participant is None


# ---------------------------------------------------------------------------
# Classroom presence tests
# ---------------------------------------------------------------------------


def test_record_and_list_classroom_heartbeat(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Class", subject="Math", topic="Algebra",
        status="active",
    )
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=user_id,
    )

    heartbeat = record_classroom_heartbeat(
        database_path, session_id=session["sessionId"], user_id=user_id,
    )
    assert heartbeat is not None
    assert heartbeat["userId"] == user_id
    assert heartbeat["isOnline"] is True

    presence = list_classroom_presence(
        database_path, session_id=session["sessionId"], user_id=user_id
    )
    assert presence is not None
    assert len(presence) == 1
    assert presence[0]["userId"] == user_id


# ---------------------------------------------------------------------------
# Classroom question tests
# ---------------------------------------------------------------------------


def test_record_and_list_classroom_questions(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Class", subject="Math", topic="Algebra",
        status="active",
    )
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=user_id,
    )

    question = record_classroom_question(
        database_path,
        session_id=session["sessionId"],
        user_id=user_id,
        question_text="How do I solve for x?",
    )
    assert question is not None
    assert question["questionId"].startswith("classroom_question_")
    assert question["questionText"] == "How do I solve for x?"
    assert question["isAnswered"] is False

    questions = list_classroom_questions(
        database_path, session_id=session["sessionId"], user_id=user_id
    )
    assert questions is not None
    assert len(questions) == 1


def test_answer_classroom_question(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Class", subject="Math", topic="Algebra",
        status="active",
    )
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=user_id,
    )

    question = record_classroom_question(
        database_path,
        session_id=session["sessionId"],
        user_id=user_id,
        question_text="How do I solve for x?",
    )

    answered = answer_classroom_question(
        database_path,
        session_id=session["sessionId"],
        question_id=question["questionId"],
        educator_id=user_id,
        answer_text="Add 5 to both sides.",
    )
    assert answered is not None
    assert answered["answerText"] == "Add 5 to both sides."
    assert answered["answeredAt"] is not None


# ---------------------------------------------------------------------------
# Classroom poll tests
# ---------------------------------------------------------------------------


def test_create_and_list_classroom_polls(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Class", subject="Math", topic="Algebra",
        status="active",
    )

    poll = create_classroom_poll(
        database_path,
        session_id=session["sessionId"],
        educator_id=user_id,
        question="What is 2+2?",
        options=["3", "4", "5"],
        correct_option_index=1,
    )
    assert poll is not None
    assert poll["pollId"].startswith("classroom_poll_")
    assert poll["question"] == "What is 2+2?"
    assert poll["options"] == ["3", "4", "5"]
    assert poll["correctOptionIndex"] == 1
    assert poll["isOpen"] is True

    polls = list_classroom_polls(
        database_path, session_id=session["sessionId"], user_id=user_id
    )
    assert polls is not None
    assert len(polls) == 1


def test_respond_to_classroom_poll(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Class", subject="Math", topic="Algebra",
        status="active",
    )
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=user_id,
    )

    poll = create_classroom_poll(
        database_path,
        session_id=session["sessionId"],
        educator_id=user_id,
        question="What is 2+2?",
        options=["3", "4", "5"],
        correct_option_index=1,
    )

    response = respond_to_classroom_poll(
        database_path,
        poll_id=poll["pollId"],
        user_id=user_id,
        option_index=1,
    )
    assert response is not None
    assert response["pollId"] == poll["pollId"]
    assert response["userId"] == user_id
    assert response["optionIndex"] == 1


def test_get_classroom_poll_results(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Class", subject="Math", topic="Algebra",
        status="active",
    )
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=user_id,
    )

    poll = create_classroom_poll(
        database_path,
        session_id=session["sessionId"],
        educator_id=user_id,
        question="What is 2+2?",
        options=["3", "4", "5"],
        correct_option_index=1,
    )

    respond_to_classroom_poll(
        database_path,
        poll_id=poll["pollId"],
        user_id=user_id,
        option_index=1,
    )

    results = get_classroom_poll_results(
        database_path, poll_id=poll["pollId"], user_id=user_id
    )
    assert results is not None
    assert results["pollId"] == poll["pollId"]
    assert results["totalResponses"] == 1
    assert results["optionCounts"] == [0, 1, 0]


# ---------------------------------------------------------------------------
# Classroom announcement tests
# ---------------------------------------------------------------------------


def test_create_and_list_classroom_announcements(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Class", subject="Math", topic="Algebra",
        status="active",
    )

    announcement = create_classroom_announcement(
        database_path,
        session_id=session["sessionId"],
        educator_id=user_id,
        message="Please open your textbooks to page 42.",
    )
    assert announcement is not None
    assert announcement["announcementId"].startswith("announcement_")
    assert announcement["message"] == "Please open your textbooks to page 42."

    announcements = list_classroom_announcements(
        database_path, session_id=session["sessionId"], user_id=user_id
    )
    assert announcements is not None
    assert len(announcements) == 1


# ---------------------------------------------------------------------------
# Classroom activity state tests
# ---------------------------------------------------------------------------


def test_update_and_get_classroom_activity_state(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Class", subject="Math", topic="Algebra",
        status="active",
    )

    state = update_classroom_activity_state(
        database_path,
        session_id=session["sessionId"],
        educator_id=user_id,
        state_type="slide",
        state_data={"slideNumber": 5, "title": "Linear Equations"},
    )
    assert state is not None
    assert state["stateId"].startswith("activity_state_")
    assert state["stateType"] == "slide"
    assert state["stateData"]["slideNumber"] == 5

    fetched = get_classroom_activity_state(
        database_path, session_id=session["sessionId"], user_id=user_id
    )
    assert fetched is not None
    assert fetched["stateType"] == "slide"
    assert fetched["stateData"]["slideNumber"] == 5


# ---------------------------------------------------------------------------
# Classroom session recovery tests
# ---------------------------------------------------------------------------


def test_recover_classroom_session(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    session = create_classroom_session(
        database_path, educator_id=user_id, lesson_plan_id=None,
        title="Class", subject="Math", topic="Algebra",
        status="active",
    )
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=user_id,
    )
    record_classroom_heartbeat(
        database_path, session_id=session["sessionId"], user_id=user_id,
    )
    record_classroom_question(
        database_path,
        session_id=session["sessionId"],
        user_id=user_id,
        question_text="Help!",
    )
    create_classroom_announcement(
        database_path,
        session_id=session["sessionId"],
        educator_id=user_id,
        message="Welcome to class!",
    )

    recovery = recover_classroom_session(
        database_path, session_id=session["sessionId"], user_id=user_id,
    )
    assert recovery is not None
    assert recovery["session"]["sessionId"] == session["sessionId"]
    assert len(recovery["participants"]) == 1
    assert len(recovery["presence"]) == 1
    assert len(recovery["questions"]) == 1
    assert len(recovery["announcements"]) == 1


# ---------------------------------------------------------------------------
# User isolation tests
# ---------------------------------------------------------------------------


def test_user_isolation_in_classroom_sessions(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "classtest_b", "hashed-password", is_admin=False)

    session_a = create_classroom_session(
        database_path, educator_id=user_id_a, lesson_plan_id=None,
        title="Class A", subject="Math", topic="Algebra",
        status="scheduled",
    )

    sessions_b = list_classroom_sessions(database_path, user_id=user_id_b)
    assert len(sessions_b) == 0

    sessions_a = list_classroom_sessions(database_path, educator_id=user_id_a)
    assert len(sessions_a) == 1

    fetched = get_classroom_session(
        database_path, session_id=session_a["sessionId"], user_id=user_id_b
    )
    assert fetched is None


# ---------------------------------------------------------------------------
# Empty state tests
# ---------------------------------------------------------------------------


def test_empty_classroom_session_state(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    sessions = list_classroom_sessions(database_path, user_id=user_id)
    assert sessions == []

    fetched = get_classroom_session(
        database_path, session_id="nonexistent", user_id=user_id
    )
    assert fetched is None


if __name__ == "__main__":
    pass
