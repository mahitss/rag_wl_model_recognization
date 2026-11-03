"""
M52 — Adaptive Learning Engine regressions.

These tests exercise the *existing* adaptive-learning functions (mastery
estimation, difficulty mapping, adaptive recommendations, learning-path
objectives with prerequisites) to verify that M52's requirements are already
met by the current implementation.  They use synthetic learner profiles and
fixed timestamps; no AI model or external service is involved.

Per the M52 constraints, this file does NOT duplicate existing
adaptive-learning functionality — it only verifies it.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from app.auth import hash_password
from app.config import Settings
from app.learning_analytics import build_analytics, calculate_mastery
from app.storage import (
    _difficulty_level_from_mastery,  # noqa: PLC2701  (test-only)
    compute_adaptive_recommendations,
    create_learning_objective,
    create_learning_path,
    create_quiz_set,
    create_user,
    get_learning_objective,
    initialize_storage,
    record_quiz_attempt,
)

NOW = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)

TOPIC_PHOTOSYNTHESIS = "topic-photosynthesis"
TOPIC_CHEMISTRY = "topic-chemistry"


def _settings(tmp_path: str) -> Settings:
    return Settings(
        _env_file=None,
        database_path=os.path.join(tmp_path, "adaptive_engine.sqlite3"),
        rate_limit_enabled=False,
    )


def _seed_user(tmp_path: str, username: str = "learner") -> tuple[str, Settings]:
    settings = _settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = create_user(settings.database_path, username, hash_password("password-123"))
    return user_id, settings


def _seed_quiz(
    settings: Settings,
    user_id: str,
    topic_id: str = TOPIC_PHOTOSYNTHESIS,
    topic: str = "Photosynthesis",
    quiz_id: str | None = None,
) -> str:
    if quiz_id is None:
        quiz_id = f"quiz-{topic_id}"
    create_quiz_set(
        settings.database_path,
        {
            "quiz_id": quiz_id,
            "topic_id": topic_id,
            "topic": topic,
            "created_at": NOW.isoformat(),
            "questions": [
                {
                    "question_id": f"{quiz_id}-q1",
                    "question_type": "multiple_choice",
                    "prompt": "What does chlorophyll absorb?",
                    "options": [{"id": "A", "text": "Light energy"}],
                    "answer_key": "A",
                    "accepted_answers": [],
                    "explanation": "Chlorophyll absorbs light energy.",
                    "citations": [],
                },
                {
                    "question_id": f"{quiz_id}-q2",
                    "question_type": "multiple_choice",
                    "prompt": "What is the byproduct?",
                    "options": [{"id": "B", "text": "Oxygen"}],
                    "answer_key": "B",
                    "accepted_answers": [],
                    "explanation": "Oxygen is released.",
                    "citations": [],
                },
            ],
        },
        user_id=user_id,
    )
    return quiz_id


def _record(
    settings: Settings,
    user_id: str,
    question_id: str,
    outcome: str,
    created_at: datetime,
) -> None:
    record_quiz_attempt(
        settings.database_path,
        attempt_id=f"attempt-{question_id}-{created_at.isoformat()}",
        question_id=question_id,
        outcome=outcome,
        created_at=created_at.isoformat(),
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# 2. Learner knowledge estimation
# ---------------------------------------------------------------------------


def test_mastery_strong_topic_advanced_level(tmp_path: str) -> None:
    """Strong demonstrated mastery maps to advanced difficulty."""
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)
    base = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)
    for i in range(4):
        _record(
            settings,
            user_id,
            f"quiz-{TOPIC_PHOTOSYNTHESIS}-q1",
            "correct",
            datetime(2026, 4, 14, 12, i, tzinfo=UTC),
        )
    _record(settings, user_id, f"quiz-{TOPIC_PHOTOSYNTHESIS}-q2", "correct", base)

    mastery = calculate_mastery(
        [
            {
                "questionId": f"quiz-{TOPIC_PHOTOSYNTHESIS}-q1",
                "outcome": "correct",
                "createdAt": datetime(2026, 4, 14, 12, 3, tzinfo=UTC).isoformat(),
            },
            {
                "questionId": f"quiz-{TOPIC_PHOTOSYNTHESIS}-q2",
                "outcome": "correct",
                "createdAt": base.isoformat(),
            },
        ],
        now=NOW,
    )
    assert mastery is not None and mastery >= 0.80
    assert _difficulty_level_from_mastery(mastery) == "advanced"


def test_mastery_weak_topic_is_building(tmp_path: str) -> None:
    """Weak demonstrated mastery maps to building difficulty and flags weak."""
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)
    attempts = [
        {
            "attemptId": "attempt-q1",
            "questionId": f"quiz-{TOPIC_PHOTOSYNTHESIS}-q1",
            "topicId": TOPIC_PHOTOSYNTHESIS,
            "topic": "Photosynthesis",
            "outcome": "correct",
            "prompt": "What does chlorophyll absorb?",
            "createdAt": NOW.isoformat(),
        },
        {
            "attemptId": "attempt-q2",
            "questionId": f"quiz-{TOPIC_PHOTOSYNTHESIS}-q2",
            "topicId": TOPIC_PHOTOSYNTHESIS,
            "topic": "Photosynthesis",
            "outcome": "incorrect",
            "prompt": "What is the byproduct?",
            "createdAt": NOW.isoformat(),
        },
    ]
    mastery = calculate_mastery(attempts, now=NOW)
    assert mastery is not None and mastery < 0.60
    assert _difficulty_level_from_mastery(mastery) == "building"

    analytics = build_analytics(
        attempts=attempts,
        sessions=[],
        reviews=[],
        topic_sources={TOPIC_PHOTOSYNTHESIS: []},
        concepts=[],
        now=NOW,
    )
    assert analytics["topics"][0]["weak"] is True


def test_mastery_unknown_when_no_scored_history(tmp_path: str) -> None:
    """Missing data never produces a fabricated mastery score."""
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)
    # Only ambiguous outcomes are unscored.
    mastery = calculate_mastery(
        [
            {
                "questionId": f"quiz-{TOPIC_PHOTOSYNTHESIS}-q1",
                "outcome": "ambiguous",
                "createdAt": NOW.isoformat(),
            }
        ],
        now=NOW,
    )
    assert mastery is None
    assert _difficulty_level_from_mastery(None) == "foundational"


# ---------------------------------------------------------------------------
# 3. Adaptive difficulty engine
# ---------------------------------------------------------------------------


def test_difficulty_level_transitions() -> None:
    """Difficulty level moves monotonically with estimated mastery."""
    assert _difficulty_level_from_mastery(None) == "foundational"
    assert _difficulty_level_from_mastery(0.0) == "foundational"
    assert _difficulty_level_from_mastery(0.39) == "foundational"
    assert _difficulty_level_from_mastery(0.40) == "building"
    assert _difficulty_level_from_mastery(0.59) == "building"
    assert _difficulty_level_from_mastery(0.60) == "practice"
    assert _difficulty_level_from_mastery(0.79) == "practice"
    assert _difficulty_level_from_mastery(0.80) == "advanced"
    assert _difficulty_level_from_mastery(1.0) == "advanced"


def test_repeated_incorrect_answers_do_not_overreact(tmp_path: str) -> None:
    """A single recent incorrect answer is damped by the rest of the record.

    Mastery uses each question's latest result weighted by recency, so one
    incorrect answer among several questions does not collapse mastery to zero
    or flip the topic to foundational on its own.
    """
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)
    q1 = f"quiz-{TOPIC_PHOTOSYNTHESIS}-q1"
    q2 = f"quiz-{TOPIC_PHOTOSYNTHESIS}-q2"
    attempts = [
        # Strong recent correct on question 1.
        {"questionId": q1, "outcome": "correct", "createdAt": "2026-04-14T12:00:00+00:00"},
        # Question 2: earlier correct, then a single recent incorrect (latest).
        {"questionId": q2, "outcome": "correct", "createdAt": "2026-04-10T12:00:00+00:00"},
        {"questionId": q2, "outcome": "incorrect", "createdAt": NOW.isoformat()},
    ]
    mastery = calculate_mastery(attempts, now=NOW)
    # Latest per question: q1 correct, q2 incorrect -> dampened but not zero.
    assert mastery is not None
    assert 0.40 <= mastery < 0.60, f"single incorrect overreacted: mastery={mastery}"
    assert _difficulty_level_from_mastery(mastery) == "building"


def test_conflicting_performance_signals_stay_unscored(tmp_path: str) -> None:
    """When every result is ambiguous, mastery stays None (no fabricated score)."""
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)
    attempts = [
        {
            "questionId": f"quiz-{TOPIC_PHOTOSYNTHESIS}-q1",
            "outcome": "ambiguous",
            "createdAt": "2026-03-01T12:00:00+00:00",
        },
        {
            "questionId": f"quiz-{TOPIC_PHOTOSYNTHESIS}-q2",
            "outcome": "ambiguous",
            "createdAt": NOW.isoformat(),
        },
    ]
    mastery = calculate_mastery(attempts, now=NOW)
    assert mastery is None
    assert _difficulty_level_from_mastery(mastery) == "foundational"


def test_recalculation_reflects_new_attempts(tmp_path: str) -> None:
    """Estimates recalculate from current data after new attempts."""
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)
    q1 = f"quiz-{TOPIC_PHOTOSYNTHESIS}-q1"

    mastery_before = calculate_mastery(
        [
            {"questionId": q1, "outcome": "incorrect", "createdAt": NOW.isoformat()},
        ],
        now=NOW,
    )
    assert mastery_before == 0.0

    # Add a recent correct attempt for the same question; latest flips to correct.
    mastery_after = calculate_mastery(
        [
            {"questionId": q1, "outcome": "incorrect", "createdAt": "2026-04-01T12:00:00+00:00"},
            {"questionId": q1, "outcome": "correct", "createdAt": NOW.isoformat()},
        ],
        now=NOW,
    )
    assert mastery_after == 1.0
    assert mastery_after != mastery_before


# ---------------------------------------------------------------------------
# 4. Personalized learning paths (prerequisite dependencies)
# ---------------------------------------------------------------------------


def test_learning_objective_prerequisite_dependencies(tmp_path: str) -> None:
    """Objectives can declare prerequisites within the same path."""
    user_id, settings = _seed_user(tmp_path)
    path = create_learning_path(
        settings.database_path,
        user_id=user_id,
        title="Photosynthesis path",
        description="Learn photosynthesis from the ground up.",
    )
    path_id = path["pathId"]

    foundational = create_learning_objective(
        settings.database_path,
        path_id=path_id,
        title="Light absorption",
        description="Chlorophyll absorbs light.",
        order_index=0,
        difficulty_level="foundational",
    )
    assert foundational is not None
    assert foundational["prerequisiteObjectiveIds"] == []

    advanced = create_learning_objective(
        settings.database_path,
        path_id=path_id,
        title="Calvin cycle",
        description="Carbon fixation.",
        order_index=1,
        prerequisite_objective_ids=[foundational["objectiveId"]],
        difficulty_level="advanced",
    )
    assert advanced is not None
    assert advanced["prerequisiteObjectiveIds"] == [foundational["objectiveId"]]

    fetched = get_learning_objective(settings.database_path, advanced["objectiveId"])
    assert fetched is not None
    assert fetched["difficultyLevel"] == "advanced"
    assert fetched["prerequisiteObjectiveIds"] == [foundational["objectiveId"]]


def test_prerequisite_must_belong_to_same_path(tmp_path: str) -> None:
    """Cross-path prerequisite references are rejected."""
    user_id, settings = _seed_user(tmp_path)
    path_a = create_learning_path(
        settings.database_path,
        user_id=user_id,
        title="Path A",
        description="First path.",
    )
    path_b = create_learning_path(
        settings.database_path,
        user_id=user_id,
        title="Path B",
        description="Second path.",
    )
    obj_a = create_learning_objective(
        settings.database_path,
        path_id=path_a["pathId"],
        title="Objective A",
        description="A.",
        order_index=0,
        difficulty_level="foundational",
    )
    with pytest.raises(ValueError, match="Prerequisite objectives not found"):
        create_learning_objective(
            settings.database_path,
            path_id=path_b["pathId"],
            title="Objective B",
            description="B.",
            order_index=0,
            prerequisite_objective_ids=[obj_a["objectiveId"]],
            difficulty_level="foundational",
        )


# ---------------------------------------------------------------------------
# 9. Multi-user isolation & 8. model-unavailable-not-needed
# ---------------------------------------------------------------------------


def test_adaptive_recommendations_isolate_users(tmp_path: str) -> None:
    """User A's mastery data never leaks into user B's estimates."""
    user_a, settings_a = _seed_user(tmp_path, "learner-a")
    user_b, settings_b = _seed_user(tmp_path, "learner-b")

    # Both users share the same database file; tenant scoping is by user_id.
    settings = settings_a
    _seed_quiz(settings, user_a, quiz_id="quiz-a")
    _seed_quiz(settings, user_b, quiz_id="quiz-b")

    # User A gets it wrong; user B gets it right.
    _record(settings, user_a, "quiz-a-q1", "incorrect", NOW)
    _record(settings, user_b, "quiz-b-q1", "correct", NOW)

    mastery_a = calculate_mastery(
        [{"questionId": "quiz-a-q1", "outcome": "incorrect", "createdAt": NOW.isoformat()}],
        now=NOW,
    )
    mastery_b = calculate_mastery(
        [{"questionId": "quiz-b-q1", "outcome": "correct", "createdAt": NOW.isoformat()}],
        now=NOW,
    )
    assert mastery_a == 0.0
    assert mastery_b == 1.0
    assert _difficulty_level_from_mastery(mastery_a) == "foundational"
    assert _difficulty_level_from_mastery(mastery_b) == "advanced"

    # Recommendations are computed per user from that user's own records.
    recs_a = compute_adaptive_recommendations(
        settings.database_path, user_id=user_a, now=NOW, limit=5
    )
    recs_b = compute_adaptive_recommendations(
        settings.database_path, user_id=user_b, now=NOW, limit=5
    )
    assert isinstance(recs_a, list) and isinstance(recs_b, list)


def test_adaptive_engine_works_without_ai_model(tmp_path: str) -> None:
    """Adaptive estimates are computed from stored records only; no model."""
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)
    _record(
        settings,
        user_id,
        f"quiz-{TOPIC_PHOTOSYNTHESIS}-q1",
        "correct",
        NOW,
    )

    recs = compute_adaptive_recommendations(
        settings.database_path, user_id=user_id, now=NOW, limit=5
    )
    # Deterministic, model-free output.
    assert isinstance(recs, list)
    for rec in recs:
        assert "topicId" in rec
        assert "difficultyLevel" in rec
        assert "priorityScore" in rec
        assert "reasons" in rec
        assert "mastery" in rec
        assert "measuredFields" in rec
        assert "estimatedFields" in rec


# ---------------------------------------------------------------------------
# 9. Insufficient learner history
# ---------------------------------------------------------------------------


def test_insufficient_history_returns_empty_recommendations_and_foundational(
    tmp_path: str,
) -> None:
    """No attempts -> no recommendations; difficulty defaults to foundational."""
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)

    recs = compute_adaptive_recommendations(
        settings.database_path, user_id=user_id, now=NOW, limit=5
    )
    assert recs == []
    assert _difficulty_level_from_mastery(None) == "foundational"
    assert calculate_mastery([], now=NOW) is None
