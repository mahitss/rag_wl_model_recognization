"""
M55 — Adaptive Learning Engine regressions.

Verify that the M55 additions work correctly:
- User estimate reset (delete own quiz attempts and review items)
- Per-question difficulty in quiz generation
- Time-spent weighting in mastery calculation

Uses synthetic learner profiles and fixed timestamps; no AI model required.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from app.auth import hash_password
from app.config import Settings
from app.learning_analytics import calculate_mastery
from app.storage import (
    create_quiz_set,
    create_user,
    initialize_storage,
    record_quiz_attempt,
    reset_user_progress,
)

NOW = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)

TOPIC_PHOTOSYNTHESIS = "topic-photosynthesis"


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_path=os.path.join(str(tmp_path), "m55_adaptive.sqlite3"),
        rate_limit_enabled=False,
    )


def _seed_user(tmp_path, username: str = "learner"):
    settings = _settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = create_user(settings.database_path, username, hash_password("password-123"))
    return user_id, settings


def _seed_quiz(
    settings,
    user_id,
    topic_id=TOPIC_PHOTOSYNTHESIS,
    topic="Photosynthesis",
    quiz_id="quiz-m55",
):
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
                    "difficulty_level": "foundational",
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
                    "difficulty_level": "advanced",
                },
            ],
        },
        user_id=user_id,
    )
    return quiz_id


def _record(settings, user_id, question_id, outcome, created_at, duration_ms=None):
    record_quiz_attempt(
        settings.database_path,
        attempt_id=f"attempt-{question_id}-{created_at.isoformat()}",
        question_id=question_id,
        outcome=outcome,
        created_at=created_at.isoformat(),
        user_id=user_id,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# 1. User estimate reset
# ---------------------------------------------------------------------------


def test_reset_user_progress_clears_attempts_and_reviews(tmp_path):
    """DELETE /progress/reset equivalent: clears a user's attempts and reviews."""
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)
    _record(
        settings,
        user_id,
        "quiz-m55-q1",
        "correct",
        NOW,
    )

    # Verify data exists before reset
    from app.storage import get_learning_progress

    progress_before = get_learning_progress(settings.database_path, user_id=user_id)
    assert len(progress_before["topics"]) == 1

    # Reset
    result = reset_user_progress(settings.database_path, user_id)
    assert result["attempts_deleted"] == 1
    assert result["reviews_deleted"] == 0

    # Verify data is gone after reset
    progress_after = get_learning_progress(settings.database_path, user_id=user_id)
    assert progress_after["topics"] == []
    assert progress_after["totalAttempts"] == 0


def test_reset_user_progress_is_user_scoped(tmp_path):
    """Resetting one user's progress does not affect another user's data."""
    user_a, settings_a = _seed_user(tmp_path, "learner-a")
    user_b, settings_b = _seed_user(tmp_path, "learner-b")
    # Both users share the same database file
    settings = settings_a
    _seed_quiz(settings, user_a, quiz_id="quiz-a")
    _seed_quiz(settings, user_b, quiz_id="quiz-b")
    _record(settings, user_a, "quiz-a-q1", "correct", NOW)
    _record(settings, user_b, "quiz-b-q1", "correct", NOW)

    result = reset_user_progress(settings.database_path, user_a)
    assert result["attempts_deleted"] == 1

    # User B's data is untouched
    from app.storage import get_learning_progress

    progress_b = get_learning_progress(settings.database_path, user_id=user_b)
    assert len(progress_b["topics"]) == 1


# ---------------------------------------------------------------------------
# 2. Per-question difficulty
# ---------------------------------------------------------------------------


def test_quiz_question_has_difficulty_level(tmp_path):
    """Questions stored with difficulty_level return it via get_quiz_question."""
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)

    from app.storage import get_quiz_question

    q1 = get_quiz_question(settings.database_path, "quiz-m55-q1", user_id=user_id)
    assert q1["difficulty_level"] == "foundational"
    q2 = get_quiz_question(settings.database_path, "quiz-m55-q2", user_id=user_id)
    assert q2["difficulty_level"] == "advanced"


def test_difficulty_level_defaults_to_none(tmp_path):
    """Questions without a difficulty level have difficulty_level=None."""
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)
    # Add a question without difficulty_level
    from app.storage import create_quiz_set

    create_quiz_set(
        settings.database_path,
        {
            "quiz_id": "quiz-no-diff",
            "topic_id": TOPIC_PHOTOSYNTHESIS,
            "topic": "Photosynthesis",
            "created_at": NOW.isoformat(),
            "questions": [
                {
                    "question_id": "quiz-no-diff-q3",
                    "question_type": "multiple_choice",
                    "prompt": "What color is chlorophyll?",
                    "options": [{"id": "A", "text": "Green"}],
                    "answer_key": "A",
                    "accepted_answers": [],
                    "explanation": "Chlorophyll is green.",
                    "citations": [],
                }
            ],
        },
        user_id=user_id,
    )
    from app.storage import get_quiz_question

    q3 = get_quiz_question(settings.database_path, "quiz-no-diff-q3", user_id=user_id)
    assert q3["difficulty_level"] is None


# ---------------------------------------------------------------------------
# 3. Time-spent weighting in mastery
# ---------------------------------------------------------------------------


def test_mastery_weights_by_time_spent(tmp_path):
    """Longer time spent on a question increases its weight in mastery."""
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)
    q1 = "quiz-m55-q1"
    q2 = "quiz-m55-q2"

    # q1 correct with lots of time (full time weight ~2.0), q2 incorrect with little time
    mastery = calculate_mastery(
        [
            {
                "questionId": q1,
                "outcome": "correct",
                "createdAt": "2026-04-14T12:00:00+00:00",
                "duration_ms": 60_000,
            },
            {
                "questionId": q2,
                "outcome": "incorrect",
                "createdAt": "2026-04-14T12:00:00+00:00",
                "duration_ms": 5_000,
            },
        ],
        now=NOW,
    )
    assert mastery is not None
    assert 0.0 < mastery < 1.0
    # q1 correct with high time weight should dominate q2 incorrect with low time weight
    assert mastery > 0.5  # weighted toward q1's correct answer


def test_mastery_without_duration_ms_is_neutral(tmp_path):
    """Attempts without duration_ms use neutral time weight of 1.0."""
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)
    q1 = "quiz-m55-q1"

    mastery = calculate_mastery(
        [
            {
                "questionId": q1,
                "outcome": "correct",
                "createdAt": NOW.isoformat(),
                # no duration_ms
            },
        ],
        now=NOW,
    )
    assert mastery == 1.0  # single correct answer with neutral weight = 1.0


def test_mastery_incorrect_with_time_spent_is_low(tmp_path):
    """A recent incorrect answer with lots of time spent lowers mastery significantly."""
    user_id, settings = _seed_user(tmp_path)
    _seed_quiz(settings, user_id)
    q1 = "quiz-m55-q1"

    mastery = calculate_mastery(
        [
            {
                "questionId": q1,
                "outcome": "incorrect",
                "createdAt": NOW.isoformat(),
                "duration_ms": 60_000,  # lots of time, still wrong
            },
        ],
        now=NOW,
    )
    assert mastery == 0.0  # incorrect answer = 0 correctness regardless of time
