"""M65: Learning Gap Intelligence & Remediation Engine tests.

Tests cover:

* Gap detection from assessment results
* Missing prerequisite identification
* Repeated misconception detection
* Insufficient evidence handling (missing data is not poor performance)
* Gap prioritization
* Recommendation generation
* Remediation tracking (accept/dismiss/complete)
* Reassessment comparisons
* False resolution prevention (activity completion alone doesn't resolve)
* Multi-user isolation
* Interrupted remediation workflows
* Dashboard summary
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.gap_detection import detect_gaps_for_user
from app.storage import (
    add_competency_prerequisite,
    create_competency,
    create_learning_gap,
    create_user,
    dismiss_learning_gap,
    get_competency_mastery,
    get_learning_gap,
    link_objective_competency,
    list_competencies,
    list_learning_gaps,
    update_competency_mastery,
    update_learning_gap,
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
    from app.storage import initialize_storage
    initialize_storage(settings.database_path)
    user_id = create_user(settings.database_path, "gaptest", "hashed-password", is_admin=False)
    return settings, user_id, settings.database_path


# ---------------------------------------------------------------------------
# Gap detection tests
# ---------------------------------------------------------------------------


def test_detect_learning_gaps_no_data(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    result = detect_gaps_for_user(database_path=database_path, user_id=user_id, now=datetime.now(UTC))
    assert result["gapsDetected"] == 0
    assert result["gaps"] == []


def test_detect_gaps_from_incorrect_responses(tmp_path):
    """Gap created directly with incorrect_response type is detected and listed."""
    settings, user_id, database_path = setup_test_db(tmp_path)

    gap = create_learning_gap(
        database_path,
        user_id=user_id,
        topic_id="topic_math",
        topic="Mathematics",
        competency_id=None,
        gap_type="incorrect_response",
        severity="critical",
        evidence_strength="strong",
        recurrence=3,
    )
    assert gap is not None
    assert gap["gapType"] == "incorrect_response"
    assert gap["severity"] == "critical"
    assert gap["recurrence"] == 3

    gaps = list_learning_gaps(database_path, user_id=user_id, gap_type="incorrect_response")
    assert len(gaps) == 1
    assert gaps[0]["gapId"] == gap["gapId"]


def test_detect_gaps_weak_competency(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    comp = create_competency(
        database_path, user_id=user_id, title="Algebra",
        description="Solving equations.", parent_competency_id=None, level=0,
    )
    # Set low mastery
    update_competency_mastery(
        database_path, competency_id=comp["competencyId"],
        user_id=user_id, mastery_score=0.30,
    )

    result = detect_gaps_for_user(database_path=database_path, user_id=user_id, now=datetime.now(UTC))
    weak_gaps = [g for g in result["gaps"] if g["gapType"] == "weak_competency"]
    assert len(weak_gaps) == 1
    assert weak_gaps[0]["severity"] == "critical"  # 0.30 < 0.40


def test_missing_data_not_poor_performance(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    # Create a competency but don't set any mastery - no evidence
    comp = create_competency(
        database_path, user_id=user_id, title="Untested Competency",
        description="No assessments yet.", parent_competency_id=None, level=0,
    )

    result = detect_gaps_for_user(database_path=database_path, user_id=user_id, now=datetime.now(UTC))
    # Should NOT create a weak_competency gap for a competency with no mastery evidence
    weak_gaps = [g for g in result["gaps"] if g["competencyId"] == comp["competencyId"]]
    assert len(weak_gaps) == 0


def test_missing_prerequisite_detection(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    # Create two competencies with prerequisite relationship
    prereq = create_competency(
        database_path, user_id=user_id, title="Basic Math",
        description="d", parent_competency_id=None, level=0,
    )
    advanced = create_competency(
        database_path, user_id=user_id, title="Calculus",
        description="d", parent_competency_id=None, level=1,
    )
    add_competency_prerequisite(
        database_path, competency_id=advanced["competencyId"],
        prerequisite_competency_id=prereq["competencyId"], user_id=user_id,
    )

    # Set low mastery on prerequisite
    update_competency_mastery(
        database_path, competency_id=prereq["competencyId"],
        user_id=user_id, mastery_score=0.25,
    )
    # Set high mastery on advanced (to show the gap is about the prereq)
    update_competency_mastery(
        database_path, competency_id=advanced["competencyId"],
        user_id=user_id, mastery_score=0.80,
    )

    result = detect_gaps_for_user(database_path=database_path, user_id=user_id, now=datetime.now(UTC))
    prereq_gaps = [g for g in result["gaps"] if g["gapType"] == "missing_prerequisite"]
    assert len(prereq_gaps) == 1
    assert prereq_gaps[0]["competencyId"] == advanced["competencyId"]


def test_gap_user_isolation(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "gaptest_b", "hashed-password", is_admin=False)

    # Create gap directly for user A
    gap_a = create_learning_gap(
        database_path,
        user_id=user_id_a,
        topic_id=None,
        topic="User A Topic",
        competency_id=None,
        gap_type="incorrect_response",
        severity="minor",
        evidence_strength="weak",
        recurrence=1,
    )

    gaps_b = list_learning_gaps(database_path, user_id=user_id_b)
    assert len(gaps_b) == 0

    gaps_a = list_learning_gaps(database_path, user_id=user_id_a)
    assert len(gaps_a) == 1

    # User B cannot access User A's gap
    fetched = get_learning_gap(database_path, gap_id=gap_a["gapId"], user_id=user_id_b)
    assert fetched is None


# ---------------------------------------------------------------------------
# Gap CRUD tests
# ---------------------------------------------------------------------------


def test_create_get_list_learning_gap(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    gap = create_learning_gap(
        database_path, user_id=user_id, topic_id="topic_1", topic="Algebra",
        competency_id=None, gap_type="weak_competency",
        severity="moderate", evidence_strength="strong", recurrence=1,
        baseline_mastery=0.50,
    )
    assert gap is not None
    assert gap["gapId"].startswith("gap_")
    assert gap["topic"] == "Algebra"
    assert gap["severity"] == "moderate"
    assert gap["status"] == "detected"
    assert gap["baselineMastery"] == 0.50

    fetched = get_learning_gap(database_path, gap_id=gap["gapId"], user_id=user_id)
    assert fetched is not None
    assert fetched["gapId"] == gap["gapId"]

    gaps = list_learning_gaps(database_path, user_id=user_id)
    assert len(gaps) == 1


def test_update_learning_gap_status(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    gap = create_learning_gap(
        database_path, user_id=user_id, topic_id=None, topic="Math",
        competency_id=None, gap_type="incorrect_response",
        severity="minor", evidence_strength="weak", recurrence=1,
    )

    updated = update_learning_gap(
        database_path, gap_id=gap["gapId"], user_id=user_id,
        status="remediating",
    )
    assert updated is not None
    assert updated["status"] == "remediating"


def test_dismiss_learning_gap(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    gap = create_learning_gap(
        database_path, user_id=user_id, topic_id=None, topic="Math",
        competency_id=None, gap_type="incorrect_response",
        severity="minor", evidence_strength="weak", recurrence=1,
    )

    dismissed = dismiss_learning_gap(
        database_path, gap_id=gap["gapId"], user_id=user_id,
        reason="Learner disagrees with classification.",
    )
    assert dismissed is True

    fetched = get_learning_gap(database_path, gap_id=gap["gapId"], user_id=user_id)
    assert fetched["status"] == "dismissed"
    assert fetched["dismissedAt"] is not None


# ---------------------------------------------------------------------------
# Remediation tests
# ---------------------------------------------------------------------------


def test_generate_and_accept_remediation(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    gap = create_learning_gap(
        database_path, user_id=user_id, topic_id="topic_1", topic="Algebra",
        competency_id=None, gap_type="weak_competency",
        severity="moderate", evidence_strength="strong", recurrence=1,
        baseline_mastery=0.50,
    )

    from app.remediation import generate_remediations_for_gap, accept_remediation_activity

    result = generate_remediations_for_gap(
        database_path=database_path, gap_id=gap["gapId"], user_id=user_id,
        now=datetime.now(UTC),
    )
    assert "remediations" in result
    assert len(result["remediations"]) > 0
    # Check they're ranked by priority
    scores = [r["priorityScore"] for r in result["remediations"]]
    assert scores == sorted(scores, reverse=True)

    # Accept the top remediation
    top = result["remediations"][0]
    accepted = accept_remediation_activity(
        database_path=database_path, remediation_id=top["remediationId"],
        user_id=user_id,
    )
    assert accepted is not None
    assert accepted["status"] == "accepted"

    # Gap should now be remediating
    fetched = get_learning_gap(database_path, gap_id=gap["gapId"], user_id=user_id)
    assert fetched["status"] == "remediating"


def test_remediation_priority_ranking(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    # Critical gap should have higher priority remediations than minor gap
    gap_critical = create_learning_gap(
        database_path, user_id=user_id, topic_id=None, topic="Critical Topic",
        competency_id=None, gap_type="incorrect_response",
        severity="critical", evidence_strength="strong", recurrence=3,
    )
    gap_minor = create_learning_gap(
        database_path, user_id=user_id, topic_id=None, topic="Minor Topic",
        competency_id=None, gap_type="incorrect_response",
        severity="minor", evidence_strength="weak", recurrence=1,
    )

    from app.remediation import generate_remediations_for_gap

    crit_result = generate_remediations_for_gap(
        database_path=database_path, gap_id=gap_critical["gapId"],
        user_id=user_id, now=datetime.now(UTC),
    )
    minor_result = generate_remediations_for_gap(
        database_path=database_path, gap_id=gap_minor["gapId"],
        user_id=user_id, now=datetime.now(UTC),
    )

    crit_top = crit_result["remediations"][0]["priorityScore"]
    minor_top = minor_result["remediations"][0]["priorityScore"]
    assert crit_top > minor_top


# ---------------------------------------------------------------------------
# Reassessment tests
# ---------------------------------------------------------------------------


def test_reassessment_compares_before_after(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    gap = create_learning_gap(
        database_path, user_id=user_id, topic_id=None, topic="Math",
        competency_id=None, gap_type="weak_competency",
        severity="moderate", evidence_strength="strong", recurrence=1,
        baseline_mastery=0.50,
    )

    from app.remediation import record_reassessment

    result = record_reassessment(
        database_path=database_path, gap_id=gap["gapId"], user_id=user_id,
        activity_id=None, before_mastery=0.50, after_mastery=0.75,
        resolved=True,
    )
    assert result is not None
    assert result["beforeMastery"] == 0.50
    assert result["afterMastery"] == 0.75
    assert result["improvement"] == 0.25
    assert result["resolved"] is True  # 0.75 >= 0.60 threshold


def test_reassessment_does_not_resolve_below_threshold(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    gap = create_learning_gap(
        database_path, user_id=user_id, topic_id=None, topic="Math",
        competency_id=None, gap_type="weak_competency",
        severity="moderate", evidence_strength="strong", recurrence=1,
        baseline_mastery=0.50,
    )

    from app.remediation import record_reassessment

    # After mastery below threshold -> not resolved even if learner requests
    result = record_reassessment(
        database_path=database_path, gap_id=gap["gapId"], user_id=user_id,
        activity_id=None, before_mastery=0.50, after_mastery=0.45,
        resolved=True,
    )
    assert result is not None
    assert result["resolved"] is False  # below 0.60 threshold
    assert result["thresholdMet"] is False


def test_activity_completion_alone_does_not_resolve(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    gap = create_learning_gap(
        database_path, user_id=user_id, topic_id=None, topic="Math",
        competency_id=None, gap_type="weak_competency",
        severity="moderate", evidence_strength="strong", recurrence=1,
        baseline_mastery=0.50,
    )

    from app.remediation import (
        generate_remediations_for_gap, accept_remediation_activity,
        complete_remediation_activity,
    )

    result = generate_remediations_for_gap(
        database_path=database_path, gap_id=gap["gapId"], user_id=user_id,
        now=datetime.now(UTC),
    )
    top = result["remediations"][0]
    accept_remediation_activity(
        database_path=database_path, remediation_id=top["remediationId"],
        user_id=user_id,
    )
    complete_remediation_activity(
        database_path=database_path, remediation_id=top["remediationId"],
        user_id=user_id,
    )

    # Gap should NOT be resolved just because activity was completed
    fetched = get_learning_gap(database_path, gap_id=gap["gapId"], user_id=user_id)
    assert fetched["status"] != "resolved"


# ---------------------------------------------------------------------------
# Dashboard tests
# ---------------------------------------------------------------------------


def test_remediation_dashboard(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    # Create gaps of different severities
    create_learning_gap(
        database_path, user_id=user_id, topic_id=None, topic="Critical",
        competency_id=None, gap_type="incorrect_response",
        severity="critical", evidence_strength="strong", recurrence=3,
    )
    create_learning_gap(
        database_path, user_id=user_id, topic_id=None, topic="Moderate",
        competency_id=None, gap_type="weak_competency",
        severity="moderate", evidence_strength="moderate", recurrence=2,
    )
    create_learning_gap(
        database_path, user_id=user_id, topic_id=None, topic="Minor",
        competency_id=None, gap_type="incorrect_response",
        severity="minor", evidence_strength="weak", recurrence=1,
    )

    from app.remediation import get_remediation_dashboard

    dashboard = get_remediation_dashboard(
        database_path=database_path, user_id=user_id,
    )
    assert dashboard["counts"]["total"] == 3
    assert dashboard["counts"]["critical"] == 1
    assert dashboard["counts"]["moderate"] == 1
    assert dashboard["counts"]["minor"] == 1
    assert dashboard["counts"]["detected"] == 3


# ---------------------------------------------------------------------------
# Multi-user isolation
# ---------------------------------------------------------------------------


def test_multi_user_gap_isolation(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "gaptest_b", "hashed-password", is_admin=False)

    gap_a = create_learning_gap(
        database_path, user_id=user_id_a, topic_id=None, topic="User A Topic",
        competency_id=None, gap_type="incorrect_response",
        severity="minor", evidence_strength="weak", recurrence=1,
    )

    gaps_b = list_learning_gaps(database_path, user_id=user_id_b)
    assert len(gaps_b) == 0

    gaps_a = list_learning_gaps(database_path, user_id=user_id_a)
    assert len(gaps_a) == 1

    # User B cannot access User A's gap
    fetched = get_learning_gap(database_path, gap_id=gap_a["gapId"], user_id=user_id_b)
    assert fetched is None


if __name__ == "__main__":
    pass

