"""M69: AI Lesson Planner & Teaching Studio tests.

Tests cover:

* Lesson plan CRUD (create, list, get, update, delete)
* Lesson plan sections (create, list)
* Lesson sessions (create, list, get, update)
* Lesson attendance (record, list)
* Lesson learner questions (record, list)
* User isolation (cross-educator data leakage)
* Empty state handling
* Invalid input validation
* Status transitions (draft -> published)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import Settings
from app.storage import (
    create_competency,
    create_lesson_plan,
    create_lesson_plan_section,
    create_lesson_session,
    create_user,
    delete_lesson_plan,
    get_competency_mastery,
    get_lesson_plan,
    get_lesson_session,
    initialize_storage,
    list_lesson_attendance,
    list_lesson_plan_sections,
    list_lesson_plans,
    list_lesson_questions,
    list_lesson_sessions,
    record_lesson_attendance,
    record_lesson_question,
    update_competency_mastery,
    update_lesson_plan,
    update_lesson_session,
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
    user_id = create_user(settings.database_path, "lessontest", "hashed-password", is_admin=False)
    return settings, user_id, settings.database_path


# ---------------------------------------------------------------------------
# Lesson plan CRUD tests
# ---------------------------------------------------------------------------


def test_create_lesson_plan(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plan = create_lesson_plan(
        database_path,
        user_id=user_id,
        classroom_id=None,
        title="Introduction to Algebra",
        subject="Mathematics",
        topic="Linear Equations",
        student_level="foundational",
        duration_minutes=45,
        status="draft",
        learning_objectives=["Solve linear equations", "Graph linear functions"],
        prerequisite_concepts=["Basic arithmetic", "Variables"],
        source_material_refs=[{"documentId": "doc_1", "displayFilename": "algebra.pdf"}],
    )

    assert plan is not None
    assert plan["lessonPlanId"].startswith("lesson_")
    assert plan["userId"] == user_id
    assert plan["title"] == "Introduction to Algebra"
    assert plan["subject"] == "Mathematics"
    assert plan["topic"] == "Linear Equations"
    assert plan["studentLevel"] == "foundational"
    assert plan["durationMinutes"] == 45
    assert plan["status"] == "draft"
    assert plan["learningObjectives"] == ["Solve linear equations", "Graph linear functions"]
    assert plan["prerequisiteConcepts"] == ["Basic arithmetic", "Variables"]
    assert plan["publishedAt"] is None


def test_create_lesson_plan_invalid_inputs(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    # Empty title
    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="", subject="Math", topic="Algebra", student_level=None,
        duration_minutes=30, status="draft",
    )
    assert plan is None

    # Invalid duration
    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Plan", subject="Math", topic="Algebra", student_level=None,
        duration_minutes=0, status="draft",
    )
    assert plan is None

    # Invalid status
    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Plan", subject="Math", topic="Algebra", student_level=None,
        duration_minutes=30, status="invalid_status",
    )
    assert plan is None


def test_get_lesson_plan(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Algebra", subject="Math", topic="Linear Equations",
        student_level="foundational", duration_minutes=45, status="draft",
    )

    fetched = get_lesson_plan(
        database_path, lesson_plan_id=plan["lessonPlanId"], user_id=user_id
    )
    assert fetched is not None
    assert fetched["lessonPlanId"] == plan["lessonPlanId"]
    assert fetched["title"] == "Algebra"


def test_get_lesson_plan_wrong_user(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "lessontest_b", "hashed-password", is_admin=False)

    plan = create_lesson_plan(
        database_path, user_id=user_id_a, classroom_id=None,
        title="Algebra", subject="Math", topic="Linear Equations",
        student_level=None, duration_minutes=30, status="draft",
    )

    fetched = get_lesson_plan(
        database_path, lesson_plan_id=plan["lessonPlanId"], user_id=user_id_b
    )
    assert fetched is None


def test_list_lesson_plans(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Plan 1", subject="Math", topic="Algebra",
        student_level=None, duration_minutes=30, status="draft",
    )
    create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Plan 2", subject="Science", topic="Biology",
        student_level=None, duration_minutes=60, status="published",
    )

    plans = list_lesson_plans(database_path, user_id=user_id)
    assert len(plans) == 2
    titles = {p["title"] for p in plans}
    assert titles == {"Plan 1", "Plan 2"}


def test_list_lesson_plans_filter_status(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Draft Plan", subject="Math", topic="Algebra",
        student_level=None, duration_minutes=30, status="draft",
    )
    create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Published Plan", subject="Math", topic="Geometry",
        student_level=None, duration_minutes=45, status="published",
    )

    drafts = list_lesson_plans(database_path, user_id=user_id, status="draft")
    assert len(drafts) == 1
    assert drafts[0]["title"] == "Draft Plan"


def test_update_lesson_plan(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Old Title", subject="Math", topic="Algebra",
        student_level=None, duration_minutes=30, status="draft",
    )

    updated = update_lesson_plan(
        database_path,
        lesson_plan_id=plan["lessonPlanId"],
        user_id=user_id,
        title="New Title",
        status="published",
        duration_minutes=60,
    )
    assert updated is not None
    assert updated["title"] == "New Title"
    assert updated["status"] == "published"
    assert updated["durationMinutes"] == 60
    assert updated["subject"] == "Math"  # unchanged
    assert updated["publishedAt"] is not None


def test_update_lesson_plan_invalid_status(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Plan", subject="Math", topic="Algebra",
        student_level=None, duration_minutes=30, status="draft",
    )

    updated = update_lesson_plan(
        database_path,
        lesson_plan_id=plan["lessonPlanId"],
        user_id=user_id,
        status="invalid_status",
    )
    assert updated is None


def test_delete_lesson_plan(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="To Delete", subject="Math", topic="Algebra",
        student_level=None, duration_minutes=30, status="draft",
    )

    deleted = delete_lesson_plan(
        database_path, lesson_plan_id=plan["lessonPlanId"], user_id=user_id
    )
    assert deleted is True

    fetched = get_lesson_plan(
        database_path, lesson_plan_id=plan["lessonPlanId"], user_id=user_id
    )
    assert fetched is None


def test_delete_lesson_plan_wrong_user(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "lessontest_b", "hashed-password", is_admin=False)

    plan = create_lesson_plan(
        database_path, user_id=user_id_a, classroom_id=None,
        title="Plan", subject="Math", topic="Algebra",
        student_level=None, duration_minutes=30, status="draft",
    )

    deleted = delete_lesson_plan(
        database_path, lesson_plan_id=plan["lessonPlanId"], user_id=user_id_b
    )
    assert deleted is False


# ---------------------------------------------------------------------------
# Lesson plan section tests
# ---------------------------------------------------------------------------


def test_create_and_list_lesson_plan_sections(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Algebra", subject="Math", topic="Linear Equations",
        student_level="foundational", duration_minutes=45, status="draft",
    )

    section = create_lesson_plan_section(
        database_path,
        lesson_plan_id=plan["lessonPlanId"],
        user_id=user_id,
        section_type="concept_explanation",
        section_title="What is a linear equation?",
        content="A linear equation is an equation between two variables...",
        citations=[{"citationId": "cite_1", "retrieval": {}}],
        position=0,
        order_index=0,
    )
    assert section is not None
    assert section["sectionId"].startswith("lesson_section_")
    assert section["sectionType"] == "concept_explanation"
    assert section["sectionTitle"] == "What is a linear equation?"

    sections = list_lesson_plan_sections(
        database_path, lesson_plan_id=plan["lessonPlanId"], user_id=user_id
    )
    assert sections is not None
    assert len(sections) == 1


def test_create_section_invalid_type(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Plan", subject="Math", topic="Algebra",
        student_level=None, duration_minutes=30, status="draft",
    )

    section = create_lesson_plan_section(
        database_path,
        lesson_plan_id=plan["lessonPlanId"],
        user_id=user_id,
        section_type="invalid_type",
        section_title="Section",
        content="Content",
    )
    assert section is None


def test_create_section_empty_content(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Plan", subject="Math", topic="Algebra",
        student_level=None, duration_minutes=30, status="draft",
    )

    section = create_lesson_plan_section(
        database_path,
        lesson_plan_id=plan["lessonPlanId"],
        user_id=user_id,
        section_type="introduction",
        section_title="Intro",
        content="",
    )
    assert section is None


# ---------------------------------------------------------------------------
# Lesson session tests
# ---------------------------------------------------------------------------


def test_create_and_list_lesson_sessions(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Algebra", subject="Math", topic="Linear Equations",
        student_level="foundational", duration_minutes=45, status="published",
    )

    session = create_lesson_session(
        database_path,
        lesson_plan_id=plan["lessonPlanId"],
        user_id=user_id,
        classroom_id=None,
        title="Algebra Session 1",
        scheduled_at=datetime.now(UTC).isoformat(),
        status="scheduled",
    )
    assert session is not None
    assert session["sessionId"].startswith("lesson_session_")
    assert session["title"] == "Algebra Session 1"
    assert session["status"] == "scheduled"

    sessions = list_lesson_sessions(
        database_path, lesson_plan_id=plan["lessonPlanId"], user_id=user_id
    )
    assert len(sessions) == 1


def test_get_and_update_lesson_session(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Algebra", subject="Math", topic="Linear Equations",
        student_level=None, duration_minutes=30, status="published",
    )
    session = create_lesson_session(
        database_path,
        lesson_plan_id=plan["lessonPlanId"],
        user_id=user_id,
        classroom_id=None,
        title="Session",
        scheduled_at=None,
        status="scheduled",
    )

    fetched = get_lesson_session(
        database_path, session_id=session["sessionId"], user_id=user_id
    )
    assert fetched is not None
    assert fetched["sessionId"] == session["sessionId"]

    updated = update_lesson_session(
        database_path,
        session_id=session["sessionId"],
        user_id=user_id,
        status="in_progress",
        started_at=datetime.now(UTC).isoformat(),
    )
    assert updated is not None
    assert updated["status"] == "in_progress"
    assert updated["startedAt"] is not None


# ---------------------------------------------------------------------------
# Lesson attendance tests
# ---------------------------------------------------------------------------


def test_record_and_list_lesson_attendance(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Algebra", subject="Math", topic="Linear Equations",
        student_level=None, duration_minutes=30, status="published",
    )
    session = create_lesson_session(
        database_path,
        lesson_plan_id=plan["lessonPlanId"],
        user_id=user_id,
        classroom_id=None,
        title="Session",
        scheduled_at=None,
        status="in_progress",
    )

    attendance = record_lesson_attendance(
        database_path,
        session_id=session["sessionId"],
        user_id=user_id,
        joined_at=datetime.now(UTC).isoformat(),
        participation_events=[{"type": "question", "timestamp": "t1"}],
    )
    assert attendance is not None
    assert attendance["attendanceId"].startswith("lesson_attendance_")
    assert attendance["participationEvents"] == [{"type": "question", "timestamp": "t1"}]

    attendance_list = list_lesson_attendance(
        database_path, session_id=session["sessionId"], user_id=user_id
    )
    assert attendance_list is not None
    assert len(attendance_list) == 1


# ---------------------------------------------------------------------------
# Lesson questions tests
# ---------------------------------------------------------------------------


def test_record_and_list_lesson_questions(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Algebra", subject="Math", topic="Linear Equations",
        student_level=None, duration_minutes=30, status="published",
    )
    session = create_lesson_session(
        database_path,
        lesson_plan_id=plan["lessonPlanId"],
        user_id=user_id,
        classroom_id=None,
        title="Session",
        scheduled_at=None,
        status="in_progress",
    )

    question = record_lesson_question(
        database_path,
        session_id=session["sessionId"],
        user_id=user_id,
        question_text="How do I solve for x?",
        answered_at=None,
        answer_text=None,
    )
    assert question is not None
    assert question["questionId"].startswith("lesson_question_")
    assert question["questionText"] == "How do I solve for x?"

    questions = list_lesson_questions(
        database_path, session_id=session["sessionId"], user_id=user_id
    )
    assert questions is not None
    assert len(questions) == 1


def test_record_question_empty_text(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plan = create_lesson_plan(
        database_path, user_id=user_id, classroom_id=None,
        title="Plan", subject="Math", topic="Algebra",
        student_level=None, duration_minutes=30, status="published",
    )
    session = create_lesson_session(
        database_path,
        lesson_plan_id=plan["lessonPlanId"],
        user_id=user_id,
        classroom_id=None,
        title="Session",
        scheduled_at=None,
        status="in_progress",
    )

    question = record_lesson_question(
        database_path,
        session_id=session["sessionId"],
        user_id=user_id,
        question_text="   ",
    )
    assert question is None


# ---------------------------------------------------------------------------
# User isolation tests
# ---------------------------------------------------------------------------


def test_user_isolation_in_lesson_plans(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "lessontest_b", "hashed-password", is_admin=False)

    plan_a = create_lesson_plan(
        database_path, user_id=user_id_a, classroom_id=None,
        title="Plan A", subject="Math", topic="Algebra",
        student_level=None, duration_minutes=30, status="draft",
    )

    plans_b = list_lesson_plans(database_path, user_id=user_id_b)
    assert len(plans_b) == 0

    plans_a = list_lesson_plans(database_path, user_id=user_id_a)
    assert len(plans_a) == 1

    fetched = get_lesson_plan(
        database_path, lesson_plan_id=plan_a["lessonPlanId"], user_id=user_id_b
    )
    assert fetched is None


def test_user_isolation_in_lesson_sessions(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "lessontest_b", "hashed-password", is_admin=False)

    plan_a = create_lesson_plan(
        database_path, user_id=user_id_a, classroom_id=None,
        title="Plan A", subject="Math", topic="Algebra",
        student_level=None, duration_minutes=30, status="published",
    )
    session_a = create_lesson_session(
        database_path,
        lesson_plan_id=plan_a["lessonPlanId"],
        user_id=user_id_a,
        classroom_id=None,
        title="Session A",
        scheduled_at=None,
        status="scheduled",
    )

    sessions_b = list_lesson_sessions(database_path, user_id=user_id_b)
    assert len(sessions_b) == 0

    sessions_a = list_lesson_sessions(database_path, user_id=user_id_a)
    assert len(sessions_a) == 1

    fetched = get_lesson_session(
        database_path, session_id=session_a["sessionId"], user_id=user_id_b
    )
    assert fetched is None


# ---------------------------------------------------------------------------
# Empty state tests
# ---------------------------------------------------------------------------


def test_empty_lesson_plan_state(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    plans = list_lesson_plans(database_path, user_id=user_id)
    assert plans == []

    fetched = get_lesson_plan(database_path, lesson_plan_id="nonexistent", user_id=user_id)
    assert fetched is None


def test_empty_lesson_session_state(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    sessions = list_lesson_sessions(database_path, user_id=user_id)
    assert sessions == []

    fetched = get_lesson_session(database_path, session_id="nonexistent", user_id=user_id)
    assert fetched is None


if __name__ == "__main__":
    pass
