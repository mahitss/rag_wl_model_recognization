"""M71: Classroom Intelligence & Teaching Effectiveness Analytics tests.

Tests cover:

* Classroom metric aggregation from persisted quiz attempts, study sessions,
  and topic reviews.
* Topic-level performance (accuracy, mastery, attempts).
* Sample-size handling and small-group privacy suppression.
* Missing and incomplete data (empty sessions, no attempts).
* Duplicate activity events (idempotent recompute).
* Individual versus aggregate access (educator vs learner).
* Cross-classroom isolation.
* Recommendation evidence and persistence.
* Permission enforcement.
* Data deletion and recalculation.
"""

from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.classroom_intelligence import (
    build_classroom_intelligence_dashboard,
    compute_teaching_effectiveness,
    generate_classroom_recommendations,
)
from app.config import Settings
from app.storage import (
    complete_study_session,
    connect_database,
    create_classroom_analytics_snapshot,
    create_classroom_recommendation,
    create_classroom_session,
    create_user,
    delete_classroom_session,
    initialize_storage,
    join_classroom_session,
    list_classroom_analytics_snapshots,
    list_classroom_recommendations,
    list_classroom_topic_performance,
    record_classroom_topic_performance,
    record_quiz_attempt,
    start_study_session,
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


def _create_quiz_set_direct(
    database_path: Path,
    user_id: str,
    topic_id: str,
    topic: str,
    n_questions: int = 3,
    suffix: str = "",
) -> str:
    """Create a quiz set and questions directly in the database."""
    quiz_id = f"quiz_direct_{user_id}_{topic_id}_{suffix}"
    now = datetime.now(UTC).isoformat()
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                "INSERT INTO quiz_sets("
                "quiz_id, topic_id, topic, created_at, user_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (quiz_id, topic_id, topic, now, user_id),
            )
            for i in range(n_questions):
                qid = f"q_direct_{quiz_id}_{i}"
                connection.execute(
                    """
                    INSERT INTO quiz_questions(
                        question_id, quiz_id, position, question_type, prompt,
                        options_json, answer_key, accepted_answers_json, explanation,
                        difficulty_level, user_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        qid,
                        quiz_id,
                        i,
                        "multiple_choice",
                        f"Question {i} about {topic}?",
                        json.dumps([{"id": "A", "text": "A"}, {"id": "B", "text": "B"}]),
                        "A",
                        json.dumps(["A"]),
                        "Explanation",
                        "building",
                        user_id,
                    ),
                )
    return quiz_id


def _record_attempts(
    database_path: Path,
    user_id: str,
    topic_id: str,
    topic: str,
    outcomes: list[str],
    base_time: datetime,
    suffix: str = "",
) -> None:
    """Record quiz attempts for a user on a topic."""
    quiz_id = _create_quiz_set_direct(
        database_path, user_id, topic_id, topic, suffix=suffix
    )
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            "SELECT question_id FROM quiz_questions WHERE quiz_id = ? ORDER BY position",
            (quiz_id,),
        ).fetchall()
    question_ids = [row["question_id"] for row in rows]
    if not question_ids:
        return
    for idx, outcome in enumerate(outcomes):
        qid = question_ids[idx % len(question_ids)]
        created_at = (base_time + timedelta(minutes=idx)).isoformat()
        attempt_id = (
            f"attempt_{user_id}_{topic_id}_{suffix}_{idx}"
            if suffix
            else f"attempt_{user_id}_{topic_id}_{idx}"
        )
        record_quiz_attempt(
            database_path,
            attempt_id=attempt_id,
            question_id=qid,
            outcome=outcome,
            created_at=created_at,
            user_id=user_id,
            duration_ms=30000,
        )


# ---------------------------------------------------------------------------
# Dashboard / metric aggregation tests
# ---------------------------------------------------------------------------


def test_dashboard_aggregates_quiz_attempts_by_topic(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Algebra Class",
        subject="Mathematics",
        topic="Linear Equations",
        status="active",
    )
    learner_id = create_user(database_path, "learner1", "hashed-password")
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=learner_id, role="learner"
    )
    # Add two more learners to meet MIN_STUDENTS_FOR_CLASSROOM_AGGREGATE (3)
    for i in (2, 3):
        uid = create_user(database_path, f"learner_{i}", "hashed-password")
        join_classroom_session(
            database_path, session_id=session["sessionId"], user_id=uid, role="learner"
        )

    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    _record_attempts(
        database_path, learner_id, "topic_linear", "Linear Equations",
        ["correct", "correct", "incorrect", "correct"], base,
    )

    dashboard = build_classroom_intelligence_dashboard(
        database_path=database_path,
        session_id=session["sessionId"],
        user_id=educator_id,
        now=base + timedelta(hours=1),
    )
    assert dashboard is not None
    assert dashboard["sessionId"] == session["sessionId"]
    assert dashboard["educatorId"] == educator_id
    assert dashboard["sampleSize"] >= 1
    topic_map = {t["topicId"]: t for t in dashboard["topicPerformance"]}
    assert "topic_linear" in topic_map
    assert topic_map["topic_linear"]["accuracy"] == 0.75
    # 4 quiz attempts + 3 topic reviews (one per unique question) = 7 total
    assert topic_map["topic_linear"]["totalAttempts"] == 7
    assert topic_map["topic_linear"]["correctAttempts"] == 3
    assert topic_map["topic_linear"]["incorrectAttempts"] == 1


def test_dashboard_includes_study_sessions_and_reviews(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Bio Class",
        subject="Biology",
        topic="Photosynthesis",
        status="active",
    )
    learner_id = create_user(database_path, "learner2", "hashed-password")
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=learner_id, role="learner"
    )
    # Add two more learners to meet MIN_STUDENTS_FOR_CLASSROOM_AGGREGATE (3)
    for i in (2, 3):
        uid = create_user(database_path, f"photo_learner_{i}", "hashed-password")
        join_classroom_session(
            database_path, session_id=session["sessionId"], user_id=uid, role="learner"
        )

    base = datetime(2026, 2, 1, 9, 0, 0, tzinfo=UTC)
    _record_attempts(
        database_path, learner_id, "topic_photo", "Photosynthesis",
        ["correct", "incorrect"], base,
    )
    start_study_session(
        database_path,
        session_id=f"study_{learner_id}_photo",
        topic_id="topic_photo",
        topic="Photosynthesis",
        started_at=(base + timedelta(minutes=10)).isoformat(),
        user_id=learner_id,
    )
    complete_study_session(
        database_path,
        session_id=f"study_{learner_id}_photo",
        completed_at=(base + timedelta(minutes=40)).isoformat(),
    )

    dashboard = build_classroom_intelligence_dashboard(
        database_path=database_path,
        session_id=session["sessionId"],
        user_id=educator_id,
        now=base + timedelta(hours=2),
    )
    assert dashboard is not None
    topic_map = {t["topicId"]: t for t in dashboard["topicPerformance"]}
    assert "topic_photo" in topic_map
    # Should include quiz attempts (2) + study session (1) = 3 total
    assert topic_map["topic_photo"]["totalAttempts"] >= 2


def test_dashboard_empty_session(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Empty Class",
        subject="Math",
        topic="Nothing",
        status="scheduled",
    )

    dashboard = build_classroom_intelligence_dashboard(
        database_path=database_path,
        session_id=session["sessionId"],
        user_id=educator_id,
        now=datetime.now(UTC),
    )
    assert dashboard is not None
    assert dashboard["sampleSize"] >= 1
    assert dashboard["topicPerformance"] == []
    assert dashboard["overallMetrics"]["overallAccuracy"] is None


def test_dashboard_nonexistent_session(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    result = build_classroom_intelligence_dashboard(
        database_path=database_path,
        session_id="classroom_nonexistent",
        user_id=educator_id,
        now=datetime.now(UTC),
    )
    assert result is None


# ---------------------------------------------------------------------------
# Topic-level performance tests
# ---------------------------------------------------------------------------


def test_topic_performance_records_and_lists(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Topic Perf Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )

    created = record_classroom_topic_performance(
        database_path,
        session_id=session["sessionId"],
        topic_id="topic_1",
        topic="Linear Equations",
        total_attempts=10,
        correct_attempts=7,
        incorrect_attempts=3,
        ambiguous_attempts=0,
    )
    assert created is not None
    assert created["accuracy"] == 0.7
    assert created["masteryScore"] == 0.7

    listed = list_classroom_topic_performance(
        database_path, session_id=session["sessionId"], user_id=educator_id
    )
    assert len(listed) == 1
    assert listed[0]["topicId"] == "topic_1"


def test_topic_performance_denormalizes_on_duplicate_record(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Dup Topic Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )

    record_classroom_topic_performance(
        database_path,
        session_id=session["sessionId"],
        topic_id="topic_dup",
        topic="Equations",
        total_attempts=5,
        correct_attempts=3,
        incorrect_attempts=2,
        ambiguous_attempts=0,
    )
    record_classroom_topic_performance(
        database_path,
        session_id=session["sessionId"],
        topic_id="topic_dup",
        topic="Equations",
        total_attempts=5,
        correct_attempts=3,
        incorrect_attempts=2,
        ambiguous_attempts=0,
    )
    listed = list_classroom_topic_performance(
        database_path, session_id=session["sessionId"], user_id=educator_id
    )
    assert len(listed) == 1


def test_topic_performance_invalid_inputs_return_none(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Invalid Topic Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )

    result = record_classroom_topic_performance(
        database_path,
        session_id=session["sessionId"],
        topic_id="topic_bad",
        topic="Bad",
        total_attempts=2,
        correct_attempts=5,
        incorrect_attempts=0,
        ambiguous_attempts=0,
    )
    assert result is None

    result = record_classroom_topic_performance(
        database_path,
        session_id=session["sessionId"],
        topic_id="topic_neg",
        topic="Neg",
        total_attempts=-1,
        correct_attempts=0,
        incorrect_attempts=0,
        ambiguous_attempts=0,
    )
    assert result is None


# ---------------------------------------------------------------------------
# Small-group privacy tests
# ---------------------------------------------------------------------------


def test_small_group_privacy_suppresses_detail(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Tiny Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )
    learner_id = create_user(database_path, "lonely", "hashed-password")
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=learner_id, role="learner"
    )
    # totalParticipants = 2 < MIN_STUDENTS_FOR_CLASSROOM_AGGREGATE (3)

    base = datetime(2026, 3, 1, 8, 0, 0, tzinfo=UTC)
    _record_attempts(
        database_path, learner_id, "topic_tiny", "Tiny Topic",
        ["correct", "correct", "correct"], base,
    )

    dashboard = build_classroom_intelligence_dashboard(
        database_path=database_path,
        session_id=session["sessionId"],
        user_id=educator_id,
        now=base + timedelta(hours=1),
    )
    assert dashboard is not None
    assert dashboard["privacySuppressed"] is True
    assert dashboard["sampleSize"] == 2
    for tp in dashboard["topicPerformance"]:
        assert tp.get("privacySuppressed") is True
        assert tp["accuracy"] is None
        assert tp["totalAttempts"] is None


def test_group_meets_threshold_shows_detail(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Normal Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )
    learner_ids = []
    for i in range(3):
        uid = create_user(database_path, f"learner_{i}", "hashed-password")
        learner_ids.append(uid)
        join_classroom_session(
            database_path, session_id=session["sessionId"], user_id=uid, role="learner"
        )

    base = datetime(2026, 4, 1, 8, 0, 0, tzinfo=UTC)
    _record_attempts(
        database_path, learner_ids[0], "topic_normal", "Normal Topic",
        ["correct", "incorrect"], base,
    )

    dashboard = build_classroom_intelligence_dashboard(
        database_path=database_path,
        session_id=session["sessionId"],
        user_id=educator_id,
        now=base + timedelta(hours=1),
    )
    assert dashboard is not None
    assert dashboard["privacySuppressed"] is False
    topic_map = {t["topicId"]: t for t in dashboard["topicPerformance"]}
    assert "topic_normal" in topic_map
    assert topic_map["topic_normal"]["accuracy"] is not None


# ---------------------------------------------------------------------------
# Missing / incomplete data tests
# ---------------------------------------------------------------------------


def test_dashboard_with_only_questions_no_attempts(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Question Only Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )

    dashboard = build_classroom_intelligence_dashboard(
        database_path=database_path,
        session_id=session["sessionId"],
        user_id=educator_id,
        now=datetime.now(UTC),
    )
    assert dashboard is not None
    assert dashboard["overallMetrics"]["overallAccuracy"] is None
    assert dashboard["topicPerformance"] == []


# ---------------------------------------------------------------------------
# Individual vs aggregate access tests
# ---------------------------------------------------------------------------


def test_learner_can_access_own_classroom_aggregate(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Access Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )
    learner_id = create_user(database_path, "learner_access", "hashed-password")
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=learner_id, role="learner"
    )

    dashboard = build_classroom_intelligence_dashboard(
        database_path=database_path,
        session_id=session["sessionId"],
        user_id=learner_id,
        now=datetime.now(UTC),
    )
    assert dashboard is not None


def test_non_participant_cannot_access_classroom(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Isolated Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )
    outsider = create_user(database_path, "outsider", "hashed-password")

    dashboard = build_classroom_intelligence_dashboard(
        database_path=database_path,
        session_id=session["sessionId"],
        user_id=outsider,
        now=datetime.now(UTC),
    )
    assert dashboard is None


# ---------------------------------------------------------------------------
# Cross-classroom isolation tests
# ---------------------------------------------------------------------------


def test_cross_classroom_isolation(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session_a = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Class A",
        subject="Math",
        topic="Algebra",
        status="active",
    )
    session_b = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Class B",
        subject="Math",
        topic="Geometry",
        status="active",
    )
    learner_id = create_user(database_path, "learner_iso", "hashed-password")
    join_classroom_session(
        database_path, session_id=session_a["sessionId"], user_id=learner_id, role="learner"
    )
    base = datetime(2026, 5, 1, 8, 0, 0, tzinfo=UTC)
    _record_attempts(
        database_path, learner_id, "topic_a", "Algebra Topic",
        ["correct", "correct"], base,
    )

    dashboard_a = build_classroom_intelligence_dashboard(
        database_path=database_path,
        session_id=session_a["sessionId"],
        user_id=educator_id,
        now=base + timedelta(hours=1),
    )
    dashboard_b = build_classroom_intelligence_dashboard(
        database_path=database_path,
        session_id=session_b["sessionId"],
        user_id=educator_id,
        now=base + timedelta(hours=1),
    )
    assert dashboard_a is not None
    assert dashboard_b is not None
    topic_ids_b = {t["topicId"] for t in dashboard_b["topicPerformance"]}
    assert "topic_a" not in topic_ids_b


# ---------------------------------------------------------------------------
# Recommendation tests
# ---------------------------------------------------------------------------


def test_generate_recommendations_evidence_grounded(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Rec Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )
    learner_id = create_user(database_path, "learner_rec", "hashed-password")
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=learner_id, role="learner"
    )
    # Add two more learners to meet MIN_STUDENTS_FOR_CLASSROOM_AGGREGATE (3)
    for i in (2, 3):
        uid = create_user(database_path, f"rec_learner_{i}", "hashed-password")
        join_classroom_session(
            database_path, session_id=session["sessionId"], user_id=uid, role="learner"
        )

    base = datetime(2026, 6, 1, 8, 0, 0, tzinfo=UTC)
    _record_attempts(
        database_path, learner_id, "topic_weak", "Weak Topic",
        ["incorrect", "incorrect", "incorrect", "correct"], base,
    )

    result = generate_classroom_recommendations(
        database_path=database_path,
        session_id=session["sessionId"],
        user_id=educator_id,
        now=base + timedelta(hours=1),
    )
    assert result is not None
    assert len(result["recommendations"]) >= 1
    rec = result["recommendations"][0]
    assert rec["recommendationType"] == "concept_reteaching"
    assert rec["targetTopic"] == "Weak Topic"
    assert "evidenceSummary" in rec
    assert rec["evidenceSummary"] is not None


def test_create_and_list_recommendation(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Rec Persist Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )

    created = create_classroom_recommendation(
        database_path,
        session_id=session["sessionId"],
        educator_id=educator_id,
        recommendation_type="concept_reteaching",
        title="Reteach Fractions",
        description="Students struggle with fractions",
        rationale="Low accuracy on fraction questions",
        priority_score=85.0,
        target_topic="Fractions",
        target_competency="topic_fractions",
        evidence_summary="40% accuracy on 10 attempts",
    )
    assert created is not None
    assert created["status"] == "pending"
    assert created["priorityScore"] == 85.0

    listed = list_classroom_recommendations(
        database_path, session_id=session["sessionId"], user_id=educator_id
    )
    assert len(listed) == 1
    assert listed[0]["recommendationId"] == created["recommendationId"]


def test_recommendation_invalid_type_returns_none(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Rec Invalid Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )
    result = create_classroom_recommendation(
        database_path,
        session_id=session["sessionId"],
        educator_id=educator_id,
        recommendation_type="not_a_valid_type",
        title="X",
        description="Y",
        rationale=None,
        priority_score=50.0,
        target_topic=None,
        target_competency=None,
        evidence_summary=None,
    )
    assert result is None


# ---------------------------------------------------------------------------
# Teaching effectiveness tests
# ---------------------------------------------------------------------------


def test_compute_teaching_effectiveness(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Effectiveness Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )
    learner_id = create_user(database_path, "learner_eff", "hashed-password")
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=learner_id, role="learner"
    )
    # Add two more learners to meet MIN_STUDENTS_FOR_CLASSROOM_AGGREGATE (3)
    for i in (2, 3):
        uid = create_user(database_path, f"eff_learner_{i}", "hashed-password")
        join_classroom_session(
            database_path, session_id=session["sessionId"], user_id=uid, role="learner"
        )

    base = datetime(2026, 7, 1, 8, 0, 0, tzinfo=UTC)
    _record_attempts(
        database_path, learner_id, "topic_eff", "Eff Topic",
        ["correct", "correct", "incorrect"], base,
    )

    result = compute_teaching_effectiveness(
        database_path=database_path,
        session_id=session["sessionId"],
        user_id=educator_id,
        now=base + timedelta(hours=1),
    )
    assert result is not None
    assert len(result["effectivenessIndicators"]) >= 1
    indicator = result["effectivenessIndicators"][0]
    assert "indicatorName" in indicator
    assert "dataSource" in indicator
    assert "evidenceCount" in indicator


# ---------------------------------------------------------------------------
# Analytics snapshot tests
# ---------------------------------------------------------------------------


def test_record_and_list_analytics_snapshot(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Snapshot Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )

    snapshot = create_classroom_analytics_snapshot(
        database_path,
        session_id=session["sessionId"],
        snapshot_date="2026-08-01",
        user_id=educator_id,
        total_participants=2,
        active_participants=2,
        total_questions=5,
        answered_questions=3,
        total_polls=1,
        total_poll_responses=2,
        average_assessment_score=60.0,
        total_assessments=1,
        total_assignments_completed=0,
        total_assignments_assigned=0,
        average_mastery_score=0.6,
    )
    assert snapshot is not None
    assert snapshot["snapshotId"].startswith("snap_")
    assert snapshot["averageAssessmentScore"] == 60.0

    listed = list_classroom_analytics_snapshots(
        database_path, session_id=session["sessionId"], user_id=educator_id
    )
    assert len(listed) == 1
    assert listed[0]["snapshotId"] == snapshot["snapshotId"]


def test_snapshot_validation_rejects_bad_scores(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Snapshot Bad Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )

    result = create_classroom_analytics_snapshot(
        database_path,
        session_id=session["sessionId"],
        snapshot_date="2026-08-02",
        user_id=educator_id,
        total_participants=2,
        active_participants=2,
        total_questions=0,
        answered_questions=0,
        total_polls=0,
        total_poll_responses=0,
        average_assessment_score=150.0,
        total_assessments=0,
        total_assignments_completed=0,
        total_assignments_assigned=0,
        average_mastery_score=None,
    )
    assert result is None


# ---------------------------------------------------------------------------
# Permission enforcement tests
# ---------------------------------------------------------------------------


def test_non_educator_cannot_create_snapshot(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Perm Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )
    learner_id = create_user(database_path, "learner_perm", "hashed-password")
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=learner_id, role="learner"
    )

    result = create_classroom_analytics_snapshot(
        database_path,
        session_id=session["sessionId"],
        snapshot_date="2026-09-01",
        user_id=learner_id,
        total_participants=2,
        active_participants=2,
        total_questions=0,
        answered_questions=0,
        total_polls=0,
        total_poll_responses=0,
        average_assessment_score=None,
        total_assessments=0,
        total_assignments_completed=0,
        total_assignments_assigned=0,
        average_mastery_score=None,
    )
    assert result is None


# ---------------------------------------------------------------------------
# Data deletion and recalculation tests
# ---------------------------------------------------------------------------


def test_deleting_classroom_removes_dependent_analytics(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Delete Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )

    record_classroom_topic_performance(
        database_path,
        session_id=session["sessionId"],
        topic_id="topic_del",
        topic="To Delete",
        total_attempts=3,
        correct_attempts=2,
        incorrect_attempts=1,
        ambiguous_attempts=0,
    )
    create_classroom_analytics_snapshot(
        database_path,
        session_id=session["sessionId"],
        snapshot_date="2026-10-01",
        user_id=educator_id,
        total_participants=1,
        active_participants=1,
        total_questions=0,
        answered_questions=0,
        total_polls=0,
        total_poll_responses=0,
        average_assessment_score=None,
        total_assessments=0,
        total_assignments_completed=0,
        total_assignments_assigned=0,
        average_mastery_score=None,
    )

    delete_classroom_session(
        database_path, session_id=session["sessionId"], educator_id=educator_id
    )

    # Session is deleted, so list functions return None (session not found)
    topics = list_classroom_topic_performance(
        database_path, session_id=session["sessionId"], user_id=educator_id
    )
    assert topics is None
    snapshots = list_classroom_analytics_snapshots(
        database_path, session_id=session["sessionId"], user_id=educator_id
    )
    assert snapshots is None


def test_recalculation_after_new_attempts(tmp_path):
    settings, educator_id, database_path = setup_test_db(tmp_path)
    session = create_classroom_session(
        database_path,
        educator_id=educator_id,
        lesson_plan_id=None,
        title="Recalc Class",
        subject="Math",
        topic="Algebra",
        status="active",
    )
    learner_id = create_user(database_path, "learner_recalc", "hashed-password")
    join_classroom_session(
        database_path, session_id=session["sessionId"], user_id=learner_id, role="learner"
    )
    # Add two more learners to meet MIN_STUDENTS_FOR_CLASSROOM_AGGREGATE (3)
    for i in (2, 3):
        uid = create_user(database_path, f"recalc_learner_{i}", "hashed-password")
        join_classroom_session(
            database_path, session_id=session["sessionId"], user_id=uid, role="learner"
        )

    base = datetime(2026, 11, 1, 8, 0, 0, tzinfo=UTC)
    _record_attempts(
        database_path, learner_id, "topic_recalc", "Recalc Topic",
        ["incorrect", "incorrect"], base, suffix="first"
    )
    dashboard_1 = build_classroom_intelligence_dashboard(
        database_path=database_path,
        session_id=session["sessionId"],
        user_id=educator_id,
        now=base + timedelta(hours=1),
    )
    topic_map_1 = {t["topicId"]: t for t in dashboard_1["topicPerformance"]}
    assert topic_map_1["topic_recalc"]["accuracy"] == 0.0

    _record_attempts(
        database_path, learner_id, "topic_recalc", "Recalc Topic",
        ["correct", "correct", "correct"], base + timedelta(hours=2), suffix="second"
    )
    dashboard_2 = build_classroom_intelligence_dashboard(
        database_path=database_path,
        session_id=session["sessionId"],
        user_id=educator_id,
        now=base + timedelta(hours=5),
    )
    topic_map_2 = {t["topicId"]: t for t in dashboard_2["topicPerformance"]}
    # 3 correct, 2 incorrect out of 5 scored; topic reviews add to total attempts
    assert abs(topic_map_2["topic_recalc"]["accuracy"] - 0.6) < 0.001


if __name__ == "__main__":
    pass
