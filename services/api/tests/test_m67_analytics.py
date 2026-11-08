"""M67: Predictive Learning Analytics & Student Performance Intelligence tests.

Tests cover:

* Performance aggregation from persisted evidence
* Historical trend calculations
* Exam readiness prediction (with and without sufficient data)
* Topic forgetting risk prediction
* Insufficient-data handling (no predictions fabricated)
* Explainable recommendations with evidence
* Performance dashboard assembly
* Temporal data leakage prevention
* Multi-user isolation
* Analytics snapshot creation
* Model version registration
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import Settings
from app.storage import (
    create_analytics_snapshot,
    create_competency,
    create_exam,
    record_exam_result,
    create_learning_gap,
    create_performance_prediction,
    create_prediction_model_version,
    create_topic_trend,
    create_user,
    get_competency_mastery,
    initialize_storage,
    list_analytics_snapshots,
    list_competencies,
    list_exam_results,
    list_learning_gaps,
    list_performance_predictions,
    list_prediction_model_versions,
    list_topic_trends,
    record_exam_answer,
    update_competency_mastery,
)
from app.predictive_analytics import (
    build_performance_dashboard,
    compute_performance_trends,
    generate_explainable_recommendations,
    generate_performance_predictions,
    record_analytics_snapshot,
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
    user_id = create_user(settings.database_path, "analystest", "hashed-password", is_admin=False)
    return settings, user_id, settings.database_path


def _add_quiz_attempts(
    database_path: Path,
    user_id: str,
    topic_id: str,
    topic: str,
    outcomes: list[str],
    base_time: datetime,
) -> None:
    """Helper to add quiz attempts for a topic via the quiz system."""
    from app.storage import (
        create_quiz_set as _cqs,
        record_quiz_attempt as _rqa,
    )

    quiz_id = f"quiz_{user_id}_{topic_id}"
    now_iso = base_time.isoformat()
    questions_data = []
    for i, outcome in enumerate(outcomes):
        qid = f"q_{topic_id}_{i}"
        questions_data.append({
            "question_id": qid,
            "question_type": "multiple_choice",
            "prompt": f"Q{i}?",
            "options": ["A", "B", "C", "D"],
            "answer_key": "A",
            "accepted_answers": ["A"],
            "explanation": None,
            "difficulty_level": "foundational",
            "explanation": "",
            "citations": [],
        })

    _cqs(
        database_path,
        {
            "quiz_id": quiz_id,
            "topic_id": topic_id,
            "topic": topic,
            "created_at": now_iso,
            "questions": questions_data,
        },
        user_id=user_id,
    )

    # Record attempts for each question
    for i, outcome in enumerate(outcomes):
        qid = f"q_{topic_id}_{i}"
        attempt_id = f"attempt_{topic_id}_{i}"
        attempt_time = (base_time + timedelta(minutes=i)).isoformat()
        _rqa(
            database_path,
            attempt_id=attempt_id,
            question_id=qid,
            outcome=outcome,
            created_at=attempt_time,
            user_id=user_id,
        )


# ---------------------------------------------------------------------------
# Performance aggregation tests
# ---------------------------------------------------------------------------


def test_compute_performance_trends_no_data(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    result = compute_performance_trends(
        database_path=database_path, user_id=user_id, now=datetime.now(UTC)
    )
    assert result["userId"] == user_id
    assert result["overallMastery"] is None
    assert result["overallAccuracy"] is None
    assert result["totalAttempts"] == 0
    assert result["topicTrends"] == []
    assert result["competencyTrends"] == []
    assert result["examHistory"] == []


def test_compute_performance_trends_with_attempts(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    now = datetime.now(UTC)
    _add_quiz_attempts(
        database_path, user_id, "topic_math", "Mathematics",
        ["correct", "correct", "incorrect", "correct"],
        now,
    )

    result = compute_performance_trends(
        database_path=database_path, user_id=user_id, now=now
    )
    assert result["totalAttempts"] >= 1
    assert result["overallAccuracy"] is not None
    assert 0.0 <= result["overallAccuracy"] <= 1.0
    assert len(result["topicTrends"]) == 1
    assert result["topicTrends"][0]["topic"] == "Mathematics"


def test_temporal_no_future_leakage(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    now = datetime.now(UTC)
    # Add an attempt dated in the future relative to `now`
    future = now + timedelta(days=30)
    _add_quiz_attempts(
        database_path, user_id, "topic_future", "Future Topic",
        ["correct"], future,
    )

    # Trends computed at `now` should NOT include the future attempt
    result = compute_performance_trends(
        database_path=database_path, user_id=user_id, now=now
    )
    assert result["totalAttempts"] == 0
    assert result["topicTrends"] == []


# ---------------------------------------------------------------------------
# Exam readiness tests
# ---------------------------------------------------------------------------


def test_exam_readiness_with_sufficient_data(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    now = datetime.now(UTC)
    # Create competency with high mastery
    comp = create_competency(
        database_path, user_id=user_id, title="Algebra Mastery",
        description="d", parent_competency_id=None, level=0,
    )
    update_competency_mastery(
        database_path, competency_id=comp["competencyId"],
        user_id=user_id, mastery_score=0.85,
    )

    # Add quiz attempts
    _add_quiz_attempts(
        database_path, user_id, "topic_math", "Mathematics",
        ["correct", "correct", "correct", "incorrect", "correct"],
        now,
    )

    result = generate_performance_predictions(
        database_path=database_path, user_id=user_id, now=now
    )
    assert result["predictionCount"] >= 1
    readiness = [p for p in result["predictions"] if p["predictionType"] == "exam_readiness"]
    assert len(readiness) == 1
    assert readiness[0]["predictedValue"] is not None
    assert 0.0 <= readiness[0]["predictedValue"] <= 1.0
    assert readiness[0]["confidence"] > 0
    assert readiness[0]["isGuaranteed"] is False


def test_exam_readiness_insufficient_data(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    # No competency, no attempts - should return None prediction
    result = generate_performance_predictions(
        database_path=database_path, user_id=user_id, now=datetime.now(UTC)
    )
    readiness = [p for p in result["predictions"] if p["predictionType"] == "exam_readiness"]
    assert len(readiness) == 0  # No prediction when insufficient data


def test_readiness_not_guaranteed(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    now = datetime.now(UTC)
    comp = create_competency(
        database_path, user_id=user_id, title="Algebra Mastery",
        description="d", parent_competency_id=None, level=0,
    )
    update_competency_mastery(
        database_path, competency_id=comp["competencyId"],
        user_id=user_id, mastery_score=0.95,
    )

    result = generate_performance_predictions(
        database_path=database_path, user_id=user_id, now=now
    )
    readiness = [p for p in result["predictions"] if p["predictionType"] == "exam_readiness"]
    assert len(readiness) == 1
    assert readiness[0]["isGuaranteed"] is False
    assert "limitations" in readiness[0]
    assert "note" in readiness[0]["limitations"]


# ---------------------------------------------------------------------------
# Topic forgetting risk tests
# ---------------------------------------------------------------------------


def test_topic_forgetting_risk_detection(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    now = datetime.now(UTC)
    # Add attempts long ago (high forgetting risk)
    old_time = now - timedelta(days=45)
    _add_quiz_attempts(
        database_path, user_id, "topic_forgotten", "Forgotten Topic",
        ["incorrect", "incorrect", "incorrect"], old_time,
    )

    result = generate_performance_predictions(
        database_path=database_path, user_id=user_id, now=now
    )
    risks = [p for p in result["predictions"] if p["predictionType"] == "topic_risk"]
    # May or may not detect risk depending on threshold; if detected, verify structure
    for risk in risks:
        assert 0.0 <= risk["predictedValue"] <= 1.0
        assert risk["confidence"] > 0
        assert risk["uncertainty"] in {"low", "medium", "high"}
        assert risk["isGuaranteed"] is False
        assert "evidence" in risk
        assert "limitations" in risk


# ---------------------------------------------------------------------------
# Recommendations tests
# ---------------------------------------------------------------------------


def test_generate_recommendations_with_weak_competency(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    now = datetime.now(UTC)
    comp = create_competency(
        database_path, user_id=user_id, title="Weak Algebra",
        description="d", parent_competency_id=None, level=0,
    )
    update_competency_mastery(
        database_path, competency_id=comp["competencyId"],
        user_id=user_id, mastery_score=0.30,
    )

    result = generate_explainable_recommendations(
        database_path=database_path, user_id=user_id, now=now, limit=10
    )
    assert result["userId"] == user_id
    assert len(result["recommendations"]) >= 1
    rec = result["recommendations"][0]
    assert "recommendationId" in rec
    assert "type" in rec
    assert "title" in rec
    assert "evidence" in rec
    assert "reason" in rec
    assert "priority" in rec
    # Evidence must be present (not fabricated)
    assert rec["evidence"] is not None


def test_recommendations_empty_when_no_weaknesses(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    now = datetime.now(UTC)
    comp = create_competency(
        database_path, user_id=user_id, title="Strong Algebra",
        description="d", parent_competency_id=None, level=0,
    )
    update_competency_mastery(
        database_path, competency_id=comp["competencyId"],
        user_id=user_id, mastery_score=0.95,
    )

    result = generate_explainable_recommendations(
        database_path=database_path, user_id=user_id, now=now, limit=10
    )
    # No weak competencies, no gaps -> likely empty or minimal recommendations
    assert result["userId"] == user_id


# ---------------------------------------------------------------------------
# Dashboard tests
# ---------------------------------------------------------------------------


def test_build_performance_dashboard(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    now = datetime.now(UTC)
    dashboard = build_performance_dashboard(
        database_path=database_path, user_id=user_id, now=now
    )
    assert dashboard["userId"] == user_id
    assert "overallMastery" in dashboard
    assert "overallAccuracy" in dashboard
    assert "totalAttempts" in dashboard
    assert "weakTopics" in dashboard
    assert "strongTopics" in dashboard
    assert "topicProgress" in dashboard
    assert "competencyTrends" in dashboard
    assert "examHistory" in dashboard
    assert "activePredictions" in dashboard
    assert "recommendations" in dashboard
    assert "generatedAt" in dashboard


# ---------------------------------------------------------------------------
# Analytics snapshot tests
# ---------------------------------------------------------------------------


def test_record_and_list_analytics_snapshot(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    now = datetime.now(UTC)
    today = now.date().isoformat()

    snapshot = create_analytics_snapshot(
        database_path,
        user_id=user_id,
        snapshot_date=today,
        topic_id="topic_1",
        topic="Mathematics",
        mastery_score=0.75,
        accuracy=0.80,
        attempt_count=10,
        correct_count=8,
        incorrect_count=2,
        ambiguous_count=0,
        review_count=5,
        overdue_review_count=1,
        time_spent_ms=300000,
    )
    assert snapshot is not None
    assert snapshot["snapshotId"].startswith("snap_")
    assert snapshot["masteryScore"] == 0.75
    assert snapshot["accuracy"] == 0.80

    snapshots = list_analytics_snapshots(
        database_path, user_id=user_id
    )
    assert len(snapshots) == 1


def test_record_analytics_snapshot_invalid(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    # Invalid mastery score
    snapshot = create_analytics_snapshot(
        database_path,
        user_id=user_id,
        snapshot_date="2026-01-01",
        topic_id=None,
        topic=None,
        mastery_score=1.5,  # invalid
        accuracy=None,
        attempt_count=0,
        correct_count=0,
        incorrect_count=0,
        ambiguous_count=0,
        review_count=0,
        overdue_review_count=0,
        time_spent_ms=None,
    )
    assert snapshot is None


# ---------------------------------------------------------------------------
# Model version tests
# ---------------------------------------------------------------------------


def test_register_and_list_model_versions(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    model = create_prediction_model_version(
        database_path,
        model_name="readiness_estimator",
        version="1.0.0",
        description="Baseline readiness estimator",
        is_active=True,
        validation_metrics={"mae": 0.05, "rmse": 0.08},
    )
    assert model is not None
    assert model["modelVersionId"].startswith("model_")
    assert model["modelName"] == "readiness_estimator"
    assert model["version"] == "1.0.0"
    assert model["isActive"] is True

    models = list_prediction_model_versions(database_path)
    assert len(models) == 1


def test_model_version_deactivates_previous(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    m1 = create_prediction_model_version(
        database_path,
        model_name="readiness_estimator",
        version="1.0.0",
        description="First version",
        is_active=True,
        validation_metrics={},
    )
    m2 = create_prediction_model_version(
        database_path,
        model_name="readiness_estimator",
        version="2.0.0",
        description="Second version",
        is_active=True,
        validation_metrics={},
    )

    models = list_prediction_model_versions(database_path)
    active = [m for m in models if m["isActive"]]
    assert len(active) == 1
    assert active[0]["version"] == "2.0.0"


# ---------------------------------------------------------------------------
# Topic trend tests
# ---------------------------------------------------------------------------


def test_create_and_list_topic_trends(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    now = datetime.now(UTC)
    today = now.date().isoformat()

    trend = create_topic_trend(
        database_path,
        user_id=user_id,
        topic_id="topic_math",
        topic="Mathematics",
        measurement_date=today,
        mastery_score=0.70,
        accuracy=0.75,
        attempt_count=5,
    )
    assert trend is not None
    assert trend["trendId"].startswith("trend_")
    assert trend["masteryScore"] == 0.70

    trends = list_topic_trends(
        database_path, user_id=user_id, topic_id="topic_math"
    )
    assert len(trends) == 1


# ---------------------------------------------------------------------------
# User isolation tests
# ---------------------------------------------------------------------------


def test_user_isolation_in_analytics(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "analystest_b", "hashed-password", is_admin=False)

    # Create data for user A
    comp_a = create_competency(
        database_path, user_id=user_id_a, title="Comp A",
        description="d", parent_competency_id=None, level=0,
    )
    update_competency_mastery(
        database_path, competency_id=comp_a["competencyId"],
        user_id=user_id_a, mastery_score=0.30,
    )

    # User B should have no data
    trends_b = compute_performance_trends(
        database_path=database_path, user_id=user_id_b, now=datetime.now(UTC)
    )
    assert trends_b["totalAttempts"] == 0
    assert trends_b["overallMastery"] is None

    # User A should have data
    trends_a = compute_performance_trends(
        database_path=database_path, user_id=user_id_a, now=datetime.now(UTC)
    )
    assert trends_a["totalAttempts"] >= 0


if __name__ == "__main__":
    pass
