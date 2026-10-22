"""Test-only authentication helpers for API integration tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx

from app.auth import hash_password
from app.storage import (
    connect_database,
    create_user,
    get_user_by_username,
    initialize_storage,
)

_AUTH_CACHE: dict[str, tuple[str, str]] = {}


def prepare_test_account(app: Any) -> str:
    """Create a stable test account and explicitly assign seeded fixture rows to it."""
    database_path: Path = app.state.settings.database_path
    initialize_storage(database_path)
    username = f"api-test-{hashlib.sha256(str(database_path).encode()).hexdigest()[:16]}"
    user = get_user_by_username(database_path, username)
    if user is None:
        user_id = create_user(database_path, username, hash_password("test-password-123"))
    else:
        user_id = user["user_id"]

    with connect_database(database_path) as connection:
        with connection:
            legacy_concepts = connection.execute(
                "SELECT concept_id, normalized_name FROM knowledge_concepts WHERE user_id IS NULL"
            ).fetchall()
            for concept in legacy_concepts:
                connection.execute(
                    "UPDATE knowledge_concepts SET user_id=?, normalized_name=? "
                    "WHERE concept_id=? AND user_id IS NULL",
                    (user_id, f"{user_id}\x1f{concept['normalized_name']}", concept["concept_id"]),
                )
            for table in (
                "documents",
                "quiz_sets",
                "study_sessions",
                "learning_images",
                "study_coach_recommendations",
            ):
                connection.execute(
                    f"UPDATE {table} SET user_id=? WHERE user_id IS NULL", (user_id,)
                )
            for child, parent, child_key, parent_key in (
                ("chunks", "documents", "document_id", "document_id"),
                ("quiz_questions", "quiz_sets", "quiz_id", "quiz_id"),
                ("quiz_attempts", "quiz_questions", "question_id", "question_id"),
                ("quiz_citations", "quiz_questions", "question_id", "question_id"),
                ("image_regions", "learning_images", "image_id", "image_id"),
                ("image_region_citations", "image_regions", "region_id", "region_id"),
                ("knowledge_concept_evidence", "knowledge_concepts", "concept_id", "concept_id"),
                (
                    "knowledge_relationships",
                    "knowledge_concepts",
                    "source_concept_id",
                    "concept_id",
                ),
                ("chunk_embeddings", "chunks", "chunk_id", "chunk_id"),
                ("topic_review_items", "quiz_questions", "question_id", "question_id"),
            ):
                connection.execute(
                    f"""UPDATE {child}
                        SET user_id=(
                            SELECT parent.user_id FROM {parent} AS parent
                            WHERE parent.{parent_key}={child}.{child_key}
                        )
                        WHERE user_id IS NULL AND EXISTS (
                            SELECT 1 FROM {parent} AS parent
                            WHERE parent.{parent_key}={child}.{child_key}
                              AND parent.user_id=?
                        )""",
                    (user_id,),
                )
    return user_id


async def authenticated_request(app: Any, method: str, path: str, **kwargs: Any):
    async with app.router.lifespan_context(app):
        prepare_test_account(app)
        path_hash = hashlib.sha256(str(app.state.settings.database_path).encode()).hexdigest()[:16]
        username = f"api-test-{path_hash}"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            cache_key = str(app.state.settings.database_path)
            cached = _AUTH_CACHE.get(cache_key)
            if cached is None:
                csrf_response = await client.get("/api/v1/auth/csrf-token")
                csrf = csrf_response.json()["csrfToken"]
                response = await client.post(
                    "/api/v1/auth/login",
                    json={"username": username, "password": "test-password-123"},
                    headers={"X-CSRF-Token": csrf},
                )
                if response.status_code != 200:
                    raise AssertionError(f"Test account login failed: {response.status_code}")
                session_cookie = client.cookies.get("wl_session")
                if session_cookie is None:
                    raise AssertionError("Test login did not set a session cookie")
                cached = (session_cookie, csrf)
                _AUTH_CACHE[cache_key] = cached
            session_cookie, csrf = cached
            client.cookies.update({"wl_session": session_cookie, "wl_csrf_token": csrf})
            request_headers = dict(kwargs.pop("headers", {}))
            request_headers["X-CSRF-Token"] = csrf
            return await client.request(method, path, headers=request_headers, **kwargs)
