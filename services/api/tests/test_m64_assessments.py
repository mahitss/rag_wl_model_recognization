"""M64: Collaborative AI Assessment & Competency Management tests.

Tests cover:

* Assessment creation, listing, retrieval, update, deletion
* Assessment question creation, listing, retrieval
* Assessment attempt creation, listing, retrieval
* Question answer recording and listing
* Competency creation, listing, retrieval, update, deletion
* Competency prerequisite management
* Objective ↔ competency linking
* Question ↔ competency linking
* Competency mastery get and update
* User isolation (no cross-user data leakage)
* Empty state handling
* Schema validation (invalid question types, invalid outcomes, etc.)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.storage import (
    add_competency_prerequisite,
    create_assessment,
    create_assessment_question,
    create_competency,
    create_user,
    delete_assessment,
    delete_competency,
    get_assessment,
    get_assessment_attempt,
    get_assessment_question,
    get_competency,
    get_competency_mastery,
    initialize_storage,
    link_objective_competency,
    link_question_competency,
    list_assessment_attempts,
    list_assessment_question_attempts,
    list_assessment_questions,
    list_assessments,
    list_competencies,
    list_competency_prerequisites,
    record_assessment_attempt,
    record_assessment_question_answer,
    update_assessment,
    update_competency,
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
    """Create a database with a test user and return (settings, user_id, database_path)."""
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = create_user(settings.database_path, "assesstest", "hashed-password", is_admin=False)
    return settings, user_id, settings.database_path


# ---------------------------------------------------------------------------
# Assessment CRUD tests
# ---------------------------------------------------------------------------


def test_create_assessment(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Biology Quiz",
        description="Intro biology assessment.",
        assessment_type="quiz",
        status="draft",
    )

    assert assessment is not None
    assert assessment["assessmentId"].startswith("assess_")
    assert assessment["userId"] == user_id
    assert assessment["courseId"] is None
    assert assessment["title"] == "Biology Quiz"
    assert assessment["description"] == "Intro biology assessment."
    assert assessment["assessmentType"] == "quiz"
    assert assessment["status"] == "draft"


def test_create_assessment_with_course(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    from app.storage import create_course

    course = create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title="Course",
        description="desc.",
        source_type="document",
        source_refs=[],
    )

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=course["courseId"],
        title="Course Final",
        description="Final exam.",
        assessment_type="final",
        status="published",
    )
    assert assessment is not None
    assert assessment["courseId"] == course["courseId"]


def test_create_assessment_invalid_type(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Bad",
        description="",
        assessment_type="invalid_type",
    )
    assert assessment is None


def test_create_assessment_invalid_status(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Bad",
        description="",
        assessment_type="quiz",
        status="invalid_status",
    )
    assert assessment is None


def test_create_assessment_empty_title(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="",
        description="",
        assessment_type="quiz",
    )
    assert assessment is None


def test_get_assessment(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Quiz",
        description="desc.",
        assessment_type="quiz",
    )

    fetched = get_assessment(
        database_path, assessment_id=assessment["assessmentId"], user_id=user_id
    )
    assert fetched is not None
    assert fetched["assessmentId"] == assessment["assessmentId"]
    assert fetched["title"] == "Quiz"


def test_get_assessment_wrong_user(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "assesstest_b", "hashed-password", is_admin=False)

    assessment = create_assessment(
        database_path,
        user_id=user_id_a,
        course_id=None,
        title="Quiz",
        description="desc.",
        assessment_type="quiz",
    )

    fetched = get_assessment(
        database_path, assessment_id=assessment["assessmentId"], user_id=user_id_b
    )
    assert fetched is None


def test_list_assessments(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    create_assessment(
        database_path, user_id=user_id, course_id=None, title="Quiz 1", description="d1",
        assessment_type="quiz", status="draft",
    )
    create_assessment(
        database_path, user_id=user_id, course_id=None, title="Quiz 2", description="d2",
        assessment_type="final", status="published",
    )

    assessments = list_assessments(database_path, user_id=user_id)
    assert len(assessments) == 2
    titles = {a["title"] for a in assessments}
    assert titles == {"Quiz 1", "Quiz 2"}


def test_list_assessments_filter_status(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    create_assessment(
        database_path, user_id=user_id, course_id=None, title="Draft Quiz", description="d",
        assessment_type="quiz", status="draft",
    )
    create_assessment(
        database_path, user_id=user_id, course_id=None, title="Published Final", description="d",
        assessment_type="final", status="published",
    )

    drafts = list_assessments(database_path, user_id=user_id, status="draft")
    assert len(drafts) == 1
    assert drafts[0]["title"] == "Draft Quiz"


def test_update_assessment(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Old Title",
        description="Old desc.",
        assessment_type="quiz",
    )

    updated = update_assessment(
        database_path,
        assessment_id=assessment["assessmentId"],
        user_id=user_id,
        title="New Title",
        status="published",
    )
    assert updated is not None
    assert updated["title"] == "New Title"
    assert updated["status"] == "published"
    assert updated["description"] == "Old desc."  # unchanged


def test_update_assessment_invalid_status(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Quiz",
        description="d",
        assessment_type="quiz",
    )

    updated = update_assessment(
        database_path,
        assessment_id=assessment["assessmentId"],
        user_id=user_id,
        status="invalid_status",
    )
    assert updated is None


def test_delete_assessment(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="To Delete",
        description="d",
        assessment_type="quiz",
    )

    deleted = delete_assessment(
        database_path, assessment_id=assessment["assessmentId"], user_id=user_id
    )
    assert deleted is True

    fetched = get_assessment(
        database_path, assessment_id=assessment["assessmentId"], user_id=user_id
    )
    assert fetched is None


def test_delete_assessment_wrong_user(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "assesstest_b", "hashed-password", is_admin=False)

    assessment = create_assessment(
        database_path,
        user_id=user_id_a,
        course_id=None,
        title="Quiz",
        description="d",
        assessment_type="quiz",
    )

    deleted = delete_assessment(
        database_path, assessment_id=assessment["assessmentId"], user_id=user_id_b
    )
    assert deleted is False


# ---------------------------------------------------------------------------
# Assessment question tests
# ---------------------------------------------------------------------------


def test_create_and_list_assessment_questions(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Quiz",
        description="d",
        assessment_type="quiz",
    )

    q1 = create_assessment_question(
        database_path,
        assessment_id=assessment["assessmentId"],
        user_id=user_id,
        question_type="multiple_choice",
        prompt="What is photosynthesis?",
        answer_key="A",
        accepted_answers=["A"],
        explanation="Plants convert light to energy.",
        difficulty_level="foundational",
        citations=[{"citationId": "cite_1", "retrieval": {"chunkId": "c1"}}],
    )
    assert q1 is not None
    assert q1["questionId"].startswith("aq_")
    assert q1["questionType"] == "multiple_choice"

    q2 = create_assessment_question(
        database_path,
        assessment_id=assessment["assessmentId"],
        user_id=user_id,
        question_type="short_answer",
        prompt="Explain gravity.",
        answer_key="Gravity attracts masses.",
        explanation="Masses attract.",
        difficulty_level="building",
    )
    assert q2 is not None
    assert q2["questionType"] == "short_answer"

    questions = list_assessment_questions(
        database_path, assessment_id=assessment["assessmentId"], user_id=user_id
    )
    assert questions is not None
    assert len(questions) == 2


def test_create_question_invalid_type(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Quiz",
        description="d",
        assessment_type="quiz",
    )

    question = create_assessment_question(
        database_path,
        assessment_id=assessment["assessmentId"],
        user_id=user_id,
        question_type="invalid_type",
        prompt="Prompt?",
        answer_key="A",
    )
    assert question is None


def test_get_assessment_question(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Quiz",
        description="d",
        assessment_type="quiz",
    )

    question = create_assessment_question(
        database_path,
        assessment_id=assessment["assessmentId"],
        user_id=user_id,
        question_type="multiple_choice",
        prompt="Prompt?",
        answer_key="A",
    )

    fetched = get_assessment_question(
        database_path, question_id=question["questionId"], user_id=user_id
    )
    assert fetched is not None
    assert fetched["questionId"] == question["questionId"]
    assert fetched["prompt"] == "Prompt?"


# ---------------------------------------------------------------------------
# Assessment attempt tests
# ---------------------------------------------------------------------------


def test_create_and_list_assessment_attempts(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Quiz",
        description="d",
        assessment_type="quiz",
    )

    attempt = record_assessment_attempt(
        database_path,
        assessment_id=assessment["assessmentId"],
        user_id=user_id,
        status="in_progress",
    )
    assert attempt is not None
    assert attempt["attemptId"].startswith("attempt_")
    assert attempt["status"] == "in_progress"

    attempts = list_assessment_attempts(
        database_path, assessment_id=assessment["assessmentId"], user_id=user_id
    )
    assert len(attempts) == 1
    assert attempts[0]["attemptId"] == attempt["attemptId"]


def test_get_assessment_attempt(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Quiz",
        description="d",
        assessment_type="quiz",
    )

    attempt = record_assessment_attempt(
        database_path,
        assessment_id=assessment["assessmentId"],
        user_id=user_id,
        status="in_progress",
    )

    fetched = get_assessment_attempt(
        database_path, attempt_id=attempt["attemptId"], user_id=user_id
    )
    assert fetched is not None
    assert fetched["attemptId"] == attempt["attemptId"]


# ---------------------------------------------------------------------------
# Question answer recording tests
# ---------------------------------------------------------------------------


def test_record_and_list_question_answers(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Quiz",
        description="d",
        assessment_type="quiz",
    )
    question = create_assessment_question(
        database_path,
        assessment_id=assessment["assessmentId"],
        user_id=user_id,
        question_type="multiple_choice",
        prompt="Prompt?",
        answer_key="A",
    )
    attempt = record_assessment_attempt(
        database_path,
        assessment_id=assessment["assessmentId"],
        user_id=user_id,
        status="in_progress",
    )

    answer = record_assessment_question_answer(
        database_path,
        attempt_id=attempt["attemptId"],
        question_id=question["questionId"],
        user_id=user_id,
        question_type="multiple_choice",
        submitted_answer="A",
        answer_key="A",
        result="correct",
        score=1.0,
        citations=[{"citationId": "cite_1", "retrieval": {}}],
    )
    assert answer is not None
    assert answer["result"] == "correct"
    assert answer["score"] == 1.0

    answers = list_assessment_question_attempts(
        database_path, attempt_id=attempt["attemptId"], user_id=user_id
    )
    assert answers is not None
    assert len(answers) == 1
    assert answers[0]["result"] == "correct"


def test_record_answer_invalid_result(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Quiz",
        description="d",
        assessment_type="quiz",
    )
    question = create_assessment_question(
        database_path,
        assessment_id=assessment["assessmentId"],
        user_id=user_id,
        question_type="multiple_choice",
        prompt="Prompt?",
        answer_key="A",
    )
    attempt = record_assessment_attempt(
        database_path,
        assessment_id=assessment["assessmentId"],
        user_id=user_id,
        status="in_progress",
    )

    answer = record_assessment_question_answer(
        database_path,
        attempt_id=attempt["attemptId"],
        question_id=question["questionId"],
        user_id=user_id,
        question_type="multiple_choice",
        submitted_answer="A",
        answer_key="A",
        result="invalid_result",
        score=1.0,
    )
    assert answer is None


# ---------------------------------------------------------------------------
# Competency tests
# ---------------------------------------------------------------------------


def test_create_and_list_competencies(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    comp1 = create_competency(
        database_path,
        user_id=user_id,
        title="Algebra",
        description="Solving equations.",
        parent_competency_id=None,
        level=0,
    )
    assert comp1 is not None
    assert comp1["competencyId"].startswith("comp_")
    assert comp1["level"] == 0

    comp2 = create_competency(
        database_path,
        user_id=user_id,
        title="Calculus",
        description="Derivatives and integrals.",
        parent_competency_id=comp1["competencyId"],
        level=1,
    )
    assert comp2 is not None
    assert comp2["parentCompetencyId"] == comp1["competencyId"]

    competencies = list_competencies(database_path, user_id=user_id)
    assert len(competencies) == 2


def test_get_competency(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    comp = create_competency(
        database_path,
        user_id=user_id,
        title="Algebra",
        description="desc.",
        parent_competency_id=None,
        level=0,
    )

    fetched = get_competency(
        database_path, competency_id=comp["competencyId"], user_id=user_id
    )
    assert fetched is not None
    assert fetched["competencyId"] == comp["competencyId"]
    assert fetched["title"] == "Algebra"


def test_update_competency(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    comp = create_competency(
        database_path,
        user_id=user_id,
        title="Old Title",
        description="Old desc.",
        parent_competency_id=None,
        level=0,
    )

    updated = update_competency(
        database_path,
        competency_id=comp["competencyId"],
        user_id=user_id,
        title="New Title",
        level=2,
    )
    assert updated is not None
    assert updated["title"] == "New Title"
    assert updated["level"] == 2
    assert updated["description"] == "Old desc."  # unchanged


def test_delete_competency(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    comp = create_competency(
        database_path,
        user_id=user_id,
        title="To Delete",
        description="d",
        parent_competency_id=None,
        level=0,
    )

    deleted = delete_competency(
        database_path, competency_id=comp["competencyId"], user_id=user_id
    )
    assert deleted is True

    fetched = get_competency(
        database_path, competency_id=comp["competencyId"], user_id=user_id
    )
    assert fetched is None


# ---------------------------------------------------------------------------
# Competency prerequisite tests
# ---------------------------------------------------------------------------


def test_add_and_list_competency_prerequisites(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    comp = create_competency(
        database_path,
        user_id=user_id,
        title="Calculus",
        description="desc.",
        parent_competency_id=None,
        level=1,
    )
    prereq = create_competency(
        database_path,
        user_id=user_id,
        title="Algebra",
        description="desc.",
        parent_competency_id=None,
        level=0,
    )

    ok = add_competency_prerequisite(
        database_path,
        competency_id=comp["competencyId"],
        prerequisite_competency_id=prereq["competencyId"],
        user_id=user_id,
    )
    assert ok is True

    prereqs = list_competency_prerequisites(
        database_path, competency_id=comp["competencyId"], user_id=user_id
    )
    assert prereqs is not None
    assert len(prereqs) == 1
    assert prereqs[0]["competencyId"] == prereq["competencyId"]


def test_add_self_prerequisite_fails(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    comp = create_competency(
        database_path,
        user_id=user_id,
        title="Calculus",
        description="desc.",
        parent_competency_id=None,
        level=1,
    )

    ok = add_competency_prerequisite(
        database_path,
        competency_id=comp["competencyId"],
        prerequisite_competency_id=comp["competencyId"],
        user_id=user_id,
    )
    assert ok is False


# ---------------------------------------------------------------------------
# Objective ↔ competency link tests
# ---------------------------------------------------------------------------


def test_link_objective_competency(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    from app.storage import create_course, create_course_objective

    course = create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title="Course",
        description="d",
        source_type="document",
        source_refs=[],
    )
    objective = create_course_objective(
        database_path,
        course_id=course["courseId"],
        user_id=user_id,
        title="Learn algebra",
        description="desc.",
        order_index=1,
    )
    comp = create_competency(
        database_path,
        user_id=user_id,
        title="Algebra",
        description="desc.",
        parent_competency_id=None,
        level=0,
    )

    ok = link_objective_competency(
        database_path,
        objective_id=objective["objectiveId"],
        competency_id=comp["competencyId"],
        user_id=user_id,
    )
    assert ok is True


# ---------------------------------------------------------------------------
# Question ↔ competency link tests
# ---------------------------------------------------------------------------


def test_link_question_competency(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessment = create_assessment(
        database_path,
        user_id=user_id,
        course_id=None,
        title="Quiz",
        description="d",
        assessment_type="quiz",
    )
    question = create_assessment_question(
        database_path,
        assessment_id=assessment["assessmentId"],
        user_id=user_id,
        question_type="multiple_choice",
        prompt="Prompt?",
        answer_key="A",
    )
    comp = create_competency(
        database_path,
        user_id=user_id,
        title="Algebra",
        description="desc.",
        parent_competency_id=None,
        level=0,
    )

    ok = link_question_competency(
        database_path,
        question_id=question["questionId"],
        competency_id=comp["competencyId"],
        user_id=user_id,
    )
    assert ok is True


# ---------------------------------------------------------------------------
# Competency mastery tests
# ---------------------------------------------------------------------------


def test_get_and_update_competency_mastery(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    comp = create_competency(
        database_path,
        user_id=user_id,
        title="Algebra",
        description="desc.",
        parent_competency_id=None,
        level=0,
    )

    # No mastery record yet
    mastery = get_competency_mastery(
        database_path, competency_id=comp["competencyId"], user_id=user_id
    )
    assert mastery is None

    # Update mastery
    updated = update_competency_mastery(
        database_path,
        competency_id=comp["competencyId"],
        user_id=user_id,
        mastery_score=0.75,
    )
    assert updated is not None
    assert updated["masteryScore"] == 0.75
    assert updated["evidenceCount"] == 1

    # Get mastery
    fetched = get_competency_mastery(
        database_path, competency_id=comp["competencyId"], user_id=user_id
    )
    assert fetched is not None
    assert fetched["masteryScore"] == 0.75
    assert fetched["evidenceCount"] == 1


def test_update_competency_mastery_invalid_score(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    comp = create_competency(
        database_path,
        user_id=user_id,
        title="Algebra",
        description="desc.",
        parent_competency_id=None,
        level=0,
    )

    updated = update_competency_mastery(
        database_path,
        competency_id=comp["competencyId"],
        user_id=user_id,
        mastery_score=1.5,
    )
    assert updated is None


def test_update_competency_mastery_increments_evidence(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    comp = create_competency(
        database_path,
        user_id=user_id,
        title="Algebra",
        description="desc.",
        parent_competency_id=None,
        level=0,
    )

    update_competency_mastery(
        database_path,
        competency_id=comp["competencyId"],
        user_id=user_id,
        mastery_score=0.5,
    )
    updated = update_competency_mastery(
        database_path,
        competency_id=comp["competencyId"],
        user_id=user_id,
        mastery_score=0.8,
    )
    assert updated is not None
    assert updated["evidenceCount"] == 2
    assert updated["masteryScore"] == 0.8


# ---------------------------------------------------------------------------
# User isolation tests
# ---------------------------------------------------------------------------


def test_user_isolation_in_assessments(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "assesstest_b", "hashed-password", is_admin=False)

    assessment_a = create_assessment(
        database_path,
        user_id=user_id_a,
        course_id=None,
        title="Assessment A",
        description="User A's assessment.",
        assessment_type="quiz",
    )

    assessments_b = list_assessments(database_path, user_id=user_id_b)
    assert len(assessments_b) == 0

    assessments_a = list_assessments(database_path, user_id=user_id_a)
    assert len(assessments_a) == 1

    fetched = get_assessment(
        database_path, assessment_id=assessment_a["assessmentId"], user_id=user_id_b
    )
    assert fetched is None


def test_user_isolation_in_competencies(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "assesstest_b", "hashed-password", is_admin=False)

    comp_a = create_competency(
        database_path,
        user_id=user_id_a,
        title="Competency A",
        description="User A's competency.",
        parent_competency_id=None,
        level=0,
    )

    competencies_b = list_competencies(database_path, user_id=user_id_b)
    assert len(competencies_b) == 0

    competencies_a = list_competencies(database_path, user_id=user_id_a)
    assert len(competencies_a) == 1

    fetched = get_competency(
        database_path, competency_id=comp_a["competencyId"], user_id=user_id_b
    )
    assert fetched is None


# ---------------------------------------------------------------------------
# Empty state tests
# ---------------------------------------------------------------------------


def test_empty_assessment_state(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assessments = list_assessments(database_path, user_id=user_id)
    assert assessments == []

    fetched = get_assessment(database_path, assessment_id="nonexistent", user_id=user_id)
    assert fetched is None


def test_empty_competency_state(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    competencies = list_competencies(database_path, user_id=user_id)
    assert competencies == []

    fetched = get_competency(database_path, competency_id="nonexistent", user_id=user_id)
    assert fetched is None
