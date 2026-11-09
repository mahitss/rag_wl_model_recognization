"""Database migration tests.

Validates that SQLite schema migrations are idempotent, preserve data,
and produce the expected SCHEMA_VERSION. Uses isolated synthetic databases
to avoid interfering with other tests.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.auth import hash_password
from app.storage import (
    SCHEMA_VERSION,
    create_user,
    initialize_storage,
    list_documents,
    store_document,
)


def test_initialize_storage_sets_schema_version(tmp_path: Path) -> None:
    """Fresh database gets the current SCHEMA_VERSION."""
    database_path = tmp_path / "fresh.sqlite3"
    initialize_storage(database_path)

    with sqlite3.connect(database_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION


def test_initialize_storage_is_idempotent(tmp_path: Path) -> None:
    """Running initialize_storage twice does not change the version."""
    database_path = tmp_path / "idempotent.sqlite3"
    initialize_storage(database_path)
    with sqlite3.connect(database_path) as connection:
        version1 = connection.execute("PRAGMA user_version").fetchone()[0]

    initialize_storage(database_path)
    with sqlite3.connect(database_path) as connection:
        version2 = connection.execute("PRAGMA user_version").fetchone()[0]

    assert version1 == version2 == SCHEMA_VERSION


def test_schema_version_not_downgraded(tmp_path: Path) -> None:
    """Initializing over a higher version database raises RuntimeError (refuses to open)."""
    database_path = tmp_path / "downgrade.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")

    with pytest.raises(RuntimeError, match="newer than this application"):
        initialize_storage(database_path)


def test_migration_preserves_existing_data(tmp_path: Path) -> None:
    """Data created before initialize_storage is preserved."""
    database_path = tmp_path / "preserve.sqlite3"
    initialize_storage(database_path)

    user_id = create_user(database_path, "migration-user", hash_password("pass"))
    store_document(
        database_path,
        {
            "document_id": "doc-preserved",
            "display_name": "preserved.pdf",
            "sha256": "abc123",
            "byte_size": 10,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "parser_version": "test",
        },
        [
            {
                "chunk_id": "chunk-preserved",
                "page_number": 1,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": 10,
                "text": "preserved text",
            }
        ],
        user_id=user_id,
    )

    # Re-initialize (simulating a migration re-run).
    initialize_storage(database_path)

    documents = list_documents(database_path, user_id)
    assert len(documents) == 1
    assert documents[0]["document_id"] == "doc-preserved"
    assert documents[0]["display_name"] == "preserved.pdf"


def test_schema_has_expected_tables(tmp_path: Path) -> None:
    """Core tables exist after initialization."""
    database_path = tmp_path / "tables.sqlite3"
    initialize_storage(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    expected_tables = {
        "users",
        "user_sessions",
        "documents",
        "chunks",
        "chunk_embeddings",
        "quiz_sets",
        "quiz_questions",
        "quiz_attempts",
        "knowledge_concepts",
        "study_sessions",
        "topic_review_items",
        "learning_images",
        "image_regions",
        "feedback_items",
        "product_events",
        "institutions",
        "institution_memberships",
        "institution_invitations",
        "institution_departments",
        "institution_programs",
        "institution_settings",
        "institution_plan_entitlements",
        "classroom_sessions",
        "classroom_participants",
        "message_read_status",
        "notifications",
        "notification_preferences",
        "classroom_topic_performance",
        "classroom_analytics_snapshots",
        "classroom_recommendations",
        "classroom_learning_gap_trends",
        "api_integrations",
        "integration_credentials",
        "webhook_endpoints",
        "webhook_deliveries",
        "webhook_events",
        "idempotency_keys",
    }
    missing = expected_tables - tables
    assert not missing, f"Missing tables: {sorted(missing)}"


def test_foreign_keys_enabled_after_initialize(tmp_path: Path) -> None:
    """Foreign key enforcement is enabled after initialization."""
    database_path = tmp_path / "fk.sqlite3"
    initialize_storage(database_path)

    from app.storage import connect_database

    with closing(connect_database(database_path)) as connection:
        result = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    assert result == 1


def test_wal_mode_after_initialize(tmp_path: Path) -> None:
    """WAL journal mode can be set after initialization."""
    database_path = tmp_path / "wal.sqlite3"
    initialize_storage(database_path)

    from app.storage import connect_database

    with closing(connect_database(database_path)) as connection:
        result = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert result.lower() == "wal"


if __name__ == "__main__":
    pass
