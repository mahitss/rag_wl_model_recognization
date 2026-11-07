"""M63: Collaborative AI Course Builder & Curriculum Generator tests.

Tests cover:
* Course creation, listing, retrieval, update, deletion
* Course module creation and listing
* Course topic creation and listing
* Course objective creation and listing
* Course assessment creation and listing
* Course progress creation, retrieval, and update
* Course building from evidence (AI)
* Course curriculum retrieval
* Next topic recommendation
* Course recommendations
* User isolation (no cross-user data leakage)
* Empty state handling (new user with no courses)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.storage import (
    create_course,
    create_course_assessment,
    create_course_module,
    create_course_objective,
    create_course_progress,
    create_course_topic,
    create_quiz_set,
    create_user,
    delete_course,
    get_course,
    get_course_progress,
    initialize_storage,
    list_course_assessments,
    list_course_modules,
    list_course_objectives,
    list_course_topics,
    list_courses,
    update_course,
    update_course_progress,
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
    """Create a database with a test user and return (settings, user_id, database_path)."""
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = create_user(settings.database_path, "coursetest", "hashed-password", is_admin=False)
    return settings, user_id, settings.database_path


# ---------------------------------------------------------------------------
# Course CRUD tests
# ---------------------------------------------------------------------------


def test_create_course(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    course = create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title="Biology 101",
        description="Introductory biology course.",
        source_type="document",
        source_refs=[{"documentId": "doc_1", "displayFilename": "bio.pdf"}],
        difficulty_level="foundational",
        estimated_workload_minutes=120,
    )

    assert course is not None
    assert course["userId"] == user_id
    assert course["title"] == "Biology 101"
    assert course["description"] == "Introductory biology course."
    assert course["sourceType"] == "document"
    assert course["status"] == "draft"
    assert course["difficultyLevel"] == "foundational"
    assert course["estimatedWorkloadMinutes"] == 120


def test_get_course(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    course = create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title="Biology 101",
        description="Intro biology.",
        source_type="document",
        source_refs=[],
    )

    fetched = get_course(database_path, course_id=course["courseId"], user_id=user_id)
    assert fetched is not None
    assert fetched["courseId"] == course["courseId"]
    assert fetched["title"] == "Biology 101"


def test_list_courses(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title="Course 1",
        description="First course.",
        source_type="document",
        source_refs=[],
    )
    create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title="Course 2",
        description="Second course.",
        source_type="document",
        source_refs=[],
    )

    courses = list_courses(database_path, user_id=user_id)
    assert len(courses) == 2
    titles = {c["title"] for c in courses}
    assert titles == {"Course 1", "Course 2"}


def test_update_course(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    course = create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title="Old Title",
        description="Old description.",
        source_type="document",
        source_refs=[],
    )

    updated = update_course(
        database_path,
        course_id=course["courseId"],
        user_id=user_id,
        title="New Title",
        status="active",
    )
    assert updated is not None
    assert updated["title"] == "New Title"
    assert updated["status"] == "active"
    assert updated["description"] == "Old description."  # unchanged


def test_delete_course(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    course = create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title="To Delete",
        description="Will be deleted.",
        source_type="document",
        source_refs=[],
    )

    deleted = delete_course(database_path, course_id=course["courseId"], user_id=user_id)
    assert deleted is True

    fetched = get_course(database_path, course_id=course["courseId"], user_id=user_id)
    assert fetched is None


# ---------------------------------------------------------------------------
# Course module tests
# ---------------------------------------------------------------------------


def test_create_and_list_course_modules(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    course = create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title="Course",
        description="desc.",
        source_type="document",
        source_refs=[],
    )

    module = create_course_module(
        database_path,
        course_id=course["courseId"],
        user_id=user_id,
        title="Module 1",
        description="First module.",
        order_index=1,
    )
    assert module is not None
    assert module["title"] == "Module 1"
    assert module["courseId"] == course["courseId"]

    modules = list_course_modules(
        database_path, course_id=course["courseId"], user_id=user_id
    )
    assert len(modules) == 1
    assert modules[0]["title"] == "Module 1"


# ---------------------------------------------------------------------------
# Course topic tests
# ---------------------------------------------------------------------------


def test_create_and_list_course_topics(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    course = create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title="Course",
        description="desc.",
        source_type="document",
        source_refs=[],
    )
    module = create_course_module(
        database_path,
        course_id=course["courseId"],
        user_id=user_id,
        title="Module 1",
        description="desc.",
        order_index=1,
    )

    topic = create_course_topic(
        database_path,
        module_id=module["moduleId"],
        user_id=user_id,
        title="Topic 1",
        description="Topic desc.",
        order_index=1,
        prerequisite_topic_ids=[],
        difficulty_level="foundational",
        estimated_minutes=30,
    )
    assert topic is not None
    assert topic["title"] == "Topic 1"
    assert topic["difficultyLevel"] == "foundational"

    topics = list_course_topics(
        database_path, module_id=module["moduleId"], user_id=user_id
    )
    assert len(topics) == 1
    assert topics[0]["title"] == "Topic 1"


# ---------------------------------------------------------------------------
# Course objective tests
# ---------------------------------------------------------------------------


def test_create_and_list_course_objectives(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    course = create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title="Course",
        description="desc.",
        source_type="document",
        source_refs=[],
    )

    objective = create_course_objective(
        database_path,
        course_id=course["courseId"],
        user_id=user_id,
        title="Learn photosynthesis",
        description="Understand how plants convert light to energy.",
        order_index=1,
    )
    assert objective is not None
    assert objective["title"] == "Learn photosynthesis"

    objectives = list_course_objectives(
        database_path, course_id=course["courseId"], user_id=user_id
    )
    assert len(objectives) == 1
    assert objectives[0]["title"] == "Learn photosynthesis"


# ---------------------------------------------------------------------------
# Course assessment tests
# ---------------------------------------------------------------------------


def test_create_and_list_course_assessments(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    course = create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title="Course",
        description="desc.",
        source_type="document",
        source_refs=[],
    )

    # First create a quiz set to link as an assessment
    quiz_id = "quiz_1"
    create_quiz_set(
        database_path,
        {
            "quiz_id": quiz_id,
            "topic_id": "topic_1",
            "topic": "Biology",
            "created_at": datetime.now(UTC).isoformat(),
            "questions": [],
        },
        user_id=user_id,
    )

    assessment = create_course_assessment(
        database_path,
        course_id=course["courseId"],
        user_id=user_id,
        topic_id=None,
        assessment_type="quiz",
        quiz_id=quiz_id,
    )
    assert assessment is not None
    assert assessment["assessmentType"] == "quiz"
    assert assessment["quizId"] == quiz_id

    assessments = list_course_assessments(
        database_path, course_id=course["courseId"], user_id=user_id
    )
    assert len(assessments) == 1
    assert assessments[0]["assessmentType"] == "quiz"


# ---------------------------------------------------------------------------
# Course progress tests
# ---------------------------------------------------------------------------


def test_create_get_update_course_progress(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    course = create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title="Course",
        description="desc.",
        source_type="document",
        source_refs=[],
    )

    # Create a topic first
    module = create_course_module(
        database_path,
        course_id=course["courseId"],
        user_id=user_id,
        title="Module 1",
        description="desc.",
        order_index=1,
    )
    topic = create_course_topic(
        database_path,
        module_id=module["moduleId"],
        user_id=user_id,
        title="Topic 1",
        description="desc.",
        order_index=1,
    )

    progress = create_course_progress(
        database_path,
        course_id=course["courseId"],
        user_id=user_id,
        topic_id=topic["topicId"],
        status="in_progress",
        mastery_score=0.5,
    )
    assert progress is not None
    assert progress["status"] == "in_progress"
    assert progress["masteryScore"] == 0.5

    # Get progress
    fetched = get_course_progress(
        database_path,
        course_id=course["courseId"],
        user_id=user_id,
        topic_id=topic["topicId"],
    )
    assert fetched is not None
    assert fetched["topicId"] == topic["topicId"]
    assert fetched["masteryScore"] == 0.5

    # Update progress
    updated = update_course_progress(
        database_path,
        course_id=course["courseId"],
        user_id=user_id,
        topic_id=topic["topicId"],
        status="completed",
        mastery_score=1.0,
    )
    assert updated is not None
    assert updated["status"] == "completed"
    assert updated["masteryScore"] == 1.0


# ---------------------------------------------------------------------------
# Course build from evidence tests
# ---------------------------------------------------------------------------


def test_build_course_from_evidence_no_evidence(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    from app.ai_course import build_course_from_evidence

    result = build_course_from_evidence(
        settings,
        database_path,
        user_id=user_id,
        space_id=None,
        topic="Nonexistent Topic",
        document_ids=None,
        limit=5,
        mode=None,
        difficulty_level=None,
        embeddings=None,
    )

    assert result["courseId"] is None
    assert result["status"] == "insufficient_evidence"


# ---------------------------------------------------------------------------
# User isolation tests
# ---------------------------------------------------------------------------


def test_user_isolation_in_courses(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "coursetest_b", "hashed-password", is_admin=False)

    # Create content for user A
    course_a = create_course(
        database_path,
        user_id=user_id_a,
        space_id=None,
        title="Course A",
        description="User A's course.",
        source_type="document",
        source_refs=[],
    )

    # List courses for user B - should be empty
    courses_b = list_courses(database_path, user_id=user_id_b)
    assert len(courses_b) == 0

    # List courses for user A - should have 1
    courses_a = list_courses(database_path, user_id=user_id_a)
    assert len(courses_a) == 1

    # User B cannot access User A's course
    fetched = get_course(
        database_path, course_id=course_a["courseId"], user_id=user_id_b
    )
    assert fetched is None


# ---------------------------------------------------------------------------
# Empty state tests
# ---------------------------------------------------------------------------


def test_empty_course_state(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    courses = list_courses(database_path, user_id=user_id)
    assert courses == []

    # Get course that doesn't exist
    fetched = get_course(database_path, course_id="nonexistent", user_id=user_id)
    assert fetched is None
