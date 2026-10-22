import asyncio
from datetime import UTC, datetime

import pytest
from auth_helpers import authenticated_request, prepare_test_account

from app.config import Settings
from app.learning_analytics import build_analytics, calculate_mastery
from app.main import create_app
from app.storage import (
    create_quiz_set,
    delete_document,
    get_learning_analytics_data,
    initialize_storage,
    record_quiz_attempt,
    store_document,
)

NOW = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)


def test_mastery_uses_latest_scored_question_results_and_recency_weights():
    attempts = [
        {
            "questionId": "old-correct",
            "outcome": "correct",
            "createdAt": "2026-03-16T12:00:00+00:00",
        },
        {
            "questionId": "new-wrong",
            "outcome": "incorrect",
            "createdAt": "2026-04-15T12:00:00+00:00",
        },
        {"questionId": "retry", "outcome": "incorrect", "createdAt": "2026-04-14T12:00:00+00:00"},
        {"questionId": "retry", "outcome": "correct", "createdAt": "2026-04-15T12:00:00+00:00"},
        {
            "questionId": "uncertain",
            "outcome": "ambiguous",
            "createdAt": "2026-04-15T12:00:00+00:00",
        },
    ]
    # Weights: old correct=.5; recent incorrect=1; retry latest correct=1; ambiguous excluded.
    assert calculate_mastery(attempts, now=NOW) == pytest.approx(1.5 / 2.5)


def test_mastery_empty_and_ambiguous_only_are_unscored():
    assert calculate_mastery([], now=NOW) is None
    assert (
        calculate_mastery(
            [{"questionId": "q", "outcome": "ambiguous", "createdAt": NOW.isoformat()}],
            now=NOW,
        )
        is None
    )


def test_mastery_breaks_equal_timestamp_ties_by_persisted_attempt_sequence():
    attempts = [
        {
            "questionId": "q",
            "outcome": "correct",
            "createdAt": NOW.isoformat(),
            "sequence": 8,
        },
        {
            "questionId": "q",
            "outcome": "incorrect",
            "createdAt": NOW.isoformat(),
            "sequence": 9,
        },
    ]
    assert calculate_mastery(attempts, now=NOW) == 0.0


def test_analytics_empty_history_is_zero_and_review_due_boundary_is_inclusive():
    empty = build_analytics(
        attempts=[], sessions=[], reviews=[], topic_sources={}, concepts=[], now=NOW
    )
    assert empty["totals"]["attemptCount"] == 0
    assert empty["totals"]["quizAccuracy"] is None
    assert empty["totals"]["completedStudySessions"] == 0
    assert empty["topics"] == []
    assert all(row["attempts"] == 0 for row in empty["dailyActivity"])

    boundary = build_analytics(
        attempts=[
            {
                "attemptId": "a",
                "questionId": "q",
                "outcome": "incorrect",
                "createdAt": NOW.isoformat(),
                "topicId": "t",
                "topic": "Photosynthesis",
                "prompt": "Question?",
            }
        ],
        sessions=[],
        reviews=[
            {
                "questionId": "q",
                "topicId": "t",
                "topic": "Photosynthesis",
                "outcome": "incorrect",
                "attemptedAt": NOW.isoformat(),
                "dueAt": NOW.isoformat(),
            }
        ],
        topic_sources={},
        concepts=[],
        now=NOW,
    )
    assert boundary["totals"]["overdueReviews"] == 1
    assert boundary["topics"][0]["weak"] is True
    assert boundary["reviewHistory"][0]["status"] == "overdue"


async def request(app, method, path, **kwargs):
    return await authenticated_request(app, method, path, **kwargs)


def seed_document(database_path):
    initialize_storage(database_path)
    store_document(
        database_path,
        {
            "document_id": "doc_analytics",
            "display_name": "lesson.pdf",
            "sha256": "a" * 64,
            "byte_size": 100,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": NOW.isoformat(),
            "parser_version": "fixture",
        },
        [
            {
                "chunk_id": "chunk_analytics",
                "page_number": 2,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": 20,
                "text": "Photosynthesis material.",
            }
        ],
    )


def test_analytics_api_persists_attempts_sessions_citations_and_respects_source_deletion(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "analytics.sqlite3")
    seed_document(settings.database_path)
    create_quiz_set(
        settings.database_path,
        {
            "quiz_id": "quiz_analytics",
            "topic_id": "topic_photosynthesis",
            "topic": "Photosynthesis",
            "created_at": NOW.isoformat(),
            "questions": [
                {
                    "question_id": "question_analytics",
                    "question_type": "multiple_choice",
                    "prompt": "What does the process use?",
                    "options": [{"id": "A", "text": "Light"}],
                    "answer_key": "A",
                    "accepted_answers": [],
                    "explanation": "Source based.",
                    "citations": [
                        {
                            "citationId": "chunk_analytics",
                            "retrieval": {"method": "fts5_bm25", "rank": 1},
                        }
                    ],
                }
            ],
        },
    )
    record_quiz_attempt(
        settings.database_path,
        attempt_id="attempt_old",
        question_id="question_analytics",
        outcome="incorrect",
        created_at="2026-04-01T12:00:00+00:00",
    )
    app = create_app(settings)
    user_id = prepare_test_account(app)
    rejected = asyncio.run(request(app, "POST", "/api/v1/study-sessions", json={"topic": "  "}))
    assert rejected.status_code == 422
    started = asyncio.run(
        request(app, "POST", "/api/v1/study-sessions", json={"topic": "Photosynthesis"})
    )
    assert started.status_code == 201
    completed = asyncio.run(
        request(app, "POST", f"/api/v1/study-sessions/{started.json()['sessionId']}/complete")
    )
    assert completed.status_code == 200
    report = asyncio.run(request(app, "GET", "/api/v1/learning-analytics"))
    assert report.status_code == 200
    body = report.json()
    assert body["totals"]["attemptCount"] == 1
    assert body["totals"]["completedStudySessions"] == 1
    assert body["totals"]["overdueReviews"] == 1
    assert body["topics"][0]["citations"][0]["citationId"] == "chunk_analytics"
    assert body["topics"][0]["citations"][0]["retrieval"]["method"] == "fts5_bm25"
    assert body["topics"][0]["citations"][0]["retrieval"]["rank"] == 1
    assert any(item["kind"] == "study_session" for item in body["recentActivity"])

    assert delete_document(settings.database_path, "doc_analytics", user_id=user_id)
    after_delete_data = get_learning_analytics_data(settings.database_path, user_id=user_id)
    after_delete = build_analytics(
        attempts=after_delete_data["attempts"],
        sessions=after_delete_data["sessions"],
        reviews=after_delete_data["reviews"],
        topic_sources=after_delete_data["topicSources"],
        concepts=after_delete_data["concepts"],
        now=NOW,
    )
    assert after_delete["totals"]["attemptCount"] == 0
    assert after_delete["topics"] == []
    assert after_delete["totals"]["completedStudySessions"] == 1
