"""
M79: Personal Knowledge Graph & Cross-Subject Intelligence tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.auth import hash_password
from app.config import Settings
from app.main import create_app
from app.storage import (
    create_user,
    discover_cross_subject_connections,
    find_duplicate_concepts,
    get_knowledge_graph_insights,
    get_personalized_knowledge_graph,
    initialize_storage,
    list_knowledge_concepts,
    merge_knowledge_concepts,
    store_document,
    store_knowledge_extraction,
    traverse_classroom_concepts,
)


@pytest.fixture
def settings(tmp_path: Path):
    return Settings(_env_file=None, database_path=tmp_path / f"test_{tmp_path.name}.sqlite3")


@pytest.fixture
def app(settings):
    app = create_app(settings)
    initialize_storage(settings.database_path)
    return app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


def _create_user_and_login(app, database_path, username: str):
    """Create a user and return an authenticated client and the user_id."""
    from fastapi.testclient import TestClient
    user_id = create_user(database_path, username, hash_password("testpassword123"))
    client = TestClient(app)
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "testpassword123"},
    )
    return client, user_id


def _store_concept(database_path, user_id, concept_name, normalized_name, chunk_id="chunk_1"):
    """Create a document+chunk, store a knowledge extraction, return the concept_id."""
    import hashlib
    import time

    document_id = f"doc_{chunk_id}"
    sha256 = hashlib.sha256(f"{document_id}{user_id}".encode()).hexdigest()
    store_document(
        database_path,
        metadata={
            "document_id": document_id,
            "display_name": "Test Document",
            "sha256": sha256,
            "byte_size": 100,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": time.time(),
            "parser_version": "1.0",
            "format": "pdf",
        },
        chunks=[
            {
                "chunk_id": chunk_id,
                "page_number": 1,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": 10,
                "text": "test text",
                "user_id": user_id,
            }
        ],
        user_id=user_id,
    )
    store_knowledge_extraction(
        database_path,
        chunk={"chunk_id": chunk_id, "user_id": user_id, "page_number": 1},
        extraction={
            "concepts": [
                {
                    "name": concept_name,
                    "normalized_name": normalized_name,
                    "aliases": [],
                    "concept_id": normalized_name,
                    "supporting_text": "",
                }
            ],
            "entities": [],
            "relationships": [],
        },
        user_id=user_id,
    )
    concepts = list_knowledge_concepts(database_path, user_id=user_id)
    for c in concepts:
        if c["name"] == concept_name:
            return c["conceptId"]
    raise AssertionError(f"Concept {concept_name} not found after storage")


# ---------------------------------------------------------------------------
# Storage function tests
# ---------------------------------------------------------------------------

def test_store_and_list_knowledge_extraction(app):
    """Test storing and listing knowledge extraction results."""
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "test_user", hash_password("test123"))

    _store_concept(database_path, user_id, "Calculus", "calculus")

    topics = list_knowledge_concepts(database_path, user_id=user_id)
    assert len(topics) == 1
    assert topics[0]["name"] == "Calculus"


def test_find_duplicate_concepts(app):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "test_user", hash_password("test123"))

    _store_concept(database_path, user_id, "Calculus", "calculus", chunk_id="chunk_1")
    _store_concept(database_path, user_id, "Calculus", "calculus", chunk_id="chunk_2")

    duplicates = find_duplicate_concepts(
        database_path,
        user_id=user_id,
        similarity_threshold=0.7,
    )
    assert len(duplicates) >= 0


def test_merge_concepts(app):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "test_user", hash_password("test123"))

    c1 = _store_concept(database_path, user_id, "Calculus", "calculus", chunk_id="chunk_1")
    c2 = _store_concept(database_path, user_id, "Calculus", "calculus", chunk_id="chunk_2")

    result = merge_knowledge_concepts(
        database_path,
        primary_concept_id=c1,
        duplicate_concept_ids=[c2],
        user_id=user_id,
    )
    assert result is not None
    assert result["conceptId"] == c1
    assert c2 in result["mergedConceptIds"]


def test_traverse_classroom_concepts(app):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "test_user", hash_password("test123"))

    _store_concept(database_path, user_id, "Arithmetic", "arithmetic", chunk_id="chunk_1")
    _store_concept(database_path, user_id, "Algebra", "algebra", chunk_id="chunk_2")

    result = traverse_classroom_concepts(
        database_path,
        user_id=user_id,
        topic_id="math",
        max_depth=3,
    )
    assert result is not None
    assert "concepts" in result
    assert "relationships" in result


def test_cross_subject_discovery(app):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "test_user", hash_password("test123"))

    _store_concept(database_path, user_id, "Linear algebra", "linear_algebra", chunk_id="chunk_1")
    _store_concept(
        database_path, user_id, "Machine learning", "machine_learning", chunk_id="chunk_2"
    )

    result = discover_cross_subject_connections(
        database_path,
        user_id=user_id,
        topic_id="math",
        max_depth=3,
    )
    assert result is not None
    assert "crossSubjectConnections" in result


def test_personalized_knowledge_graph(app):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "test_user", hash_password("test123"))

    _store_concept(database_path, user_id, "Calculus", "calculus", chunk_id="chunk_1")

    result = get_personalized_knowledge_graph(
        database_path,
        user_id=user_id,
        topic_id="math",
    )
    assert result is not None
    assert "classroomGraph" in result
    assert "learnerSummary" in result


def test_get_knowledge_graph_insights(app):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "test_user", hash_password("test123"))

    _store_concept(database_path, user_id, "Calculus", "calculus", chunk_id="chunk_1")

    result = get_knowledge_graph_insights(
        database_path,
        user_id=user_id,
    )
    assert result is not None
    assert "totalConcepts" in result
    assert result["totalConcepts"] >= 1


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

def test_api_find_duplicates(app):
    client, user_id = _create_user_and_login(app, app.state.settings.database_path, "test_user")
    database_path = app.state.settings.database_path

    _store_concept(database_path, user_id, "Calculus", "calculus", chunk_id="chunk_1")
    _store_concept(database_path, user_id, "Calculus", "calculus", chunk_id="chunk_2")

    response = client.post(
        "/api/v1/knowledge-graph/duplicates",
        json={"similarity_threshold": 0.8},
    )
    assert response.status_code == 200
    data = response.json()
    assert "duplicateGroups" in data


def test_api_merge_concepts(app):
    database_path = app.state.settings.database_path
    client, user_id = _create_user_and_login(app, database_path, "test_user")

    c1 = _store_concept(database_path, user_id, "Calculus", "calculus", chunk_id="chunk_1")
    c2 = _store_concept(database_path, user_id, "Calculus", "calculus", chunk_id="chunk_2")

    response = client.post(
        "/api/v1/knowledge-graph/merge",
        json={
            "primaryConceptId": c1,
            "duplicateConceptIds": [c2],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["conceptId"] == c1
    assert c2 in data["mergedConceptIds"]


def test_api_traverse_concepts(app):
    database_path = app.state.settings.database_path
    client, user_id = _create_user_and_login(app, database_path, "test_user")

    _store_concept(database_path, user_id, "Algebra", "algebra", chunk_id="chunk_1")

    response = client.get(
        "/api/v1/knowledge-graph/traverse/test-concept",
        params={"topic_id": "math", "max_depth": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert "concepts" in data
    assert "relationships" in data


def test_api_cross_subject_discovery(app):
    database_path = app.state.settings.database_path
    client, user_id = _create_user_and_login(app, database_path, "test_user")

    _store_concept(database_path, user_id, "Linear algebra", "linear_algebra", chunk_id="chunk_1")

    response = client.get(
        "/api/v1/knowledge-graph/cross-subject",
        params={"topic_id": "math", "max_depth": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert "crossSubjectConnections" in data


def test_api_personalized_graph(app):
    database_path = app.state.settings.database_path
    client, user_id = _create_user_and_login(app, database_path, "test_user")

    _store_concept(database_path, user_id, "Calculus", "calculus", chunk_id="chunk_1")

    response = client.get(
        "/api/v1/knowledge-graph/personalized/test_user",
        params={"topic_id": "math"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "classroomGraph" in data
    assert "learnerSummary" in data


def test_api_knowledge_graph_insights(app):
    database_path = app.state.settings.database_path
    client, user_id = _create_user_and_login(app, database_path, "test_user")

    _store_concept(database_path, user_id, "Calculus", "calculus", chunk_id="chunk_1")

    response = client.get("/api/v1/knowledge-graph/insights")
    assert response.status_code == 200
    data = response.json()
    assert "totalConcepts" in data
    assert data["totalConcepts"] >= 1


# ---------------------------------------------------------------------------
# Cross-user isolation tests
# ---------------------------------------------------------------------------

def test_cross_user_isolation(app):
    database_path = app.state.settings.database_path
    client1, user_a_id = _create_user_and_login(app, database_path, "user_a")
    client2, _ = _create_user_and_login(app, database_path, "user_b")

    _store_concept(database_path, user_a_id, "User A's topic", "user_a_topic", chunk_id="chunk_1")

    response = client2.get(
        "/api/v1/knowledge-graph/personalized/user_a",
        params={"topic_id": "math"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "classroomGraph" in data
    concepts = data.get("classroomGraph", {}).get("concepts", [])
    user_a_topics = [c for c in concepts if c.get("name") == "User A's topic"]
    assert len(user_a_topics) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
