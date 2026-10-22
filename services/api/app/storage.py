"""SQLite schema, readiness checks, and transactional document persistence."""
# ruff: noqa: E501

import hashlib
import math
import secrets
import sqlite3
import struct
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 10


def _user_scoped_id(prefix: str, identifier: str, user_id: str | None) -> str:
    if user_id is None:
        return identifier
    digest = hashlib.sha256(f"{user_id}\x1f{identifier}".encode()).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _database_uri(database_path: Path, *, mode: str = "rw") -> str:
    return f"{database_path.resolve().as_uri()}?mode={mode}"


def connect_database(database_path: Path) -> sqlite3.Connection:
    """Open the existing database without creating it and enable referential integrity.

    WAL mode allows concurrent readers and a single writer without blocking,
    and `normal` synchronous mode trades a small durability window for much
    faster writes.  Both are safe for the single-file SQLite deployment model.
    """
    connection = sqlite3.connect(_database_uri(database_path), uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


# ---------------------------------------------------------------------------
# User management (M23: authentication & user isolation)
# ---------------------------------------------------------------------------


def _ensure_column(connection, table_name: str, column_name: str, column_definition: str) -> None:
    """Add a column to a table only if it doesn't already exist."""
    existing = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not any(row[1] == column_name for row in existing):
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")


def create_user(
    database_path: Path, username: str, password_hash: str, email: str | None = None
) -> str:
    """Create a new user and return the user_id."""

    user_id = f"user_{secrets.token_urlsafe(16)}"
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        with connection:
            connection.execute(
                """INSERT INTO users(
                       user_id, username, email, password_hash, created_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    user_id,
                    username,
                    email or f"{user_id}@users.wonderlearn.invalid",
                    password_hash,
                    datetime.now(UTC).isoformat(),
                ),
            )
    return user_id


def get_user_by_id(database_path: Path, user_id: str) -> dict[str, Any] | None:
    """Return a user record by user_id, or None if not found."""
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            "SELECT user_id, username, email, password_hash, created_at "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "email": row["email"],
        "password_hash": row["password_hash"],
        "created_at": row["created_at"],
    }


def get_user_by_username(database_path: Path, username: str) -> dict[str, Any] | None:
    """Return a user record by username, or None if not found."""
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            "SELECT user_id, username, email, password_hash, created_at "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if row is None:
        return None
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "email": row["email"],
        "password_hash": row["password_hash"],
        "created_at": row["created_at"],
    }


def get_user_by_session(database_path: Path, session_token: str) -> dict[str, Any] | None:
    """Return the user associated with a valid session token, or None.

    Expired sessions are deleted lazily.
    """
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        row = connection.execute(
            """SELECT u.user_id, u.username, u.email, u.password_hash, u.created_at
               FROM users u
               JOIN user_sessions s ON s.user_id = u.user_id
               WHERE s.session_id = ?""",
            (session_token,),
        ).fetchone()
        if row is None:
            return None
        expires_at = connection.execute(
            "SELECT expires_at FROM user_sessions WHERE session_id = ?",
            (session_token,),
        ).fetchone()
        if expires_at is None:
            return None
        expires_at_dt = datetime.fromisoformat(expires_at["expires_at"])
        if expires_at_dt.tzinfo is None:
            expires_at_dt = expires_at_dt.replace(tzinfo=UTC)
        if expires_at_dt <= datetime.now(UTC):
            connection.execute(
                "DELETE FROM user_sessions WHERE session_id = ?",
                (session_token,),
            )
            connection.commit()
            return None
    return dict(row) if row else None


def destroy_session(database_path: Path, session_token: str) -> None:
    """Remove a session from the store (no-op if absent)."""
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        with connection:
            connection.execute("DELETE FROM user_sessions WHERE session_id = ?", (session_token,))


def cleanup_expired_sessions(database_path: Path) -> int:
    """Delete all expired sessions and return the number removed."""
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        with connection:
            cursor = connection.execute(
                "DELETE FROM user_sessions WHERE expires_at <= ?",
                (datetime.now(UTC).isoformat(),),
            )
            return cursor.rowcount


def initialize_storage(database_path: Path) -> None:
    """Create the local database and apply idempotent, data-preserving migrations."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        with connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("Database schema is newer than this application")

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
                    page_count INTEGER NOT NULL CHECK (page_count > 0),
                    chunk_count INTEGER NOT NULL CHECK (chunk_count > 0),
                    created_at TEXT NOT NULL,
                    parser_version TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL CHECK (page_number > 0),
                    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
                    char_start INTEGER NOT NULL CHECK (char_start >= 0),
                    char_end INTEGER NOT NULL CHECK (char_end > char_start),
                    text TEXT NOT NULL CHECK (length(text) > 0),
                    UNIQUE (document_id, page_number, chunk_index)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_document_page "
                "ON chunks(document_id, page_number, chunk_index)"
            )
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                USING fts5(chunk_id UNINDEXED, text, tokenize='unicode61')
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chunk_embeddings (
                    chunk_id TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    model_id TEXT NOT NULL,
                    model_revision TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
                    vector BLOB NOT NULL,
                    CHECK (length(vector) = dimensions * 4)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_config "
                "ON chunk_embeddings(config_hash, chunk_id)"
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quiz_sets (
                    quiz_id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quiz_questions (
                    question_id TEXT PRIMARY KEY,
                    quiz_id TEXT NOT NULL REFERENCES quiz_sets(quiz_id) ON DELETE CASCADE,
                    position INTEGER NOT NULL CHECK (position >= 0),
                    question_type TEXT NOT NULL CHECK (question_type IN (
                        'multiple_choice', 'short_answer'
                    )),
                    prompt TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    answer_key TEXT NOT NULL,
                    accepted_answers_json TEXT NOT NULL,
                    explanation TEXT NOT NULL,
                    UNIQUE (quiz_id, position)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quiz_citations (
                    question_id TEXT NOT NULL REFERENCES quiz_questions(
                        question_id
                    ) ON DELETE CASCADE,
                    citation_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    position INTEGER NOT NULL CHECK (position >= 0),
                    retrieval_json TEXT NOT NULL,
                    PRIMARY KEY (question_id, citation_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quiz_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    question_id TEXT NOT NULL REFERENCES quiz_questions(
                        question_id
                    ) ON DELETE CASCADE,
                    outcome TEXT NOT NULL CHECK (outcome IN ('correct', 'incorrect', 'ambiguous')),
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_concepts (
                    concept_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    aliases_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_concept_evidence (
                    concept_id TEXT NOT NULL REFERENCES knowledge_concepts(concept_id)
                        ON DELETE CASCADE,
                    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL CHECK (page_number > 0),
                    supporting_text TEXT NOT NULL CHECK (length(supporting_text) > 0),
                    PRIMARY KEY (concept_id, chunk_id, supporting_text)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_relationships (
                    relationship_id TEXT PRIMARY KEY,
                    source_concept_id TEXT NOT NULL REFERENCES knowledge_concepts(concept_id)
                        ON DELETE CASCADE,
                    target_concept_id TEXT NOT NULL REFERENCES knowledge_concepts(concept_id)
                        ON DELETE CASCADE,
                    relation TEXT NOT NULL,
                    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL CHECK (page_number > 0),
                    supporting_text TEXT NOT NULL CHECK (length(supporting_text) > 0),
                    status TEXT NOT NULL CHECK (status IN ('candidate', 'human_verified')),
                    UNIQUE(source_concept_id, target_concept_id, relation, chunk_id,
                           supporting_text)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS study_sessions (
                    session_id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_study_sessions_completed "
                "ON study_sessions(completed_at DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS topic_review_items (
                    question_id TEXT PRIMARY KEY REFERENCES quiz_questions(question_id)
                        ON DELETE CASCADE,
                    topic_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    last_attempt_id TEXT NOT NULL REFERENCES quiz_attempts(attempt_id)
                        ON DELETE CASCADE,
                    last_outcome TEXT NOT NULL CHECK (last_outcome IN (
                        'correct', 'incorrect', 'ambiguous'
                    )),
                    attempted_at TEXT NOT NULL,
                    due_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_topic_review_due "
                "ON topic_review_items(due_at, topic_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_relationship_source "
                "ON knowledge_relationships(source_concept_id, target_concept_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS study_coach_recommendations (
                    recommendation_id TEXT PRIMARY KEY,
                    plan_date TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    title TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    reasons_json TEXT NOT NULL,
                    graph_concept_id TEXT,
                    related_concept_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'planned', 'accepted', 'dismissed', 'completed'
                    )),
                    estimated_minutes INTEGER NOT NULL CHECK (estimated_minutes BETWEEN 5 AND 180),
                    updated_at TEXT NOT NULL,
                    UNIQUE(plan_date, topic_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_study_coach_date "
                "ON study_coach_recommendations(plan_date, priority DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_images (
                    image_id TEXT PRIMARY KEY,
                    display_filename TEXT NOT NULL,
                    content_type TEXT NOT NULL CHECK (content_type IN ('image/png', 'image/jpeg')),
                    width INTEGER NOT NULL CHECK (width > 0),
                    height INTEGER NOT NULL CHECK (height > 0),
                    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    ocr_status TEXT NOT NULL CHECK (ocr_status IN (
                        'available', 'empty', 'unavailable', 'failed'
                    )),
                    extracted_text TEXT,
                    image_bytes BLOB NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS image_regions (
                    region_id TEXT PRIMARY KEY,
                    image_id TEXT NOT NULL REFERENCES learning_images(image_id) ON DELETE CASCADE,
                    x REAL NOT NULL CHECK (x >= 0 AND x < 1),
                    y REAL NOT NULL CHECK (y >= 0 AND y < 1),
                    width REAL NOT NULL CHECK (width > 0 AND width <= 1 AND x + width <= 1),
                    height REAL NOT NULL CHECK (height > 0 AND height <= 1 AND y + height <= 1),
                    ocr_status TEXT NOT NULL CHECK (ocr_status IN (
                        'available', 'empty', 'unavailable', 'failed'
                    )),
                    extracted_text TEXT,
                    diagram_id TEXT,
                    concept_id TEXT,
                    concept_name TEXT,
                    created_at TEXT NOT NULL,
                    CHECK ((diagram_id IS NULL) = (concept_id IS NULL)),
                    UNIQUE(image_id, region_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_image_regions_image "
                "ON image_regions(image_id, created_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS image_region_citations (
                    region_id TEXT NOT NULL REFERENCES image_regions(region_id) ON DELETE CASCADE,
                    citation_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    position INTEGER NOT NULL CHECK (position >= 0),
                    retrieval_json TEXT NOT NULL,
                    PRIMARY KEY (region_id, citation_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quiz_questions_quiz_position "
                "ON quiz_questions(quiz_id, position)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quiz_attempts_question_time "
                "ON quiz_attempts(question_id, created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_created_at "
                "ON documents(created_at DESC, document_id ASC)"
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS remove_quiz_question_without_citations
                AFTER DELETE ON quiz_citations
                WHEN NOT EXISTS (
                    SELECT 1 FROM quiz_citations WHERE question_id = OLD.question_id
                )
                BEGIN
                    DELETE FROM quiz_questions WHERE question_id = OLD.question_id;
                END
                """
            )
            # --- M23: Authentication & user isolation ---
            # Add user_id columns to all user-owned tables (nullable for
            # backward compatibility with pre-M23 data).  Existing rows keep
            # user_id = NULL (orphaned/legacy); new writes require auth and
            # set user_id from the session.
            _ensure_column(
                connection,
                "documents",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "quiz_sets",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "quiz_attempts",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "study_sessions",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "learning_images",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "knowledge_concepts",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "study_coach_recommendations",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            # Users table
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            # User sessions table
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id)
                        ON DELETE CASCADE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at "
                "ON user_sessions(expires_at)"
            )
            # Indexes on user_id columns for query performance
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quiz_sets_user_id ON quiz_sets(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quiz_attempts_user_id ON quiz_attempts(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_study_sessions_user_id ON study_sessions(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_learning_images_user_id ON learning_images(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_concepts_user_id "
                "ON knowledge_concepts(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_study_coach_recs_user_id "
                "ON study_coach_recommendations(user_id)"
            )
            # Additional user_id columns on inherited tables for simpler queries
            _ensure_column(
                connection,
                "chunks",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "quiz_questions",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "quiz_citations",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "image_regions",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "image_region_citations",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "knowledge_concept_evidence",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "knowledge_relationships",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "chunk_embeddings",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "topic_review_items",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            # Additional indexes
            connection.execute("CREATE INDEX IF NOT EXISTS idx_chunks_user_id ON chunks(user_id)")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quiz_questions_user_id ON quiz_questions(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quiz_citations_user_id ON quiz_citations(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_image_regions_user_id ON image_regions(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_image_region_citations_user_id "
                "ON image_region_citations(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_concept_evidence_user_id "
                "ON knowledge_concept_evidence(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_relationships_user_id "
                "ON knowledge_relationships(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_user_id "
                "ON chunk_embeddings(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_topic_review_items_user_id "
                "ON topic_review_items(user_id)"
            )

            if version < 9:
                connection.execute(
                    """
                    CREATE TABLE documents_m23 (
                        document_id TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        byte_size INTEGER NOT NULL CHECK (byte_size > 0),
                        page_count INTEGER NOT NULL CHECK (page_count > 0),
                        chunk_count INTEGER NOT NULL CHECK (chunk_count > 0),
                        created_at TEXT NOT NULL,
                        parser_version TEXT NOT NULL,
                        user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE,
                        UNIQUE(user_id, sha256)
                    )
                    """
                )
                connection.execute(
                    """INSERT INTO documents_m23(
                           document_id, display_name, sha256, byte_size, page_count,
                           chunk_count, created_at, parser_version, user_id
                       )
                       SELECT document_id, display_name, sha256, byte_size, page_count,
                              chunk_count, created_at, parser_version, user_id
                       FROM documents"""
                )
                connection.execute("DROP TABLE documents")
                connection.execute("ALTER TABLE documents_m23 RENAME TO documents")
                connection.execute(
                    """
                    CREATE TABLE study_coach_recommendations_m23 (
                        recommendation_id TEXT PRIMARY KEY,
                        plan_date TEXT NOT NULL,
                        topic_id TEXT NOT NULL,
                        topic TEXT NOT NULL,
                        title TEXT NOT NULL,
                        priority INTEGER NOT NULL,
                        reasons_json TEXT NOT NULL,
                        graph_concept_id TEXT,
                        related_concept_ids_json TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN (
                            'planned', 'accepted', 'dismissed', 'completed'
                        )),
                        estimated_minutes INTEGER NOT NULL
                            CHECK (estimated_minutes BETWEEN 5 AND 180),
                        updated_at TEXT NOT NULL,
                        user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE,
                        UNIQUE(plan_date, topic_id, user_id)
                    )
                    """
                )
                connection.execute(
                    """INSERT INTO study_coach_recommendations_m23(
                           recommendation_id, plan_date, topic_id, topic, title, priority,
                           reasons_json, graph_concept_id, related_concept_ids_json,
                           status, estimated_minutes, updated_at, user_id
                       )
                       SELECT recommendation_id, plan_date, topic_id, topic, title, priority,
                              reasons_json, graph_concept_id, related_concept_ids_json,
                              status, estimated_minutes, updated_at, user_id
                       FROM study_coach_recommendations"""
                )
                connection.execute("DROP TABLE study_coach_recommendations")
                connection.execute(
                    "ALTER TABLE study_coach_recommendations_m23 "
                    "RENAME TO study_coach_recommendations"
                )
                connection.execute(
                    "UPDATE knowledge_concepts SET normalized_name = "
                    "user_id || char(31) || normalized_name "
                    "WHERE user_id IS NOT NULL"
                )
                legacy_sessions = connection.execute(
                    "SELECT session_id FROM user_sessions"
                ).fetchall()
                for row in legacy_sessions:
                    digest = hashlib.sha256(row[0].encode("utf-8")).hexdigest()
                    connection.execute(
                        "UPDATE user_sessions SET session_id=? WHERE session_id=?",
                        (digest, row[0]),
                    )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_documents_created "
                    "ON documents(created_at DESC, document_id ASC)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_study_coach_date "
                    "ON study_coach_recommendations(plan_date, priority DESC)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_study_coach_recs_user_id "
                    "ON study_coach_recommendations(user_id)"
                )

            if version < 10:
                owner_relations = (
                    ("chunks", "documents", "document_id", "document_id"),
                    ("quiz_questions", "quiz_sets", "quiz_id", "quiz_id"),
                    ("quiz_attempts", "quiz_questions", "question_id", "question_id"),
                    ("quiz_citations", "quiz_questions", "question_id", "question_id"),
                    ("image_regions", "learning_images", "image_id", "image_id"),
                    ("image_region_citations", "image_regions", "region_id", "region_id"),
                    (
                        "knowledge_concept_evidence",
                        "knowledge_concepts",
                        "concept_id",
                        "concept_id",
                    ),
                    (
                        "knowledge_relationships",
                        "knowledge_concepts",
                        "source_concept_id",
                        "concept_id",
                    ),
                    ("chunk_embeddings", "chunks", "chunk_id", "chunk_id"),
                    ("topic_review_items", "quiz_questions", "question_id", "question_id"),
                )
                for child, parent, child_key, parent_key in owner_relations:
                    mismatch = connection.execute(
                        f"""SELECT 1 FROM {child} AS child
                            JOIN {parent} AS parent
                              ON parent.{parent_key}=child.{child_key}
                            WHERE child.user_id IS NOT NULL
                              AND parent.user_id IS NOT NULL
                              AND child.user_id != parent.user_id
                            LIMIT 1"""
                    ).fetchone()
                    if mismatch is not None:
                        raise RuntimeError(
                            f"Cannot safely migrate conflicting ownership in {child}"
                        )
                    connection.execute(
                        f"""UPDATE {child}
                            SET user_id=(
                                SELECT parent.user_id FROM {parent} AS parent
                                WHERE parent.{parent_key}={child}.{child_key}
                            )
                            WHERE user_id IS NULL AND EXISTS (
                                SELECT 1 FROM {parent} AS parent
                                WHERE parent.{parent_key}={child}.{child_key}
                                  AND parent.user_id IS NOT NULL
                            )"""
                    )

                cross_owner_checks = (
                    """SELECT 1 FROM quiz_citations AS citation
                       JOIN chunks AS chunk ON chunk.chunk_id=citation.citation_id
                       WHERE citation.user_id IS NOT chunk.user_id LIMIT 1""",
                    """SELECT 1 FROM image_region_citations AS citation
                       JOIN chunks AS chunk ON chunk.chunk_id=citation.citation_id
                       WHERE citation.user_id IS NOT chunk.user_id LIMIT 1""",
                    """SELECT 1 FROM knowledge_concept_evidence AS evidence
                       JOIN chunks AS chunk ON chunk.chunk_id=evidence.chunk_id
                       WHERE evidence.user_id IS NOT chunk.user_id LIMIT 1""",
                    """SELECT 1 FROM knowledge_relationships AS relationship
                       JOIN knowledge_concepts AS target
                         ON target.concept_id=relationship.target_concept_id
                       JOIN chunks AS chunk ON chunk.chunk_id=relationship.chunk_id
                       WHERE relationship.user_id IS NOT target.user_id
                          OR relationship.user_id IS NOT chunk.user_id LIMIT 1""",
                    """SELECT 1 FROM topic_review_items AS review
                       JOIN quiz_attempts AS attempt
                         ON attempt.attempt_id=review.last_attempt_id
                       WHERE review.user_id IS NOT attempt.user_id LIMIT 1""",
                )
                if any(connection.execute(query).fetchone() for query in cross_owner_checks):
                    raise RuntimeError("Cannot safely migrate resources with conflicting ownership")

            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.execute("PRAGMA foreign_keys = ON")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("Database migration produced invalid foreign-key references")


def check_storage(database_path: Path) -> dict[str, bool]:
    """Check that the configured existing database passes integrity and supports FTS5."""
    status = {"sqlite": False, "sqlite_fts5": False}
    try:
        with closing(connect_database(database_path)) as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            status["sqlite"] = integrity is not None and integrity[0] == "ok"
            connection.execute("CREATE VIRTUAL TABLE temp.readiness_fts USING fts5(content)")
            connection.execute("DROP TABLE temp.readiness_fts")
            status["sqlite_fts5"] = True
    except (OSError, sqlite3.Error):
        return status
    return status


def store_document(
    database_path: Path,
    metadata: dict[str, Any],
    chunks: list[dict[str, Any]],
    user_id: str | None = None,
) -> str | None:
    """Insert a document and all text/FTS rows atomically; return a duplicate's ID if present."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            if user_id is None:
                existing = connection.execute(
                    "SELECT document_id FROM documents WHERE sha256=? AND user_id IS NULL",
                    (metadata["sha256"],),
                ).fetchone()
            else:
                existing = connection.execute(
                    "SELECT document_id FROM documents WHERE sha256=? AND user_id=?",
                    (metadata["sha256"], user_id),
                ).fetchone()
            if existing:
                return str(existing["document_id"])

            connection.execute(
                """
                INSERT INTO documents (
                    document_id, display_name, sha256, byte_size, page_count,
                    chunk_count, created_at, parser_version, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metadata["document_id"],
                    metadata["display_name"],
                    metadata["sha256"],
                    metadata["byte_size"],
                    metadata["page_count"],
                    metadata["chunk_count"],
                    metadata["created_at"],
                    metadata["parser_version"],
                    user_id,
                ),
            )
            if chunks:
                connection.executemany(
                    """
                    INSERT INTO chunks (
                        chunk_id, document_id, page_number, chunk_index,
                        char_start, char_end, text, user_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            chunk["chunk_id"],
                            metadata["document_id"],
                            chunk["page_number"],
                            chunk["chunk_index"],
                            chunk["char_start"],
                            chunk["char_end"],
                            chunk["text"],
                            user_id,
                        )
                        for chunk in chunks
                    ],
                )
                connection.executemany(
                    "INSERT INTO chunks_fts(chunk_id, text) VALUES (?, ?)",
                    [(chunk["chunk_id"], chunk["text"]) for chunk in chunks],
                )
    return None


def list_documents(database_path: Path, user_id: str | None = None) -> list[dict[str, Any]]:
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            rows = connection.execute(
                """
                SELECT document_id, display_name, sha256, byte_size, page_count,
                       chunk_count, created_at, parser_version
                FROM documents WHERE user_id = ?
                ORDER BY created_at DESC, document_id ASC
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT document_id, display_name, sha256, byte_size, page_count,
                       chunk_count, created_at, parser_version
                FROM documents WHERE user_id IS NULL
                ORDER BY created_at DESC, document_id ASC
                """
            ).fetchall()
    return [dict(row) for row in rows]


def get_document(
    database_path: Path, document_id: str, user_id: str | None = None
) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            row = connection.execute(
                """
                SELECT document_id, display_name, sha256, byte_size, page_count,
                       chunk_count, created_at, parser_version
                FROM documents WHERE document_id = ? AND user_id = ?
                """,
                (document_id, user_id),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT document_id, display_name, sha256, byte_size, page_count,
                       chunk_count, created_at, parser_version
                FROM documents WHERE document_id = ? AND user_id IS NULL
                """,
                (document_id,),
            ).fetchone()
    return dict(row) if row else None


def delete_document(database_path: Path, document_id: str, user_id: str | None = None) -> bool:
    with closing(connect_database(database_path)) as connection:
        with connection:
            if user_id is not None:
                owned = connection.execute(
                    "SELECT 1 FROM documents WHERE document_id=? AND user_id=?",
                    (document_id, user_id),
                ).fetchone()
            else:
                owned = connection.execute(
                    "SELECT 1 FROM documents WHERE document_id=? AND user_id IS NULL",
                    (document_id,),
                ).fetchone()
            if owned is None:
                return False
            chunk_ids = connection.execute(
                "SELECT chunk_id FROM chunks WHERE document_id = ?",
                (document_id,),
            ).fetchall()
            connection.execute("DELETE FROM documents WHERE document_id=?", (document_id,))
            connection.executemany(
                "DELETE FROM chunks_fts WHERE chunk_id = ?",
                [(row["chunk_id"],) for row in chunk_ids],
            )
            if user_id is not None:
                connection.execute(
                    "DELETE FROM knowledge_concepts WHERE user_id=? AND NOT EXISTS ("
                    "SELECT 1 FROM knowledge_concept_evidence e "
                    "WHERE e.concept_id=knowledge_concepts.concept_id"
                    ") AND NOT EXISTS (SELECT 1 FROM knowledge_relationships r "
                    "WHERE r.source_concept_id=knowledge_concepts.concept_id "
                    "OR r.target_concept_id=knowledge_concepts.concept_id)",
                    (user_id,),
                )
            else:
                connection.execute(
                    "DELETE FROM knowledge_concepts WHERE user_id IS NULL AND NOT EXISTS ("
                    "SELECT 1 FROM knowledge_concept_evidence e "
                    "WHERE e.concept_id=knowledge_concepts.concept_id"
                    ") AND NOT EXISTS (SELECT 1 FROM knowledge_relationships r "
                    "WHERE r.source_concept_id=knowledge_concepts.concept_id "
                    "OR r.target_concept_id=knowledge_concepts.concept_id)"
                )
            return True


def _image_metadata(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "imageId": row["image_id"],
        "displayFilename": row["display_filename"],
        "contentType": row["content_type"],
        "width": row["width"],
        "height": row["height"],
        "byteSize": row["byte_size"],
        "sha256": row["sha256"],
        "createdAt": row["created_at"],
        "ocrStatus": row["ocr_status"],
        "extractedText": row["extracted_text"],
    }


def store_learning_image(
    database_path: Path,
    metadata: dict[str, Any],
    image_bytes: bytes,
    user_id: str | None = None,
) -> None:
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                """INSERT INTO learning_images(
                       image_id, display_filename, content_type, width, height, byte_size,
                       sha256, created_at, ocr_status, extracted_text, image_bytes,
                       user_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    metadata["imageId"],
                    metadata["displayFilename"],
                    metadata["contentType"],
                    metadata["width"],
                    metadata["height"],
                    len(image_bytes),
                    metadata["sha256"],
                    metadata["createdAt"],
                    metadata["ocrStatus"],
                    metadata["extractedText"],
                    image_bytes,
                    user_id,
                ),
            )


def list_learning_images(database_path: Path, user_id: str | None = None) -> list[dict[str, Any]]:
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            rows = connection.execute(
                """SELECT image_id, display_filename, content_type, width, height, byte_size,
                          sha256, created_at, ocr_status, NULL AS extracted_text
                   FROM learning_images WHERE user_id = ?
                   ORDER BY created_at DESC, image_id""",
                (user_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT image_id, display_filename, content_type, width, height, byte_size,
                          sha256, created_at, ocr_status, NULL AS extracted_text
                   FROM learning_images WHERE user_id IS NULL
                   ORDER BY created_at DESC, image_id"""
            ).fetchall()
    return [_image_metadata(row) for row in rows]


def get_learning_image(
    database_path: Path, image_id: str, user_id: str | None = None
) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            row = connection.execute(
                "SELECT * FROM learning_images WHERE image_id=? AND user_id=?",
                (image_id, user_id),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM learning_images WHERE image_id=? AND user_id IS NULL",
                (image_id,),
            ).fetchone()
    if row is None:
        return None
    return {**_image_metadata(row), "imageBytes": row["image_bytes"]}


def create_learning_image_region(
    database_path: Path, region: dict[str, Any], user_id: str | None = None
) -> None:
    with closing(connect_database(database_path)) as connection:
        with connection:
            image = connection.execute(
                "SELECT 1 FROM learning_images WHERE image_id=? AND user_id IS ?",
                (region["imageId"], user_id),
            ).fetchone()
            if image is None:
                raise ValueError("Image is not owned by the requested user")
            connection.execute(
                """INSERT INTO image_regions(
                       region_id, image_id, x, y, width, height, ocr_status, extracted_text,
                       diagram_id, concept_id, concept_name, created_at, user_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    region["regionId"],
                    region["imageId"],
                    region["geometry"]["x"],
                    region["geometry"]["y"],
                    region["geometry"]["width"],
                    region["geometry"]["height"],
                    region["ocrStatus"],
                    region["extractedText"],
                    region["diagramId"],
                    region["conceptId"],
                    region["conceptName"],
                    region["createdAt"],
                    user_id,
                ),
            )


def get_learning_image_region(
    database_path: Path, image_id: str, region_id: str, user_id: str | None = None
) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            row = connection.execute(
                """SELECT region_id, image_id, x, y, width, height, ocr_status, extracted_text,
                          diagram_id, concept_id, concept_name, created_at
                   FROM image_regions WHERE image_id=? AND region_id=? AND user_id=?""",
                (image_id, region_id, user_id),
            ).fetchone()
        else:
            row = connection.execute(
                """SELECT region_id, image_id, x, y, width, height, ocr_status, extracted_text,
                          diagram_id, concept_id, concept_name, created_at
                   FROM image_regions WHERE image_id=? AND region_id=? AND user_id IS NULL""",
                (image_id, region_id),
            ).fetchone()
    if row is None:
        return None
    return {
        "regionId": row["region_id"],
        "imageId": row["image_id"],
        "geometry": {key: row[key] for key in ("x", "y", "width", "height")},
        "ocrStatus": row["ocr_status"],
        "extractedText": row["extracted_text"],
        "diagramId": row["diagram_id"],
        "conceptId": row["concept_id"],
        "conceptName": row["concept_name"],
        "createdAt": row["created_at"],
    }


def replace_image_region_citations(
    database_path: Path,
    region_id: str,
    citations: list[dict[str, Any]],
    user_id: str | None = None,
) -> None:
    import json

    with closing(connect_database(database_path)) as connection:
        with connection:
            region = connection.execute(
                "SELECT 1 FROM image_regions WHERE region_id=? AND user_id IS ?",
                (region_id, user_id),
            ).fetchone()
            if region is None:
                raise ValueError("Image region is not owned by the requested user")
            for citation in citations:
                chunk = connection.execute(
                    """SELECT 1 FROM chunks c JOIN documents d
                         ON d.document_id=c.document_id
                       WHERE c.chunk_id=? AND c.user_id IS ? AND d.user_id IS ?""",
                    (citation["citationId"], user_id, user_id),
                ).fetchone()
                if chunk is None:
                    raise ValueError("Image citation is not owned by the requested user")
            if user_id is not None:
                connection.execute(
                    "DELETE FROM image_region_citations WHERE region_id=? AND user_id=?",
                    (region_id, user_id),
                )
            else:
                connection.execute(
                    "DELETE FROM image_region_citations WHERE region_id=? AND user_id IS NULL",
                    (region_id,),
                )
            connection.executemany(
                """INSERT INTO image_region_citations(
                       region_id, citation_id, position, retrieval_json, user_id
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        region_id,
                        item["citationId"],
                        index,
                        json.dumps(item["retrieval"]),
                        user_id,
                    )
                    for index, item in enumerate(citations)
                ],
            )


def get_learning_image_detail(
    database_path: Path, image_id: str, user_id: str | None = None
) -> dict[str, Any] | None:
    import json

    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            image = connection.execute(
                """SELECT image_id, display_filename, content_type, width, height, byte_size,
                          sha256, created_at, ocr_status, extracted_text
                   FROM learning_images WHERE image_id=? AND user_id=?""",
                (image_id, user_id),
            ).fetchone()
        else:
            image = connection.execute(
                """SELECT image_id, display_filename, content_type, width, height, byte_size,
                          sha256, created_at, ocr_status, extracted_text
                   FROM learning_images WHERE image_id=? AND user_id IS NULL""",
                (image_id,),
            ).fetchone()
        if image is None:
            return None
        if user_id is not None:
            regions = connection.execute(
                """SELECT region_id, image_id, x, y, width, height, ocr_status, extracted_text,
                          diagram_id, concept_id, concept_name, created_at
                   FROM image_regions WHERE image_id=? AND user_id=? ORDER BY created_at, region_id""",
                (image_id, user_id),
            ).fetchall()
        else:
            regions = connection.execute(
                """SELECT region_id, image_id, x, y, width, height, ocr_status, extracted_text,
                          diagram_id, concept_id, concept_name, created_at
                   FROM image_regions WHERE image_id=? AND user_id IS NULL ORDER BY created_at, region_id""",
                (image_id,),
            ).fetchall()
        result = _image_metadata(image)
        result["regions"] = []
        for row in regions:
            if user_id is not None:
                citations = connection.execute(
                    """SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
                              c.chunk_index, c.char_start, c.char_end, c.text,
                              irc.retrieval_json
                       FROM image_region_citations irc
                       JOIN chunks c ON c.chunk_id=irc.citation_id
                       JOIN documents d ON d.document_id=c.document_id
                       WHERE irc.region_id=? AND irc.user_id=? AND c.user_id=?
                         AND d.user_id=? ORDER BY irc.position""",
                    (row["region_id"], user_id, user_id, user_id),
                ).fetchall()
            else:
                citations = connection.execute(
                    """SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
                              c.chunk_index, c.char_start, c.char_end, c.text,
                              irc.retrieval_json
                       FROM image_region_citations irc
                       JOIN chunks c ON c.chunk_id=irc.citation_id
                       JOIN documents d ON d.document_id=c.document_id
                       WHERE irc.region_id=? AND irc.user_id IS NULL
                         AND c.user_id IS NULL AND d.user_id IS NULL ORDER BY irc.position""",
                    (row["region_id"],),
                ).fetchall()
            result["regions"].append(
                {
                    "regionId": row["region_id"],
                    "geometry": {key: row[key] for key in ("x", "y", "width", "height")},
                    "ocrStatus": row["ocr_status"],
                    "extractedText": row["extracted_text"],
                    "diagramId": row["diagram_id"],
                    "conceptId": row["concept_id"],
                    "conceptName": row["concept_name"],
                    "citations": [
                        {
                            "citationId": item["chunk_id"],
                            "documentId": item["document_id"],
                            "displayFilename": item["display_name"],
                            "pageNumber": item["page_number"],
                            "chunkIndex": item["chunk_index"],
                            "charStart": item["char_start"],
                            "charEnd": item["char_end"],
                            "text": item["text"],
                            "retrieval": json.loads(item["retrieval_json"]),
                        }
                        for item in citations
                    ],
                }
            )
    return result


def delete_learning_image(database_path: Path, image_id: str, user_id: str | None = None) -> bool:
    with closing(connect_database(database_path)) as connection:
        with connection:
            if user_id is not None:
                result = connection.execute(
                    "DELETE FROM learning_images WHERE image_id=? AND user_id=?",
                    (image_id, user_id),
                )
            else:
                result = connection.execute(
                    "DELETE FROM learning_images WHERE image_id=? AND user_id IS NULL",
                    (image_id,),
                )
    return result.rowcount > 0


def knowledge_graph_chunks(
    database_path: Path,
    *,
    document_ids: list[str] | None,
    limit: int,
    after_chunk_id: str | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    sql = """SELECT c.chunk_id, c.document_id, d.display_name, c.page_number, c.text
             FROM chunks c JOIN documents d ON d.document_id=c.document_id"""
    parameters: list[Any] = []
    conditions = []
    if after_chunk_id is not None:
        conditions.append("c.chunk_id > ?")
        parameters.append(after_chunk_id)
    if document_ids is not None:
        if not document_ids:
            return []
        conditions.append("c.document_id IN (" + ",".join("?" for _ in document_ids) + ")")
        parameters.extend(document_ids)
    if user_id is not None:
        conditions.append("c.user_id = ?")
        parameters.append(user_id)
    else:
        conditions.append("c.user_id IS NULL")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY c.chunk_id LIMIT ?"
    parameters.append(limit)
    with closing(connect_database(database_path)) as connection:
        return [dict(row) for row in connection.execute(sql, parameters)]


def store_knowledge_extraction(
    database_path: Path,
    chunk: dict[str, Any],
    extraction: dict[str, Any],
    user_id: str | None = None,
) -> None:
    """Atomically store concepts and evidence-backed candidate relations for one chunk."""
    import json

    with closing(connect_database(database_path)) as connection:
        with connection:
            owned_chunk = connection.execute(
                """SELECT 1 FROM chunks c JOIN documents d
                     ON d.document_id=c.document_id
                   WHERE c.chunk_id=? AND c.user_id IS ? AND d.user_id IS ?""",
                (chunk["chunk_id"], user_id, user_id),
            ).fetchone()
            if owned_chunk is None:
                raise ValueError("Knowledge graph source is not owned by the requested user")
            resolved: dict[str, str] = {}
            for concept in extraction["concepts"]:
                scoped_name = (
                    concept["normalized_name"]
                    if user_id is None
                    else f"{user_id}\x1f{concept['normalized_name']}"
                )
                row = connection.execute(
                    "SELECT concept_id, aliases_json FROM knowledge_concepts "
                    "WHERE normalized_name=? AND user_id IS ?",
                    (scoped_name, user_id),
                ).fetchone()
                if row is None:
                    concept_id = _user_scoped_id("kg", concept["concept_id"], user_id)
                    connection.execute(
                        "INSERT INTO knowledge_concepts("
                        "concept_id, canonical_name, normalized_name, aliases_json, user_id) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            concept_id,
                            concept["name"],
                            scoped_name,
                            json.dumps(concept["aliases"]),
                            user_id,
                        ),
                    )
                else:
                    concept_id = row["concept_id"]
                    aliases = list(
                        dict.fromkeys(json.loads(row["aliases_json"]) + concept["aliases"])
                    )
                    connection.execute(
                        "UPDATE knowledge_concepts SET aliases_json=? WHERE concept_id=?",
                        (json.dumps(aliases), concept_id),
                    )
                resolved[concept["normalized_name"]] = concept_id
                connection.execute(
                    "INSERT OR IGNORE INTO knowledge_concept_evidence("
                    "concept_id, chunk_id, page_number, supporting_text, user_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        concept_id,
                        chunk["chunk_id"],
                        chunk["page_number"],
                        concept["supporting_text"],
                        user_id,
                    ),
                )
            for relation in extraction["relationships"]:
                source_id = resolved[relation["source_normalized"]]
                target_id = resolved[relation["target_normalized"]]
                relationship_id = _user_scoped_id("rel", relation["relationship_id"], user_id)
                connection.execute(
                    """INSERT OR IGNORE INTO knowledge_relationships(
                       relationship_id, source_concept_id, target_concept_id, relation,
                       chunk_id, page_number, supporting_text, status, user_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate', ?)""",
                    (
                        relationship_id,
                        source_id,
                        target_id,
                        relation["relation"],
                        chunk["chunk_id"],
                        chunk["page_number"],
                        relation["supporting_text"],
                        user_id,
                    ),
                )


def list_knowledge_concepts(
    database_path: Path, user_id: str | None = None
) -> list[dict[str, Any]]:
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            rows = connection.execute(
                """SELECT c.concept_id, c.canonical_name, c.aliases_json,
                          count(DISTINCT e.chunk_id) AS evidence_count,
                          count(DISTINCT r.relationship_id) AS relationship_count
                   FROM knowledge_concepts c
                   LEFT JOIN knowledge_concept_evidence e ON e.concept_id=c.concept_id
                        AND e.user_id=c.user_id
                   LEFT JOIN knowledge_relationships r ON r.source_concept_id=c.concept_id
                        AND r.user_id=c.user_id
                        OR (r.target_concept_id=c.concept_id AND r.user_id=c.user_id)
                   WHERE c.user_id = ?
                   GROUP BY c.concept_id ORDER BY c.canonical_name COLLATE NOCASE""",
                (user_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT c.concept_id, c.canonical_name, c.aliases_json,
                          count(DISTINCT e.chunk_id) AS evidence_count,
                          count(DISTINCT r.relationship_id) AS relationship_count
                   FROM knowledge_concepts c
                   LEFT JOIN knowledge_concept_evidence e ON e.concept_id=c.concept_id
                        AND e.user_id IS NULL
                   LEFT JOIN knowledge_relationships r ON r.source_concept_id=c.concept_id
                        AND r.user_id IS NULL
                        OR (r.target_concept_id=c.concept_id AND r.user_id IS NULL)
                   WHERE c.user_id IS NULL
                   GROUP BY c.concept_id ORDER BY c.canonical_name COLLATE NOCASE"""
            ).fetchall()
    import json

    return [
        {
            "conceptId": row["concept_id"],
            "name": row["canonical_name"],
            "aliases": json.loads(row["aliases_json"]),
            "evidenceCount": row["evidence_count"],
            "relationshipCount": row["relationship_count"],
        }
        for row in rows
    ]


def get_knowledge_concept(
    database_path: Path, concept_id: str, user_id: str | None = None
) -> dict[str, Any] | None:
    import json

    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            concept = connection.execute(
                "SELECT * FROM knowledge_concepts WHERE concept_id=? AND user_id=?",
                (concept_id, user_id),
            ).fetchone()
        else:
            concept = connection.execute(
                "SELECT * FROM knowledge_concepts WHERE concept_id=? AND user_id IS NULL",
                (concept_id,),
            ).fetchone()
        if concept is None:
            return None
        if user_id is not None:
            evidence = connection.execute(
                """SELECT e.chunk_id, c.document_id, d.display_name, e.page_number,
                          e.supporting_text
                     FROM knowledge_concept_evidence e JOIN chunks c ON c.chunk_id=e.chunk_id
                     JOIN documents d ON d.document_id=c.document_id
                     WHERE e.concept_id=? AND e.user_id=?
                     ORDER BY c.document_id, e.page_number, e.chunk_id""",
                (concept_id, user_id),
            ).fetchall()
            relationships = connection.execute(
                """SELECT r.relationship_id, r.source_concept_id, s.canonical_name AS source_name,
                          r.target_concept_id, t.canonical_name AS target_name, r.relation,
                          r.chunk_id, c.document_id, d.display_name, r.page_number,
                          r.supporting_text, r.status
                     FROM knowledge_relationships r
                     JOIN knowledge_concepts s ON s.concept_id=r.source_concept_id
                     JOIN knowledge_concepts t ON t.concept_id=r.target_concept_id
                     JOIN chunks c ON c.chunk_id=r.chunk_id
                     JOIN documents d ON d.document_id=c.document_id
                     WHERE (r.source_concept_id=? OR r.target_concept_id=?)
                       AND r.user_id=?
                     ORDER BY r.source_concept_id, r.target_concept_id, r.relation, r.relationship_id""",
                (concept_id, concept_id, user_id),
            ).fetchall()
        else:
            evidence = connection.execute(
                """SELECT e.chunk_id, c.document_id, d.display_name, e.page_number,
                          e.supporting_text
                     FROM knowledge_concept_evidence e JOIN chunks c ON c.chunk_id=e.chunk_id
                     JOIN documents d ON d.document_id=c.document_id
                     WHERE e.concept_id=? AND e.user_id IS NULL
                     ORDER BY c.document_id, e.page_number, e.chunk_id""",
                (concept_id,),
            ).fetchall()
            relationships = connection.execute(
                """SELECT r.relationship_id, r.source_concept_id, s.canonical_name AS source_name,
                          r.target_concept_id, t.canonical_name AS target_name, r.relation,
                          r.chunk_id, c.document_id, d.display_name, r.page_number,
                          r.supporting_text, r.status
                     FROM knowledge_relationships r
                     JOIN knowledge_concepts s ON s.concept_id=r.source_concept_id
                     JOIN knowledge_concepts t ON t.concept_id=r.target_concept_id
                     JOIN chunks c ON c.chunk_id=r.chunk_id
                     JOIN documents d ON d.document_id=c.document_id
                     WHERE r.source_concept_id=? OR r.target_concept_id=?
                       AND r.user_id IS NULL
                     ORDER BY r.source_concept_id, r.target_concept_id, r.relation, r.relationship_id""",
                (concept_id, concept_id),
            ).fetchall()
    return {
        "conceptId": concept["concept_id"],
        "name": concept["canonical_name"],
        "aliases": json.loads(concept["aliases_json"]),
        "evidence": [
            {
                "chunkId": row["chunk_id"],
                "documentId": row["document_id"],
                "displayFilename": row["display_name"],
                "pageNumber": row["page_number"],
                "supportingText": row["supporting_text"],
            }
            for row in evidence
        ],
        "relationships": [
            {
                "relationshipId": row["relationship_id"],
                "sourceConceptId": row["source_concept_id"],
                "sourceName": row["source_name"],
                "targetConceptId": row["target_concept_id"],
                "targetName": row["target_name"],
                "relation": row["relation"],
                "chunkId": row["chunk_id"],
                "documentId": row["document_id"],
                "displayFilename": row["display_name"],
                "pageNumber": row["page_number"],
                "supportingText": row["supporting_text"],
                "status": row["status"],
            }
            for row in relationships
        ],
    }


def verify_knowledge_relationship(
    database_path: Path, relationship_id: str, user_id: str | None = None
) -> bool:
    with closing(connect_database(database_path)) as connection:
        with connection:
            if user_id is not None:
                connection.execute(
                    "UPDATE knowledge_relationships SET status='human_verified' "
                    "WHERE relationship_id=? AND status='candidate' AND user_id=?",
                    (relationship_id, user_id),
                )
                row = connection.execute(
                    "SELECT status FROM knowledge_relationships "
                    "WHERE relationship_id=? AND user_id=?",
                    (relationship_id, user_id),
                ).fetchone()
            else:
                connection.execute(
                    "UPDATE knowledge_relationships SET status='human_verified' "
                    "WHERE relationship_id=? AND status='candidate' AND user_id IS NULL",
                    (relationship_id,),
                )
                row = connection.execute(
                    "SELECT status FROM knowledge_relationships "
                    "WHERE relationship_id=? AND user_id IS NULL",
                    (relationship_id,),
                ).fetchone()
    return row is not None and row["status"] == "human_verified"


def search_chunks(
    database_path: Path,
    fts_query: str,
    document_ids: list[str] | None,
    limit: int,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
               c.chunk_index, c.char_start, c.char_end, c.text,
               bm25(chunks_fts) AS rank
        FROM chunks_fts
        JOIN chunks AS c ON c.chunk_id = chunks_fts.chunk_id
        JOIN documents AS d ON d.document_id = c.document_id
        WHERE chunks_fts MATCH ?
    """
    parameters: list[Any] = [fts_query]
    if document_ids is not None:
        placeholders = ",".join("?" for _ in document_ids)
        sql += f" AND c.document_id IN ({placeholders})"
        parameters.extend(document_ids)
    if user_id is not None:
        sql += " AND c.user_id = ?"
        parameters.append(user_id)
    else:
        sql += " AND c.user_id IS NULL"
    sql += " ORDER BY rank ASC, c.document_id ASC, c.page_number ASC, c.chunk_index ASC LIMIT ?"
    parameters.append(limit)

    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(sql, parameters).fetchall()
    return [dict(row) for row in rows]


def create_quiz_set(database_path: Path, quiz: dict[str, Any], user_id: str | None = None) -> None:
    """Persist generated questions and foreign-key citations atomically."""
    import json

    with closing(connect_database(database_path)) as connection:
        with connection:
            for question in quiz["questions"]:
                for citation in question["citations"]:
                    owned_chunk = connection.execute(
                        """SELECT 1 FROM chunks c JOIN documents d
                             ON d.document_id=c.document_id
                           WHERE c.chunk_id=? AND c.user_id IS ? AND d.user_id IS ?""",
                        (citation["citationId"], user_id, user_id),
                    ).fetchone()
                    if owned_chunk is None:
                        raise ValueError("Quiz citation is not owned by the requested user")
            connection.execute(
                "INSERT INTO quiz_sets(quiz_id, topic_id, topic, created_at, user_id) VALUES (?, ?, ?, ?, ?)",
                (quiz["quiz_id"], quiz["topic_id"], quiz["topic"], quiz["created_at"], user_id),
            )
            for position, question in enumerate(quiz["questions"]):
                connection.execute(
                    """
                    INSERT INTO quiz_questions(
                        question_id, quiz_id, position, question_type, prompt,
                        options_json, answer_key, accepted_answers_json, explanation,
                        user_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        question["question_id"],
                        quiz["quiz_id"],
                        position,
                        question["question_type"],
                        question["prompt"],
                        json.dumps(question["options"]),
                        question["answer_key"],
                        json.dumps(question["accepted_answers"]),
                        question["explanation"],
                        user_id,
                    ),
                )
                for citation_position, citation in enumerate(question["citations"]):
                    connection.execute(
                        """
                        INSERT INTO quiz_citations(
                            question_id, citation_id, position, retrieval_json, user_id
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            question["question_id"],
                            citation["citationId"],
                            citation_position,
                            json.dumps(citation["retrieval"]),
                            user_id,
                        ),
                    )


def get_quiz_set(
    database_path: Path,
    quiz_id: str,
    *,
    review_incorrect: bool = False,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    """Load a quiz with current source provenance and latest attempt outcomes."""
    import json

    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            quiz = connection.execute(
                "SELECT quiz_id, topic_id, topic, created_at FROM quiz_sets WHERE quiz_id = ? AND user_id = ?",
                (quiz_id, user_id),
            ).fetchone()
        else:
            quiz = connection.execute(
                "SELECT quiz_id, topic_id, topic, created_at FROM quiz_sets WHERE quiz_id = ? AND user_id IS NULL",
                (quiz_id,),
            ).fetchone()
        if quiz is None:
            return None
        if user_id is not None:
            rows = connection.execute(
                """
                SELECT q.*, latest.outcome AS latest_outcome,
                       (SELECT count(*) FROM quiz_attempts a
                        WHERE a.question_id=q.question_id AND a.user_id=?) AS attempt_count
                FROM quiz_questions q
                LEFT JOIN (
                    SELECT question_id, outcome,
                           row_number() OVER (
                               PARTITION BY question_id
                               ORDER BY created_at DESC, rowid DESC
                           ) AS rn
                    FROM quiz_attempts
                    WHERE user_id = ?
                ) latest ON latest.question_id=q.question_id AND latest.rn=1
                WHERE q.quiz_id=? AND q.user_id=?
                ORDER BY q.position
                """,
                (user_id, user_id, quiz_id, user_id),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT q.*, latest.outcome AS latest_outcome,
                       (SELECT count(*) FROM quiz_attempts a
                        WHERE a.question_id=q.question_id) AS attempt_count
                FROM quiz_questions q
                LEFT JOIN (
                    SELECT question_id, outcome,
                           row_number() OVER (
                               PARTITION BY question_id
                               ORDER BY created_at DESC, rowid DESC
                           ) AS rn
                    FROM quiz_attempts
                ) latest ON latest.question_id=q.question_id AND latest.rn=1
                WHERE q.quiz_id=? AND q.user_id IS NULL
                ORDER BY q.position
                """,
                (quiz_id,),
            ).fetchall()
        questions = []
        for row in rows:
            if review_incorrect and row["latest_outcome"] not in {"incorrect", "ambiguous"}:
                continue
            if user_id is None:
                citation_rows = connection.execute(
                    """SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
                              c.chunk_index, c.char_start, c.char_end, c.text,
                              qc.retrieval_json
                       FROM quiz_citations qc
                       JOIN chunks c ON c.chunk_id=qc.citation_id
                       JOIN documents d ON d.document_id=c.document_id
                       WHERE qc.question_id=? AND qc.user_id IS NULL
                         AND c.user_id IS NULL AND d.user_id IS NULL
                       ORDER BY qc.position""",
                    (row["question_id"],),
                ).fetchall()
            else:
                citation_rows = connection.execute(
                    """SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
                              c.chunk_index, c.char_start, c.char_end, c.text,
                              qc.retrieval_json
                       FROM quiz_citations qc
                       JOIN chunks c ON c.chunk_id=qc.citation_id
                       JOIN documents d ON d.document_id=c.document_id
                       WHERE qc.question_id=? AND qc.user_id=?
                         AND c.user_id=? AND d.user_id=?
                       ORDER BY qc.position""",
                    (row["question_id"], user_id, user_id, user_id),
                ).fetchall()
            citations = [
                {
                    "citationId": citation["chunk_id"],
                    "documentId": citation["document_id"],
                    "displayFilename": citation["display_name"],
                    "pageNumber": citation["page_number"],
                    "chunkIndex": citation["chunk_index"],
                    "charStart": citation["char_start"],
                    "charEnd": citation["char_end"],
                    "text": citation["text"],
                    "retrieval": json.loads(citation["retrieval_json"]),
                }
                for citation in citation_rows
            ]
            if not citations:
                continue
            has_attempt = row["latest_outcome"] is not None
            questions.append(
                {
                    "questionId": row["question_id"],
                    "questionType": row["question_type"],
                    "prompt": row["prompt"],
                    "options": json.loads(row["options_json"]),
                    "explanation": row["explanation"] if has_attempt else None,
                    "citations": citations if has_attempt else [],
                    "latestOutcome": row["latest_outcome"],
                    "attemptCount": row["attempt_count"],
                }
            )
        return {
            "quizId": quiz["quiz_id"],
            "topicId": quiz["topic_id"],
            "topic": quiz["topic"],
            "createdAt": quiz["created_at"],
            "questions": questions,
        }


def get_quiz_question(
    database_path: Path, question_id: str, user_id: str | None = None
) -> dict[str, Any] | None:
    import json

    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            row = connection.execute(
                "SELECT * FROM quiz_questions WHERE question_id=? AND user_id=?",
                (question_id, user_id),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM quiz_questions WHERE question_id=? AND user_id IS NULL",
                (question_id,),
            ).fetchone()
    if row is None:
        return None
    return {
        "question_id": row["question_id"],
        "quiz_id": row["quiz_id"],
        "question_type": row["question_type"],
        "options": json.loads(row["options_json"]),
        "answer_key": row["answer_key"],
        "accepted_answers": json.loads(row["accepted_answers_json"]),
        "explanation": row["explanation"],
    }


def record_quiz_attempt(
    database_path: Path,
    *,
    attempt_id: str,
    question_id: str,
    outcome: str,
    created_at: str,
    user_id: str | None = None,
) -> None:
    with closing(connect_database(database_path)) as connection:
        with connection:
            question = connection.execute(
                """SELECT s.topic_id, s.topic FROM quiz_questions q
                   JOIN quiz_sets s ON s.quiz_id=q.quiz_id
                   WHERE q.question_id=? AND q.user_id IS ? AND s.user_id IS ?""",
                (question_id, user_id, user_id),
            ).fetchone()
            if question is None:
                raise ValueError("Quiz question is not owned by the requested user")
            connection.execute(
                """
                INSERT INTO quiz_attempts(attempt_id, question_id, outcome, created_at, user_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (attempt_id, question_id, outcome, created_at, user_id),
            )
            from datetime import UTC, datetime, timedelta

            interval_days = {"incorrect": 1, "ambiguous": 1, "correct": 7}[outcome]
            attempted_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if attempted_at.tzinfo is None:
                attempted_at = attempted_at.replace(tzinfo=UTC)
            due_at = (attempted_at + timedelta(days=interval_days)).astimezone(UTC).isoformat()
            connection.execute(
                """INSERT INTO topic_review_items(
                           question_id, topic_id, topic, last_attempt_id, last_outcome,
                           attempted_at, due_at, user_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(question_id) DO UPDATE SET
                           topic_id=excluded.topic_id, topic=excluded.topic,
                           last_attempt_id=excluded.last_attempt_id,
                           last_outcome=excluded.last_outcome,
                           attempted_at=excluded.attempted_at, due_at=excluded.due_at""",
                (
                    question_id,
                    question["topic_id"],
                    question["topic"],
                    attempt_id,
                    outcome,
                    created_at,
                    due_at,
                    user_id,
                ),
            )


def start_study_session(
    database_path: Path,
    *,
    session_id: str,
    topic_id: str,
    topic: str,
    started_at: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                "INSERT INTO study_sessions("
                "session_id, topic_id, topic, started_at, user_id) VALUES (?, ?, ?, ?, ?)",
                (session_id, topic_id, topic, started_at, user_id),
            )
    return {
        "sessionId": session_id,
        "topicId": topic_id,
        "topic": topic,
        "startedAt": started_at,
        "completedAt": None,
    }


def complete_study_session(
    database_path: Path,
    *,
    session_id: str,
    completed_at: str,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        with connection:
            if user_id is not None:
                connection.execute(
                    "UPDATE study_sessions SET completed_at=? "
                    "WHERE session_id=? AND completed_at IS NULL AND user_id=?",
                    (completed_at, session_id, user_id),
                )
            else:
                connection.execute(
                    "UPDATE study_sessions SET completed_at=? "
                    "WHERE session_id=? AND completed_at IS NULL AND user_id IS NULL",
                    (completed_at, session_id),
                )
            row = connection.execute(
                "SELECT session_id, topic_id, topic, started_at, completed_at "
                "FROM study_sessions WHERE session_id=? AND user_id IS ?",
                (session_id, user_id),
            ).fetchone()
    if row is None:
        return None
    return {
        "sessionId": row["session_id"],
        "topicId": row["topic_id"],
        "topic": row["topic"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
    }


def get_learning_analytics_data(database_path: Path, user_id: str | None = None) -> dict[str, Any]:
    import json

    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            attempts = [
                dict(row)
                for row in connection.execute(
                    """SELECT a.rowid AS sequence, a.attempt_id AS attemptId,
                              a.question_id AS questionId,
                              a.outcome, a.created_at AS createdAt, q.prompt,
                              s.topic_id AS topicId, s.topic
                       FROM quiz_attempts a JOIN quiz_questions q ON q.question_id=a.question_id
                       JOIN quiz_sets s ON s.quiz_id=q.quiz_id
                       WHERE a.user_id = ? AND q.user_id = ? AND s.user_id = ?
                       ORDER BY a.created_at DESC, a.rowid DESC""",
                    (user_id, user_id, user_id),
                )
            ]
            sessions = [
                {
                    "sessionId": row["session_id"],
                    "topicId": row["topic_id"],
                    "topic": row["topic"],
                    "startedAt": row["started_at"],
                    "completedAt": row["completed_at"],
                }
                for row in connection.execute(
                    "SELECT * FROM study_sessions WHERE user_id=? ORDER BY started_at DESC, rowid DESC",
                    (user_id,),
                )
            ]
            reviews = [
                {
                    "questionId": row["question_id"],
                    "topicId": row["topic_id"],
                    "topic": row["topic"],
                    "outcome": row["last_outcome"],
                    "attemptedAt": row["attempted_at"],
                    "dueAt": row["due_at"],
                }
                for row in connection.execute(
                    "SELECT * FROM topic_review_items WHERE user_id=? ORDER BY due_at, question_id",
                    (user_id,),
                )
            ]
            source_rows = connection.execute(
                """SELECT s.topic_id, qc.citation_id AS chunk_id,
                          MIN(qc.retrieval_json) AS retrieval_json, c.document_id,
                          d.display_name, c.page_number, c.chunk_index, c.char_start,
                          c.char_end, c.text
                 FROM quiz_sets s JOIN quiz_questions q ON q.quiz_id=s.quiz_id
                 JOIN quiz_citations qc ON qc.question_id=q.question_id
                 JOIN chunks c ON c.chunk_id=qc.citation_id
                 JOIN documents d ON d.document_id=c.document_id
                 WHERE s.user_id=? AND q.user_id=? AND qc.user_id=?
                   AND c.user_id=? AND d.user_id=?
                 GROUP BY s.topic_id, qc.citation_id
                 ORDER BY s.topic_id, c.document_id, c.page_number, c.chunk_index""",
                (user_id, user_id, user_id, user_id, user_id),
            ).fetchall()
            concepts = [
                {
                    "conceptId": row["concept_id"],
                    "name": row["canonical_name"],
                    "aliases": json.loads(row["aliases_json"]),
                }
                for row in connection.execute(
                    "SELECT concept_id, canonical_name, aliases_json FROM knowledge_concepts WHERE user_id=?",
                    (user_id,),
                )
            ]
            relationships = [
                {
                    "relationshipId": row["relationship_id"],
                    "sourceConceptId": row["source_concept_id"],
                    "targetConceptId": row["target_concept_id"],
                    "relation": row["relation"],
                    "status": row["status"],
                }
                for row in connection.execute(
                    """SELECT relationship_id, source_concept_id, target_concept_id, relation, status
                       FROM knowledge_relationships WHERE user_id=? ORDER BY relationship_id""",
                    (user_id,),
                )
            ]
        else:
            attempts = [
                dict(row)
                for row in connection.execute(
                    """SELECT a.rowid AS sequence, a.attempt_id AS attemptId,
                              a.question_id AS questionId,
                              a.outcome, a.created_at AS createdAt, q.prompt,
                              s.topic_id AS topicId, s.topic
                       FROM quiz_attempts a JOIN quiz_questions q ON q.question_id=a.question_id
                       JOIN quiz_sets s ON s.quiz_id=q.quiz_id
                       WHERE a.user_id IS NULL AND s.user_id IS NULL
                       ORDER BY a.created_at DESC, a.rowid DESC"""
                )
            ]
            sessions = [
                {
                    "sessionId": row["session_id"],
                    "topicId": row["topic_id"],
                    "topic": row["topic"],
                    "startedAt": row["started_at"],
                    "completedAt": row["completed_at"],
                }
                for row in connection.execute(
                    "SELECT * FROM study_sessions WHERE user_id IS NULL ORDER BY started_at DESC, rowid DESC"
                )
            ]
            reviews = [
                {
                    "questionId": row["question_id"],
                    "topicId": row["topic_id"],
                    "topic": row["topic"],
                    "outcome": row["last_outcome"],
                    "attemptedAt": row["attempted_at"],
                    "dueAt": row["due_at"],
                }
                for row in connection.execute(
                    "SELECT * FROM topic_review_items WHERE user_id IS NULL ORDER BY due_at, question_id"
                )
            ]
            source_rows = connection.execute(
                """SELECT s.topic_id, qc.citation_id AS chunk_id,
                          MIN(qc.retrieval_json) AS retrieval_json, c.document_id,
                          d.display_name, c.page_number, c.chunk_index, c.char_start,
                          c.char_end, c.text
                   FROM quiz_sets s JOIN quiz_questions q ON q.quiz_id=s.quiz_id
                   JOIN quiz_citations qc ON qc.question_id=q.question_id
                   JOIN chunks c ON c.chunk_id=qc.citation_id
                   JOIN documents d ON d.document_id=c.document_id
                   WHERE s.user_id IS NULL AND q.user_id IS NULL
                     AND qc.user_id IS NULL AND c.user_id IS NULL AND d.user_id IS NULL
                   GROUP BY s.topic_id, qc.citation_id
                   ORDER BY s.topic_id, c.document_id, c.page_number, c.chunk_index"""
            ).fetchall()
            concepts = [
                {
                    "conceptId": row["concept_id"],
                    "name": row["canonical_name"],
                    "aliases": json.loads(row["aliases_json"]),
                }
                for row in connection.execute(
                    "SELECT concept_id, canonical_name, aliases_json FROM knowledge_concepts WHERE user_id IS NULL"
                )
            ]
            relationships = [
                {
                    "relationshipId": row["relationship_id"],
                    "sourceConceptId": row["source_concept_id"],
                    "targetConceptId": row["target_concept_id"],
                    "relation": row["relation"],
                    "status": row["status"],
                }
                for row in connection.execute(
                    """SELECT relationship_id, source_concept_id, target_concept_id, relation, status
                           FROM knowledge_relationships WHERE user_id IS NULL ORDER BY relationship_id"""
                )
            ]

        topic_sources: dict[str, list[dict[str, Any]]] = {}
        for row in source_rows:
            citations = topic_sources.setdefault(row["topic_id"], [])
            if len(citations) >= 5:
                continue
            citations.append(
                {
                    "citationId": row["chunk_id"],
                    "documentId": row["document_id"],
                    "displayFilename": row["display_name"],
                    "pageNumber": row["page_number"],
                    "chunkIndex": row["chunk_index"],
                    "charStart": row["char_start"],
                    "charEnd": row["char_end"],
                    "text": row["text"],
                    "retrieval": json.loads(row["retrieval_json"]),
                }
            )
        topics = []
        seen_topic_ids = set()
        for review in reviews:
            if review["topicId"] not in seen_topic_ids:
                seen_topic_ids.add(review["topicId"])
                topics.append({"topicId": review["topicId"], "topic": review["topic"]})
        return {
            "attempts": attempts,
            "sessions": sessions,
            "reviews": reviews,
            "topicSources": topic_sources,
            "topics": topics,
            "concepts": concepts,
            "relationships": relationships,
        }


def get_or_create_study_coach_plan(
    database_path: Path,
    *,
    plan_date: str,
    recommendations: list[dict[str, Any]],
    updated_at: str,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Persist the deterministic daily plan once and preserve learner decisions."""
    import json

    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.executemany(
                """INSERT OR IGNORE INTO study_coach_recommendations(
                       recommendation_id, plan_date, topic_id, topic, title, priority,
                       reasons_json, graph_concept_id, related_concept_ids_json, status,
                       estimated_minutes, updated_at, user_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?)""",
                [
                    (
                        item["recommendationId"],
                        plan_date,
                        item["topicId"],
                        item["topic"],
                        item["title"],
                        item["priority"],
                        json.dumps(item["reasons"]),
                        item["graphConceptId"],
                        json.dumps(item["relatedConceptIds"]),
                        item["estimatedMinutes"],
                        updated_at,
                        user_id,
                    )
                    for item in recommendations
                ],
            )
        if user_id is not None:
            rows = connection.execute(
                """SELECT * FROM study_coach_recommendations WHERE plan_date=? AND user_id=?
                   ORDER BY priority DESC, topic COLLATE NOCASE, recommendation_id""",
                (plan_date, user_id),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT * FROM study_coach_recommendations WHERE plan_date=? AND user_id IS NULL
                   ORDER BY priority DESC, topic COLLATE NOCASE, recommendation_id""",
                (plan_date,),
            ).fetchall()
    return [
        {
            "recommendationId": row["recommendation_id"],
            "planDate": row["plan_date"],
            "topicId": row["topic_id"],
            "topic": row["topic"],
            "title": row["title"],
            "priority": row["priority"],
            "reasons": json.loads(row["reasons_json"]),
            "graphConceptId": row["graph_concept_id"],
            "relatedConceptIds": json.loads(row["related_concept_ids_json"]),
            "status": row["status"],
            "estimatedMinutes": row["estimated_minutes"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def update_study_coach_recommendation(
    database_path: Path,
    *,
    recommendation_id: str,
    action: str,
    updated_at: str,
    title: str | None = None,
    estimated_minutes: int | None = None,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    """Apply an explicit learner decision to one persisted plan item."""
    import json

    with closing(connect_database(database_path)) as connection:
        with connection:
            row = connection.execute(
                "SELECT * FROM study_coach_recommendations "
                "WHERE recommendation_id=? AND user_id IS ?",
                (recommendation_id, user_id),
            ).fetchone()
            if row is None:
                return None
            status = {
                "accept": "accepted",
                "edit": "accepted",
                "dismiss": "dismissed",
                "complete": "completed",
            }[action]
            connection.execute(
                """UPDATE study_coach_recommendations
                   SET status=?, title=?, estimated_minutes=?, updated_at=?
                   WHERE recommendation_id=? AND user_id IS ? """,
                (
                    status,
                    title if title is not None else row["title"],
                    estimated_minutes
                    if estimated_minutes is not None
                    else row["estimated_minutes"],
                    updated_at,
                    recommendation_id,
                    user_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM study_coach_recommendations "
                "WHERE recommendation_id=? AND user_id IS ?",
                (recommendation_id, user_id),
            ).fetchone()
    if updated is None:
        return None
    return {
        "recommendationId": updated["recommendation_id"],
        "planDate": updated["plan_date"],
        "topicId": updated["topic_id"],
        "topic": updated["topic"],
        "title": updated["title"],
        "priority": updated["priority"],
        "reasons": json.loads(updated["reasons_json"]),
        "graphConceptId": updated["graph_concept_id"],
        "relatedConceptIds": json.loads(updated["related_concept_ids_json"]),
        "status": updated["status"],
        "estimatedMinutes": updated["estimated_minutes"],
        "updatedAt": updated["updated_at"],
    }


def get_learning_progress(database_path: Path, user_id: str | None = None) -> dict[str, Any]:
    """Return local topic mastery based on each question's latest attempt and recent history."""
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            topic_rows = connection.execute(
                """
                WITH latest AS (
                    SELECT a.question_id, a.outcome,
                           row_number() OVER (
                               PARTITION BY a.question_id
                               ORDER BY a.created_at DESC, a.rowid DESC
                           ) AS rn
                    FROM quiz_attempts a
                    WHERE a.user_id = ?
                ), counts AS (
                    SELECT s.topic_id, s.topic, count(DISTINCT q.question_id) AS attempted_questions,
                           sum(CASE WHEN latest.outcome='correct' THEN 1 ELSE 0 END)
                               AS correct_questions,
                           sum(CASE WHEN latest.outcome='incorrect' THEN 1 ELSE 0 END)
                               AS incorrect_questions,
                           sum(CASE WHEN latest.outcome='ambiguous' THEN 1 ELSE 0 END)
                               AS ambiguous_questions
                    FROM quiz_sets s
                    JOIN quiz_questions q ON q.quiz_id=s.quiz_id
                    JOIN latest ON latest.question_id=q.question_id AND latest.rn=1
                    WHERE s.user_id = ? AND q.user_id = ?
                    GROUP BY s.topic_id, s.topic
                )
                SELECT *, round(1.0 * correct_questions / attempted_questions, 3) AS accuracy
                FROM counts ORDER BY accuracy ASC, topic COLLATE NOCASE
                """,
                (user_id, user_id, user_id),
            ).fetchall()
            attempt_total = int(
                connection.execute(
                    "SELECT count(*) FROM quiz_attempts WHERE user_id = ?", (user_id,)
                ).fetchone()[0]
            )
            recent_rows = connection.execute(
                """
                SELECT a.attempt_id, a.outcome, a.created_at, q.prompt, s.topic_id, s.topic
                FROM quiz_attempts a
                JOIN quiz_questions q ON q.question_id=a.question_id
                JOIN quiz_sets s ON s.quiz_id=q.quiz_id
                WHERE a.user_id = ? AND q.user_id = ? AND s.user_id = ?
                ORDER BY a.created_at DESC, a.rowid DESC LIMIT 100
                """,
                (user_id, user_id, user_id),
            ).fetchall()
        else:
            topic_rows = connection.execute(
                """
                WITH latest AS (
                    SELECT a.question_id, a.outcome,
                           row_number() OVER (
                               PARTITION BY a.question_id
                               ORDER BY a.created_at DESC, a.rowid DESC
                           ) AS rn
                    FROM quiz_attempts a
                    WHERE a.user_id IS NULL
                ), counts AS (
                    SELECT s.topic_id, s.topic, count(DISTINCT q.question_id) AS attempted_questions,
                           sum(CASE WHEN latest.outcome='correct' THEN 1 ELSE 0 END)
                               AS correct_questions,
                           sum(CASE WHEN latest.outcome='incorrect' THEN 1 ELSE 0 END)
                               AS incorrect_questions,
                           sum(CASE WHEN latest.outcome='ambiguous' THEN 1 ELSE 0 END)
                               AS ambiguous_questions
                    FROM quiz_sets s
                    JOIN quiz_questions q ON q.quiz_id=s.quiz_id
                    JOIN latest ON latest.question_id=q.question_id AND latest.rn=1
                    GROUP BY s.topic_id, s.topic
                )
                SELECT *, round(1.0 * correct_questions / attempted_questions, 3) AS accuracy
                FROM counts ORDER BY accuracy ASC, topic COLLATE NOCASE
                """
            ).fetchall()
            attempt_total = int(
                connection.execute(
                    "SELECT count(*) FROM quiz_attempts WHERE user_id IS NULL"
                ).fetchone()[0]
            )
            recent_rows = connection.execute(
                """
                SELECT a.attempt_id, a.outcome, a.created_at, q.prompt, s.topic_id, s.topic
                FROM quiz_attempts a
                JOIN quiz_questions q ON q.question_id=a.question_id
                JOIN quiz_sets s ON s.quiz_id=q.quiz_id
                WHERE a.user_id IS NULL AND q.user_id IS NULL AND s.user_id IS NULL
                ORDER BY a.created_at DESC, a.rowid DESC LIMIT 100
                """
            ).fetchall()
    topics = [
        {
            "topicId": row["topic_id"],
            "topic": row["topic"],
            "attemptedQuestions": row["attempted_questions"],
            "correctQuestions": row["correct_questions"],
            "incorrectQuestions": row["incorrect_questions"],
            "ambiguousQuestions": row["ambiguous_questions"],
            "accuracy": row["accuracy"],
            "needsReview": row["incorrect_questions"] > 0 or row["ambiguous_questions"] > 0,
        }
        for row in topic_rows
    ]
    return {
        "topics": topics,
        "totalAttempts": attempt_total,
        "recentAttempts": [dict(row) for row in recent_rows],
    }


def fetch_chunk_batch(
    database_path: Path,
    *,
    after_chunk_id: str | None,
    limit: int,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read a bounded chunk batch for incremental embedding indexing."""
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            rows = connection.execute(
                """
                SELECT c.chunk_id, c.text, e.model_id, e.model_revision, e.config_hash,
                       e.dimensions, e.vector
                FROM chunks AS c
                LEFT JOIN chunk_embeddings AS e ON e.chunk_id = c.chunk_id
                    AND e.user_id IS c.user_id
                WHERE (? IS NULL OR c.chunk_id > ?) AND c.user_id = ?
                ORDER BY c.chunk_id ASC
                LIMIT ?
                """,
                (after_chunk_id, after_chunk_id, user_id, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT c.chunk_id, c.text, e.model_id, e.model_revision, e.config_hash,
                       e.dimensions, e.vector
                FROM chunks AS c
                LEFT JOIN chunk_embeddings AS e ON e.chunk_id = c.chunk_id
                    AND e.user_id IS NULL
                WHERE (? IS NULL OR c.chunk_id > ?) AND c.user_id IS NULL
                ORDER BY c.chunk_id ASC
                LIMIT ?
                """,
                (after_chunk_id, after_chunk_id, limit),
            ).fetchall()
    return [dict(row) for row in rows]


def store_chunk_embeddings(
    database_path: Path,
    entries: list[dict[str, Any]],
    user_id: str | None = None,
) -> None:
    """Atomically upsert a batch of embeddings without touching source chunks or FTS."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            for entry in entries:
                if user_id is None:
                    chunk = connection.execute(
                        "SELECT 1 FROM chunks WHERE chunk_id=? AND user_id IS NULL",
                        (entry["chunk_id"],),
                    ).fetchone()
                else:
                    chunk = connection.execute(
                        "SELECT 1 FROM chunks WHERE chunk_id=? AND user_id=?",
                        (entry["chunk_id"], user_id),
                    ).fetchone()
                if chunk is None:
                    raise ValueError("Embedding chunk is not owned by the requested user")
            connection.executemany(
                """
                INSERT INTO chunk_embeddings(
                    chunk_id, model_id, model_revision, config_hash, dimensions, vector,
                    user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    model_id=excluded.model_id,
                    model_revision=excluded.model_revision,
                    config_hash=excluded.config_hash,
                    dimensions=excluded.dimensions,
                    vector=excluded.vector,
                    user_id=excluded.user_id
                """,
                [
                    (
                        entry["chunk_id"],
                        entry["model_id"],
                        entry["model_revision"],
                        entry["config_hash"],
                        entry["dimensions"],
                        entry["vector"],
                        user_id,
                    )
                    for entry in entries
                ],
            )


def is_valid_normalized_vector_blob(vector_bytes: bytes | None, dimensions: int) -> bool:
    """Return whether a stored vector has the expected float32 shape and unit norm."""
    if not isinstance(vector_bytes, (bytes, bytearray, memoryview)):
        return False
    try:
        if len(vector_bytes) != dimensions * 4:
            return False
        vector = struct.unpack(f"<{dimensions}f", vector_bytes)
    except (TypeError, struct.error):
        return False
    norm = math.hypot(*vector)
    return all(math.isfinite(value) for value in vector) and math.isclose(norm, 1.0, abs_tol=1e-3)


def embedding_index_counts(
    database_path: Path,
    *,
    config_hash: str,
    dimensions: int,
    user_id: str | None = None,
) -> dict[str, int]:
    """Return total, current, stale, and missing vector counts for one config."""
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            total = int(
                connection.execute(
                    "SELECT count(*) FROM chunks WHERE user_id = ?", (user_id,)
                ).fetchone()[0]
            )
            vectors = connection.execute(
                "SELECT config_hash, dimensions, vector FROM chunk_embeddings WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        else:
            total = int(
                connection.execute("SELECT count(*) FROM chunks WHERE user_id IS NULL").fetchone()[
                    0
                ]
            )
            vectors = connection.execute(
                "SELECT config_hash, dimensions, vector FROM chunk_embeddings WHERE user_id IS NULL"
            ).fetchall()
    indexed = len(vectors)
    current = 0
    for row in vectors:
        vector_bytes = row["vector"]
        if (
            row["config_hash"] != config_hash
            or row["dimensions"] != dimensions
            or not is_valid_normalized_vector_blob(vector_bytes, dimensions)
        ):
            continue
        current += 1
    return {
        "total_chunks": total,
        "indexed_chunks": indexed,
        "current_chunks": current,
        "stale_chunks": indexed - current,
        "missing_chunks": total - current,
    }


def search_semantic_chunks(
    database_path: Path,
    *,
    config_hash: str,
    dimensions: int,
    query_vector: tuple[float, ...],
    limit: int,
    document_ids: list[str] | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Exact cosine search over normalized vectors.

    Two-phase execution: phase 1 fetches only chunk ids and vectors and keeps a
    bounded heap of the top-k candidates.  Phase 2 fetches full chunk details
    only for the selected candidates, avoiding materializing every row's text
    and vector in Python for large indexes.
    """
    import heapq

    detail_sql = """
        SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
               c.chunk_index, c.char_start, c.char_end, c.text
        FROM chunks AS c
        JOIN documents AS d ON d.document_id = c.document_id
        WHERE c.chunk_id IN ({placeholders})
    """

    with closing(connect_database(database_path)) as connection:
        # Phase 1: compute top-k similarities over id+vector pairs only.
        heap: list[tuple[float, str]] = []
        params = [config_hash, dimensions, dimensions * 4]
        vector_sql = """
            SELECT e.chunk_id, e.vector
            FROM chunk_embeddings AS e
            JOIN chunks AS c ON c.chunk_id = e.chunk_id
            WHERE e.config_hash = ? AND e.dimensions = ? AND length(e.vector) = ?
        """
        if document_ids is not None:
            if not document_ids:
                return []
            placeholders = ",".join("?" for _ in document_ids)
            vector_sql += f" AND c.document_id IN ({placeholders})"
            params.extend(document_ids)
        if user_id is not None:
            vector_sql += " AND c.user_id = ? AND e.user_id = ?"
            params.extend((user_id, user_id))
        else:
            vector_sql += " AND c.user_id IS NULL AND e.user_id IS NULL"

        for chunk_id, vector_bytes in connection.execute(vector_sql, params):
            if not is_valid_normalized_vector_blob(vector_bytes, dimensions):
                continue
            vector = struct.unpack(f"<{dimensions}f", vector_bytes)
            similarity = math.fsum(a * b for a, b in zip(query_vector, vector, strict=True))
            key = str(chunk_id)
            heapq.heappush(heap, (similarity, key))
            if len(heap) > limit:
                heapq.heappop(heap)

        top_ids = [key for _, key in sorted(heap, key=lambda v: (-v[0], v[1]))]
        if not top_ids:
            return []

        # Phase 2: fetch full details only for the selected chunk ids.
        placeholders = ",".join("?" for _ in top_ids)
        detail_sql = detail_sql.format(placeholders=placeholders)
        if user_id is not None:
            rows = connection.execute(
                detail_sql + " AND c.user_id = ? AND d.user_id = ?",
                [*top_ids, user_id, user_id],
            ).fetchall()
        else:
            rows = connection.execute(
                detail_sql + " AND c.user_id IS NULL AND d.user_id IS NULL", top_ids
            ).fetchall()

    # Attach similarities and return in ranked order.
    similarity_by_id = {key: sim for sim, key in heap}
    results = []
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        item["similarity"] = similarity_by_id[str(item["chunk_id"])]
        results.append(item)
    results.sort(key=lambda value: (-value["similarity"], str(value["chunk_id"])))
    return results
