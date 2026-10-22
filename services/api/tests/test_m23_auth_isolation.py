"""M23 authentication and multi-user isolation regressions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from starlette.responses import Response

from app.auth import create_session, hash_password, set_session_cookie
from app.config import Settings
from app.knowledge_graph import parse_extraction
from app.main import create_app
from app.storage import (
    complete_study_session,
    connect_database,
    create_quiz_set,
    create_user,
    get_document,
    initialize_storage,
    list_documents,
    record_quiz_attempt,
    start_study_session,
    store_document,
    store_knowledge_extraction,
    store_learning_image,
)


def authenticate_client(client: TestClient, username: str, password: str) -> TestClient:
    csrf_token = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf_token})
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return client


def test_registration_login_logout_and_generic_auth_errors(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "auth.sqlite3",
        rate_limit_enabled=False,
    )
    initialize_storage(settings.database_path)
    with TestClient(create_app(settings)) as client:
        csrf_token = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
        client.headers.update({"X-CSRF-Token": csrf_token})
        credentials = {"username": "learner-one", "password": "correct-horse-123"}

        registered = client.post("/api/v1/auth/register", json=credentials)
        duplicate = client.post("/api/v1/auth/register", json=credentials)
        assert registered.status_code == duplicate.status_code == 202
        assert registered.json() == duplicate.json()

        unknown_user = client.post(
            "/api/v1/auth/login",
            json={"username": "no-account", "password": credentials["password"]},
        )
        wrong_password = client.post(
            "/api/v1/auth/login",
            json={"username": credentials["username"], "password": "wrong-password"},
        )
        assert unknown_user.status_code == wrong_password.status_code == 401
        assert unknown_user.json() == wrong_password.json()

        logged_in = client.post("/api/v1/auth/login", json=credentials)
        assert logged_in.status_code == 200
        cookies = logged_in.headers.get_list("set-cookie")
        session_cookie = next(cookie for cookie in cookies if "wl_session=" in cookie)
        assert "httponly" in session_cookie.lower()
        assert "samesite=lax" in session_cookie.lower()
        assert "wl_session" not in logged_in.json()
        assert client.get("/api/v1/auth/me").status_code == 200

        logout = client.post("/api/v1/auth/logout")
        assert logout.status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401


def test_anonymous_access_csrf_and_expired_sessions_are_rejected(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "expiry.sqlite3",
        rate_limit_enabled=False,
    )
    initialize_storage(settings.database_path)
    user_id = create_user(settings.database_path, "expiry-user", hash_password("password-123"))
    expired_token, _ = create_session(settings.database_path, user_id, ttl_seconds=0)

    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/documents").status_code == 401
        assert client.post("/api/v1/study-sessions", json={"topic": "Biology"}).status_code == 403
        client.cookies.set("wl_session", expired_token)
        assert client.get("/api/v1/auth/me").status_code == 401


def test_production_session_cookie_is_secure():
    response = Response()
    set_session_cookie(
        response,
        "test-token",
        is_production=True,
        max_age=600,
    )
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie


def test_auth_rate_limit_and_production_configuration(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "limited.sqlite3",
        embedding_cache_path=tmp_path / "models",
        auth_rate_limit_requests_per_minute=2,
    )
    initialize_storage(settings.database_path)
    with TestClient(create_app(settings)) as client:
        csrf_token = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
        headers = {"X-CSRF-Token": csrf_token}
        payload = {"username": "missing-user", "password": "invalid-password"}
        assert client.post("/api/v1/auth/login", json=payload, headers=headers).status_code == 401
        assert client.post("/api/v1/auth/login", json=payload, headers=headers).status_code == 401
        assert client.post("/api/v1/auth/login", json=payload, headers=headers).status_code == 429

    production = Settings(
        _env_file=None,
        environment="production",
        cors_origins="https://learn.example.test",
        database_path=tmp_path / "production.sqlite3",
        embedding_cache_path=tmp_path / "production-models",
    )
    production.validate_startup()
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(
            _env_file=None,
            environment="production",
            cors_origins="http://localhost:5173",
            database_path=tmp_path / "insecure.sqlite3",
            embedding_cache_path=tmp_path / "insecure-models",
        ).validate_startup()
    with pytest.raises(ValueError, match="RATE_LIMIT_ENABLED"):
        Settings(
            _env_file=None,
            environment="production",
            cors_origins="https://learn.example.test",
            rate_limit_enabled=False,
            database_path=tmp_path / "unlimited.sqlite3",
            embedding_cache_path=tmp_path / "unlimited-models",
        ).validate_startup()


def test_legacy_database_migration_preserves_unowned_rows(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                document_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                page_count INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                parser_version TEXT NOT NULL
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                page_number INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                char_start INTEGER NOT NULL,
                char_end INTEGER NOT NULL,
                text TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED, text);
            INSERT INTO documents VALUES (
                'legacy-doc', 'legacy.pdf', 'a', 10, 1, 1,
                '2026-01-01T00:00:00+00:00', 'legacy-parser'
            );
            INSERT INTO chunks VALUES (
                'legacy-chunk', 'legacy-doc', 1, 0, 0, 12, 'legacy passage'
            );
            INSERT INTO chunks_fts VALUES ('legacy-chunk', 'legacy passage');
            PRAGMA user_version = 8;
            """
        )

    initialize_storage(database_path)
    user_id = create_user(database_path, "new-user", hash_password("new-password-123"))

    assert get_document(database_path, "legacy-doc")["display_name"] == "legacy.pdf"
    assert get_document(database_path, "legacy-doc", user_id=user_id) is None
    assert list_documents(database_path, user_id=user_id) == []
    with connect_database(database_path) as connection:
        assert (
            connection.execute(
                "SELECT user_id FROM documents WHERE document_id='legacy-doc'"
            ).fetchone()["user_id"]
            is None
        )
        assert (
            connection.execute(
                "SELECT user_id FROM chunks WHERE chunk_id='legacy-chunk'"
            ).fetchone()["user_id"]
            is None
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM chunks_fts WHERE chunk_id='legacy-chunk'"
            ).fetchone()[0]
            == 1
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10


def test_user_data_apis_are_isolated_across_resource_types(tmp_path):
    database_path = tmp_path / "isolation.sqlite3"
    settings = Settings(
        _env_file=None,
        database_path=database_path,
        rate_limit_enabled=False,
    )
    initialize_storage(database_path)
    owner_id = create_user(database_path, "owner-user", hash_password("owner-password-123"))
    create_user(database_path, "other-user", hash_password("other-password-123"))
    app = create_app(settings)

    source = "Photosynthesis uses chlorophyll to absorb light."
    document_id = "doc_owner"
    chunk_id = "chunk_owner"
    store_document(
        database_path,
        {
            "document_id": document_id,
            "display_name": "biology.pdf",
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
            "byte_size": len(source),
            "page_count": 1,
            "chunk_count": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "parser_version": "test",
        },
        [
            {
                "chunk_id": chunk_id,
                "page_number": 1,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": len(source),
                "text": source,
            }
        ],
        user_id=owner_id,
    )
    image_bytes = b"private-image-data"
    image_id = "image_owner"
    store_learning_image(
        database_path,
        {
            "imageId": image_id,
            "displayFilename": "private.png",
            "contentType": "image/png",
            "width": 1,
            "height": 1,
            "sha256": hashlib.sha256(image_bytes).hexdigest(),
            "createdAt": datetime.now(UTC).isoformat(),
            "ocrStatus": "unavailable",
            "extractedText": None,
        },
        image_bytes,
        user_id=owner_id,
    )
    graph_chunk = {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "page_number": 1,
        "text": source,
    }
    extraction = parse_extraction(
        json.dumps(
            {
                "concepts": [
                    {
                        "name": "Photosynthesis",
                        "aliases": [],
                        "supportingText": "Photosynthesis uses chlorophyll",
                    },
                    {
                        "name": "Chlorophyll",
                        "aliases": [],
                        "supportingText": "chlorophyll to absorb light",
                    },
                ],
                "relationships": [
                    {
                        "source": "Photosynthesis",
                        "target": "Chlorophyll",
                        "relation": "uses",
                        "supportingText": "Photosynthesis uses chlorophyll",
                    }
                ],
            }
        ),
        graph_chunk,
    )
    store_knowledge_extraction(database_path, graph_chunk, extraction, user_id=owner_id)

    quiz_id = "quiz_owner"
    question_id = "question_owner"
    create_quiz_set(
        database_path,
        {
            "quiz_id": quiz_id,
            "topic_id": "topic_photosynthesis",
            "topic": "Photosynthesis",
            "created_at": datetime.now(UTC).isoformat(),
            "questions": [
                {
                    "question_id": question_id,
                    "question_type": "multiple_choice",
                    "prompt": "What does chlorophyll absorb?",
                    "options": [{"id": "A", "text": "Light"}],
                    "answer_key": "A",
                    "accepted_answers": [],
                    "explanation": "The passage states chlorophyll absorbs light.",
                    "citations": [
                        {
                            "citationId": chunk_id,
                            "retrieval": {"method": "fts5_bm25", "rank": 1},
                        }
                    ],
                }
            ],
        },
        user_id=owner_id,
    )
    record_quiz_attempt(
        database_path,
        attempt_id="attempt_owner",
        question_id=question_id,
        outcome="incorrect",
        created_at=datetime.now(UTC).isoformat(),
        user_id=owner_id,
    )
    session_id = "session_owner"
    session_time = datetime.now(UTC).isoformat()
    topic_id = hashlib.sha256(b"Photosynthesis").hexdigest()[:20]
    start_study_session(
        database_path,
        session_id=session_id,
        topic_id=topic_id,
        topic="Photosynthesis",
        started_at=session_time,
        user_id=owner_id,
    )
    complete_study_session(
        database_path,
        session_id=session_id,
        completed_at=session_time,
        user_id=owner_id,
    )

    with TestClient(app) as owner, TestClient(app) as other:
        owner_client = authenticate_client(owner, "owner-user", "owner-password-123")
        other_client = authenticate_client(other, "other-user", "other-password-123")

        assert owner_client.get(f"/api/v1/documents/{document_id}").status_code == 200
        assert other_client.get(f"/api/v1/documents/{document_id}").status_code == 404
        assert owner_client.get(f"/api/v1/images/{image_id}").status_code == 200
        assert other_client.get(f"/api/v1/images/{image_id}").status_code == 404
        assert owner_client.get(f"/api/v1/quizzes/{quiz_id}").status_code == 200
        assert other_client.get(f"/api/v1/quizzes/{quiz_id}").status_code == 404
        assert owner_client.get("/api/v1/learning-analytics").json()["totals"]["attemptCount"] == 1
        other_analytics = other_client.get("/api/v1/learning-analytics").json()
        assert other_analytics["totals"]["attemptCount"] == 0
        assert other_analytics["totals"]["completedStudySessions"] == 0

        owner_concepts = owner_client.get("/api/v1/knowledge-graph").json()["concepts"]
        photosynthesis_id = next(
            concept["conceptId"]
            for concept in owner_concepts
            if concept["name"] == "Photosynthesis"
        )
        assert (
            owner_client.get(f"/api/v1/knowledge-graph/concepts/{photosynthesis_id}").status_code
            == 200
        )
        assert (
            other_client.get(f"/api/v1/knowledge-graph/concepts/{photosynthesis_id}").status_code
            == 404
        )

        owner_plan = owner_client.get("/api/v1/study-coach/plan").json()
        other_plan = other_client.get("/api/v1/study-coach/plan").json()
        assert owner_plan["recommendations"]
        assert other_plan["recommendations"] == []

        forged_delete = other_client.delete(f"/api/v1/documents/{document_id}")
        assert forged_delete.status_code == 404
        assert owner_client.get(f"/api/v1/documents/{document_id}").status_code == 200
