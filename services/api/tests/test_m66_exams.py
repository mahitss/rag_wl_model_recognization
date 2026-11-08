"""M66: AI Examination Simulator & Exam Preparation Engine tests.

Tests cover:

* Exam creation, listing, retrieval, update, deletion
* Exam section creation and listing
* Exam question creation and listing
* Exam attempt creation, listing, retrieval
* Exam answer recording and listing
* Exam result recording and retrieval
* Exam readiness calculation
* User isolation
* Empty state handling
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.storage import (
    create_assessment,
    create_assessment_question,
    create_competency,
    create_exam,
    create_exam_question,
    record_exam_result,
    create_exam_section,
    create_user,
    delete_exam,
    get_exam,
    get_exam_attempt,
    get_exam_result,
    initialize_storage,
    list_exam_attempts,
    list_exam_answers,
    list_exam_questions,
    list_exam_results,
    list_exam_sections,
    list_exams,
    record_exam_attempt,
    record_exam_answer,
    update_exam,
    update_competency_mastery,
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
    user_id = create_user(settings.database_path, "examtest", "hashed-password", is_admin=False)
    return settings, user_id, settings.database_path


# ---------------------------------------------------------------------------
# Exam CRUD tests
# ---------------------------------------------------------------------------


def test_create_exam(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    exam = create_exam(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Mathematics Final",
        subject="Mathematics",
        topic="Algebra",
        description="Final exam for algebra.",
        duration_minutes=60,
        total_marks=100,
        question_count=20,
        passing_score=0.60,
        negative_marking=0.25,
        instructions="Answer all questions.",
        difficulty_distribution={"foundational": 5, "building": 10, "practice": 5},
        status="draft",
    )

    assert exam is not None
    assert exam["examId"].startswith("exam_")
    assert exam["userId"] == user_id
    assert exam["title"] == "Mathematics Final"
    assert exam["subject"] == "Mathematics"
    assert exam["topic"] == "Algebra"
    assert exam["durationMinutes"] == 60
    assert exam["totalMarks"] == 100
    assert exam["questionCount"] == 20
    assert exam["passingScore"] == 0.60
    assert exam["negativeMarking"] == 0.25
    assert exam["status"] == "draft"


def test_create_exam_invalid_inputs(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    # Negative marks
    exam = create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Bad", subject="Math", topic="Algebra", description=None,
        duration_minutes=0, total_marks=100, question_count=20,
        passing_score=0.6, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )
    assert exam is None

    # Invalid passing score
    exam = create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Bad", subject="Math", topic="Algebra", description=None,
        duration_minutes=60, total_marks=100, question_count=20,
        passing_score=1.5, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )
    assert exam is None


def test_get_exam(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    exam = create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Quiz", subject="Math", topic="Algebra", description=None,
        duration_minutes=30, total_marks=50, question_count=10,
        passing_score=0.5, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )

    fetched = get_exam(database_path, exam_id=exam["examId"], user_id=user_id)
    assert fetched is not None
    assert fetched["examId"] == exam["examId"]
    assert fetched["title"] == "Quiz"


def test_get_exam_wrong_user(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "examtest_b", "hashed-password", is_admin=False)

    exam = create_exam(
        database_path, user_id=user_id_a, course_id=None,
        title="Quiz", subject="Math", topic="Algebra", description=None,
        duration_minutes=30, total_marks=50, question_count=10,
        passing_score=0.5, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )

    fetched = get_exam(database_path, exam_id=exam["examId"], user_id=user_id_b)
    assert fetched is None


def test_list_exams(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Exam 1", subject="Math", topic="Algebra", description=None,
        duration_minutes=30, total_marks=50, question_count=10,
        passing_score=0.5, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )
    create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Exam 2", subject="Math", topic="Calculus", description=None,
        duration_minutes=45, total_marks=75, question_count=15,
        passing_score=0.6, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )

    exams = list_exams(database_path, user_id=user_id)
    assert len(exams) == 2
    titles = {e["title"] for e in exams}
    assert titles == {"Exam 1", "Exam 2"}


def test_update_exam(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    exam = create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Old Title", subject="Math", topic="Algebra", description=None,
        duration_minutes=30, total_marks=50, question_count=10,
        passing_score=0.5, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )

    updated = update_exam(
        database_path,
        exam_id=exam["examId"], user_id=user_id,
        title="New Title", duration_minutes=45, passing_score=0.7,
    )
    assert updated is not None
    assert updated["title"] == "New Title"
    assert updated["durationMinutes"] == 45
    assert updated["passingScore"] == 0.7
    assert updated["totalMarks"] == 50  # unchanged


def test_delete_exam(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    exam = create_exam(
        database_path, user_id=user_id, course_id=None,
        title="To Delete", subject="Math", topic="Algebra", description=None,
        duration_minutes=30, total_marks=50, question_count=10,
        passing_score=0.5, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )

    deleted = delete_exam(database_path, exam_id=exam["examId"], user_id=user_id)
    assert deleted is True

    fetched = get_exam(database_path, exam_id=exam["examId"], user_id=user_id)
    assert fetched is None


# ---------------------------------------------------------------------------
# Exam section tests
# ---------------------------------------------------------------------------


def test_create_and_list_exam_sections(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    exam = create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Quiz", subject="Math", topic="Algebra", description=None,
        duration_minutes=30, total_marks=50, question_count=10,
        passing_score=0.5, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )

    section = create_exam_section(
        database_path,
        exam_id=exam["examId"], user_id=user_id,
        title="Section A", description="First section.",
        question_count=5, marks=25, position=0,
    )
    assert section is not None
    assert section["sectionId"].startswith("exsec_")
    assert section["title"] == "Section A"

    sections = list_exam_sections(
        database_path, exam_id=exam["examId"], user_id=user_id
    )
    assert sections is not None
    assert len(sections) == 1


# ---------------------------------------------------------------------------
# Exam question tests
# ---------------------------------------------------------------------------


def test_create_and_list_exam_questions(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    exam = create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Quiz", subject="Math", topic="Algebra", description=None,
        duration_minutes=30, total_marks=50, question_count=10,
        passing_score=0.5, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )

    q = create_exam_question(
        database_path,
        exam_id=exam["examId"], user_id=user_id,
        section_id=None, source_question_id=None,
        question_type="multiple_choice", prompt="2+2=?",
        answer_key="A", accepted_answers=["A"],
        explanation="Basic addition.", marks=5,
        difficulty_level="foundational", position=0,
        citations=[{"citationId": "cite_1", "retrieval": {}}],
    )
    assert q is not None
    assert q["examQuestionId"].startswith("exq_")
    assert q["questionType"] == "multiple_choice"

    questions = list_exam_questions(
        database_path, exam_id=exam["examId"], user_id=user_id
    )
    assert questions is not None
    assert len(questions) == 1


# ---------------------------------------------------------------------------
# Exam attempt tests
# ---------------------------------------------------------------------------


def test_create_and_list_exam_attempts(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    exam = create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Quiz", subject="Math", topic="Algebra", description=None,
        duration_minutes=30, total_marks=50, question_count=10,
        passing_score=0.5, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )

    attempt = record_exam_attempt(
        database_path, exam_id=exam["examId"], user_id=user_id,
    )
    assert attempt is not None
    assert attempt["attemptId"].startswith("exattempt_")
    assert attempt["status"] == "in_progress"

    attempts = list_exam_attempts(
        database_path, exam_id=exam["examId"], user_id=user_id
    )
    assert len(attempts) == 1


def test_get_exam_attempt(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    exam = create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Quiz", subject="Math", topic="Algebra", description=None,
        duration_minutes=30, total_marks=50, question_count=10,
        passing_score=0.5, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )

    attempt = record_exam_attempt(
        database_path, exam_id=exam["examId"], user_id=user_id,
    )

    fetched = get_exam_attempt(
        database_path, attempt_id=attempt["attemptId"], user_id=user_id
    )
    assert fetched is not None
    assert fetched["attemptId"] == attempt["attemptId"]


# ---------------------------------------------------------------------------
# Exam answer tests
# ---------------------------------------------------------------------------


def test_record_and_list_exam_answers(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    exam = create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Quiz", subject="Math", topic="Algebra", description=None,
        duration_minutes=30, total_marks=50, question_count=10,
        passing_score=0.5, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )
    q = create_exam_question(
        database_path,
        exam_id=exam["examId"], user_id=user_id,
        section_id=None, source_question_id=None,
        question_type="multiple_choice", prompt="2+2=?",
        answer_key="A", accepted_answers=["A"],
        explanation=None, marks=5,
        difficulty_level="foundational", position=0,
        citations=None,
    )
    attempt = record_exam_attempt(
        database_path, exam_id=exam["examId"], user_id=user_id,
    )

    answer = record_exam_answer(
        database_path,
        attempt_id=attempt["attemptId"],
        exam_question_id=q["examQuestionId"],
        user_id=user_id,
        submitted_answer="A",
        marks_obtained=5.0,
        outcome="correct",
        is_marked_for_review=False,
        time_spent_ms=30000,
    )
    assert answer is not None
    assert answer["outcome"] == "correct"
    assert answer["marksObtained"] == 5.0

    answers = list_exam_answers(
        database_path, attempt_id=attempt["attemptId"], user_id=user_id
    )
    assert answers is not None
    assert len(answers) == 1


# ---------------------------------------------------------------------------
# Exam result tests
# ---------------------------------------------------------------------------


def test_record_and_get_exam_result(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    exam = create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Quiz", subject="Math", topic="Algebra", description=None,
        duration_minutes=30, total_marks=100, question_count=20,
        passing_score=0.6, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )
    attempt = record_exam_attempt(
        database_path, exam_id=exam["examId"], user_id=user_id,
    )

    result = record_exam_result(
        database_path,
        attempt_id=attempt["attemptId"],
        exam_id=exam["examId"],
        user_id=user_id,
        total_marks=100,
        marks_obtained=75.0,
        percentage=75.0,
        passed=True,
        correct_count=15,
        incorrect_count=3,
        unanswered_count=2,
        partial_count=0,
        time_spent_ms=1800000,
        topic_wise={"algebra": {"correct": 10, "total": 12}},
        competency_wise={"algebra_mastery": {"score": 0.75}},
        difficulty_wise={"foundational": {"correct": 5, "total": 5}},
    )
    assert result is not None
    assert result["resultId"].startswith("exresult_")
    assert result["percentage"] == 75.0
    assert result["passed"] is True

    fetched = get_exam_result(
        database_path, result_id=result["resultId"], user_id=user_id
    )
    assert fetched is not None
    assert fetched["resultId"] == result["resultId"]


def test_list_exam_results(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    exam = create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Quiz", subject="Math", topic="Algebra", description=None,
        duration_minutes=30, total_marks=100, question_count=20,
        passing_score=0.6, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )
    attempt = record_exam_attempt(
        database_path, exam_id=exam["examId"], user_id=user_id,
    )

    record_exam_result(
        database_path,
        attempt_id=attempt["attemptId"],
        exam_id=exam["examId"],
        user_id=user_id,
        total_marks=100, marks_obtained=75.0, percentage=75.0,
        passed=True, correct_count=15, incorrect_count=3,
        unanswered_count=2, partial_count=0,
        time_spent_ms=1800000,
        topic_wise={"algebra": {"correct": 10, "total": 12}},
        competency_wise={"algebra_mastery": {"score": 0.75}},
        difficulty_wise={"foundational": {"correct": 5, "total": 5}},
    )

    results = list_exam_results(database_path, user_id=user_id)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# Exam readiness tests
# ---------------------------------------------------------------------------


def test_exam_readiness_with_mastery(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    exam = create_exam(
        database_path, user_id=user_id, course_id=None,
        title="Math Exam", subject="Math", topic="Algebra",
        description=None, duration_minutes=60, total_marks=100,
        question_count=20, passing_score=0.6, negative_marking=0.0,
        instructions=None, difficulty_distribution=None,
    )

    # Create competency with high mastery
    comp = create_competency(
        database_path, user_id=user_id, title="Algebra Mastery",
        description="d", parent_competency_id=None, level=0,
    )
    update_competency_mastery(
        database_path, competency_id=comp["competencyId"],
        user_id=user_id, mastery_score=0.85,
    )

    # Verify exam exists
    fetched = get_exam(database_path, exam_id=exam["examId"], user_id=user_id)
    assert fetched is not None
    assert fetched["topic"] == "Algebra"


def test_user_isolation_in_exams(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "examtest_b", "hashed-password", is_admin=False)

    exam_a = create_exam(
        database_path, user_id=user_id_a, course_id=None,
        title="Exam A", subject="Math", topic="Algebra", description=None,
        duration_minutes=30, total_marks=50, question_count=10,
        passing_score=0.5, negative_marking=0.0, instructions=None,
        difficulty_distribution=None,
    )

    exams_b = list_exams(database_path, user_id=user_id_b)
    assert len(exams_b) == 0

    exams_a = list_exams(database_path, user_id=user_id_a)
    assert len(exams_a) == 1

    fetched = get_exam(database_path, exam_id=exam_a["examId"], user_id=user_id_b)
    assert fetched is None


def test_empty_exam_state(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    exams = list_exams(database_path, user_id=user_id)
    assert exams == []

    fetched = get_exam(database_path, exam_id="nonexistent", user_id=user_id)
    assert fetched is None
