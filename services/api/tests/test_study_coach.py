import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

from auth_helpers import authenticated_request, prepare_test_account

from app.config import Settings
from app.main import create_app
from app.storage import create_quiz_set, initialize_storage, record_quiz_attempt, store_document
from app.study_coach import build_daily_recommendations


def test_prioritization_uses_overdue_reviews_weakness_sessions_and_graph_links():
    now = datetime(2026, 10, 19, 12, tzinfo=UTC)
    citation = {"citationId": "chunk-a", "text": "Real stored source."}
    analytics = {
        "topics": [
            {
                "topicId": "weak",
                "topic": "Photosynthesis",
                "overdueReviewCount": 2,
                "weak": True,
                "mastery": 0.3,
                "incorrectQuestions": 2,
                "ambiguousQuestions": 1,
                "attemptedQuestions": 3,
                "graphConceptId": "c1",
            },
            {
                "topicId": "strong",
                "topic": "Cell",
                "overdueReviewCount": 0,
                "weak": False,
                "mastery": 0.9,
                "incorrectQuestions": 0,
                "ambiguousQuestions": 0,
                "attemptedQuestions": 2,
                "graphConceptId": None,
            },
        ],
        "topicSources": {"weak": [citation], "strong": [citation]},
        "concepts": [{"conceptId": "c2", "name": "Chlorophyll"}],
        "relationships": [
            {"sourceConceptId": "c1", "targetConceptId": "c2", "status": "candidate"}
        ],
        "sessions": [{"topicId": "weak", "completedAt": now.isoformat()}],
    }
    plan = build_daily_recommendations(analytics=analytics, now=now)
    assert [item["topicId"] for item in plan] == ["weak", "strong"]
    assert plan[0]["priority"] == 2 * 100 + 40 + 20 + 5 + 1 - 10
    assert "2 review question(s)" in plan[0]["reasons"][0]
    assert "Chlorophyll" in plan[0]["reasons"][-1]
    assert plan[0]["citations"] == [citation]


def test_empty_or_unlinked_activity_produces_no_fabricated_recommendations():
    now = datetime.now(UTC)
    assert build_daily_recommendations(analytics={}, now=now) == []
    assert (
        build_daily_recommendations(
            analytics={
                "topics": [{"topicId": "t", "topic": "No sources"}],
                "topicSources": {},
                "relationships": [],
                "concepts": [],
                "sessions": [],
            },
            now=now,
        )
        == []
    )


def test_local_frontend_can_preflight_study_coach_patch_requests(tmp_path):
    app = create_app(Settings(_env_file=None, database_path=tmp_path / "coach.sqlite3"))
    response = asyncio.run(
        request(
            app,
            "OPTIONS",
            "/api/v1/study-coach/recommendations/coach_test",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    )
    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]


async def request(app, method, path, **kwargs):
    return await authenticated_request(app, method, path, **kwargs)


def make_learning_data(database_path, now):
    initialize_storage(database_path)
    store_document(
        database_path,
        {
            "document_id": "doc_coach",
            "display_name": "biology.pdf",
            "sha256": "c" * 64,
            "byte_size": 100,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": now.isoformat(),
            "parser_version": "fixture",
        },
        [
            {
                "chunk_id": "chunk_coach",
                "page_number": 3,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": 40,
                "text": "Plants use chlorophyll for photosynthesis.",
            }
        ],
    )
    create_quiz_set(
        database_path,
        {
            "quiz_id": "quiz_coach",
            "topic_id": "topic_photo",
            "topic": "Photosynthesis",
            "created_at": now.isoformat(),
            "questions": [
                {
                    "question_id": "question_coach",
                    "question_type": "multiple_choice",
                    "prompt": "What does chlorophyll absorb?",
                    "options": [{"id": "A", "text": "Light"}],
                    "answer_key": "A",
                    "accepted_answers": [],
                    "explanation": "It absorbs light.",
                    "citations": [
                        {
                            "citationId": "chunk_coach",
                            "retrieval": {"method": "fts5_bm25", "rank": 1},
                        }
                    ],
                }
            ],
        },
    )
    record_quiz_attempt(
        database_path,
        attempt_id="attempt_coach",
        question_id="question_coach",
        outcome="incorrect",
        created_at=(now - timedelta(days=3)).isoformat(),
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO knowledge_concepts("
            "concept_id, canonical_name, normalized_name, aliases_json) "
            "VALUES (?, ?, ?, ?)",
            ("concept_photo", "Photosynthesis", "photosynthesis", "[]"),
        )
        connection.execute(
            "INSERT INTO knowledge_concepts("
            "concept_id, canonical_name, normalized_name, aliases_json) "
            "VALUES (?, ?, ?, ?)",
            ("concept_light", "Light", "light", "[]"),
        )
        connection.execute(
            """INSERT INTO knowledge_relationships(
               relationship_id, source_concept_id, target_concept_id, relation,
               chunk_id, page_number, supporting_text, status) VALUES (
               ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "relation_coach",
                "concept_photo",
                "concept_light",
                "uses",
                "chunk_coach",
                3,
                "Plants use light.",
                "candidate",
            ),
        )


def test_api_plan_is_cited_persisted_and_supports_accept_edit_dismiss_complete(tmp_path):
    now = datetime.now(UTC)
    settings = Settings(_env_file=None, database_path=tmp_path / "coach.sqlite3")
    make_learning_data(settings.database_path, now)
    app = create_app(settings)
    prepare_test_account(app)
    plan = asyncio.run(request(app, "GET", "/api/v1/study-coach/plan"))
    assert plan.status_code == 200
    recommendation = plan.json()["recommendations"][0]
    rec_id = recommendation["recommendationId"]
    assert recommendation["topic"] == "Photosynthesis"
    assert recommendation["status"] == "planned"
    assert recommendation["citations"][0]["citationId"] == "chunk_coach"
    assert recommendation["citations"][0]["documentId"] == "doc_coach"
    assert recommendation["citations"][0]["pageNumber"] == 3
    assert any("overdue" in reason for reason in recommendation["reasons"])
    assert "concept_light" in recommendation["relatedConceptIds"]

    accepted = asyncio.run(
        request(
            app, "PATCH", f"/api/v1/study-coach/recommendations/{rec_id}", json={"action": "accept"}
        )
    )
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
    edited = asyncio.run(
        request(
            app,
            "PATCH",
            f"/api/v1/study-coach/recommendations/{rec_id}",
            json={"action": "edit", "title": "Review plant energy", "estimatedMinutes": 35},
        )
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "Review plant energy"
    assert edited.json()["estimatedMinutes"] == 35
    completed = asyncio.run(
        request(
            app,
            "PATCH",
            f"/api/v1/study-coach/recommendations/{rec_id}",
            json={"action": "complete"},
        )
    )
    assert completed.json()["status"] == "completed"
    dismissed = asyncio.run(
        request(
            app,
            "PATCH",
            f"/api/v1/study-coach/recommendations/{rec_id}",
            json={"action": "dismiss"},
        )
    )
    assert dismissed.json()["status"] == "dismissed"
    assert (
        asyncio.run(
            request(
                app,
                "PATCH",
                f"/api/v1/study-coach/recommendations/{rec_id}",
                json={"action": "edit"},
            )
        ).status_code
        == 422
    )
    reloaded = asyncio.run(request(app, "GET", "/api/v1/study-coach/plan"))
    assert reloaded.json()["recommendations"][0]["status"] == "dismissed"
    assert reloaded.json()["recommendations"][0]["title"] == "Review plant energy"


def test_plan_empty_state_and_source_deletion_remove_recommendation_citations(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "coach.sqlite3")
    app = create_app(settings)
    empty = asyncio.run(request(app, "GET", "/api/v1/study-coach/plan"))
    assert empty.status_code == 200
    assert empty.json()["recommendations"] == []
    assert "No quiz attempts" in empty.json()["emptyReason"]

    make_learning_data(settings.database_path, datetime.now(UTC))
    from app.storage import delete_document

    user_id = prepare_test_account(app)
    delete_document(settings.database_path, "doc_coach", user_id=user_id)
    after = asyncio.run(request(app, "GET", "/api/v1/study-coach/plan"))
    assert after.status_code == 200
    assert after.json()["recommendations"] == []


def test_local_tutor_explanation_uses_only_persisted_citations_and_unavailable_is_safe(
    tmp_path, monkeypatch
):
    now = datetime.now(UTC)
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "coach.sqlite3",
        tutor_base_url="http://localhost:8080/v1",
    )
    make_learning_data(settings.database_path, now)
    app = create_app(settings)
    monkeypatch.setattr(
        "app.api.v1.routes.generate_grounded_answer",
        lambda _settings, *, question, concept, evidence: {
            "answer": "Review the passage about chlorophyll [E1].",
            "citations": [evidence[0]],
        },
    )
    plan = asyncio.run(request(app, "GET", "/api/v1/study-coach/plan")).json()
    recommendation_id = plan["recommendations"][0]["recommendationId"]
    explanation = asyncio.run(
        request(app, "POST", f"/api/v1/study-coach/recommendations/{recommendation_id}/explanation")
    )
    assert explanation.status_code == 200
    assert explanation.json()["citations"][0]["citationId"] == "chunk_coach"

    monkeypatch.undo()
    unavailable_app = create_app(Settings(_env_file=None, database_path=settings.database_path))
    unavailable = asyncio.run(
        request(
            unavailable_app,
            "POST",
            f"/api/v1/study-coach/recommendations/{recommendation_id}/explanation",
        )
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "TUTOR_MODEL_UNAVAILABLE"
