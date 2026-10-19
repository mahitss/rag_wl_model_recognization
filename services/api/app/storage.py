"""SQLite schema, readiness checks, and transactional document persistence."""

import math
import sqlite3
import struct
from contextlib import closing
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 6


def _database_uri(database_path: Path, *, mode: str = "rw") -> str:
    return f"{database_path.resolve().as_uri()}?mode={mode}"


def connect_database(database_path: Path) -> sqlite3.Connection:
    """Open the existing database without creating it and enable referential integrity."""
    connection = sqlite3.connect(_database_uri(database_path), uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_storage(database_path: Path) -> None:
    """Create the local database and apply the initial idempotent schema migration."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("Database schema is newer than this application")

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE,
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
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


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
) -> str | None:
    """Insert a document and all text/FTS rows atomically; return a duplicate's ID if present."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT document_id FROM documents WHERE sha256 = ?", (metadata["sha256"],)
            ).fetchone()
            if existing:
                return str(existing["document_id"])

            connection.execute(
                """
                INSERT INTO documents (
                    document_id, display_name, sha256, byte_size, page_count,
                    chunk_count, created_at, parser_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            for chunk in chunks:
                connection.execute(
                    """
                    INSERT INTO chunks (
                        chunk_id, document_id, page_number, chunk_index,
                        char_start, char_end, text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk["chunk_id"],
                        metadata["document_id"],
                        chunk["page_number"],
                        chunk["chunk_index"],
                        chunk["char_start"],
                        chunk["char_end"],
                        chunk["text"],
                    ),
                )
                connection.execute(
                    "INSERT INTO chunks_fts(chunk_id, text) VALUES (?, ?)",
                    (chunk["chunk_id"], chunk["text"]),
                )
    return None


def list_documents(database_path: Path) -> list[dict[str, Any]]:
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT document_id, display_name, sha256, byte_size, page_count,
                   chunk_count, created_at, parser_version
            FROM documents ORDER BY created_at DESC, document_id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_document(database_path: Path, document_id: str) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            """
            SELECT document_id, display_name, sha256, byte_size, page_count,
                   chunk_count, created_at, parser_version
            FROM documents WHERE document_id = ?
            """,
            (document_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_document(database_path: Path, document_id: str) -> bool:
    with closing(connect_database(database_path)) as connection:
        with connection:
            chunk_ids = connection.execute(
                "SELECT chunk_id FROM chunks WHERE document_id = ?", (document_id,)
            ).fetchall()
            document = connection.execute(
                "DELETE FROM documents WHERE document_id = ?", (document_id,)
            )
            if document.rowcount == 0:
                return False
            connection.executemany(
                "DELETE FROM chunks_fts WHERE chunk_id = ?",
                [(row["chunk_id"],) for row in chunk_ids],
            )
            connection.execute(
                "DELETE FROM knowledge_concepts WHERE NOT EXISTS ("
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


def store_learning_image(database_path: Path, metadata: dict[str, Any], image_bytes: bytes) -> None:
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                """INSERT INTO learning_images(
                       image_id, display_filename, content_type, width, height, byte_size,
                       sha256, created_at, ocr_status, extracted_text, image_bytes
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                ),
            )


def list_learning_images(database_path: Path) -> list[dict[str, Any]]:
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            """SELECT image_id, display_filename, content_type, width, height, byte_size,
                      sha256, created_at, ocr_status, NULL AS extracted_text
               FROM learning_images ORDER BY created_at DESC, image_id"""
        ).fetchall()
    return [_image_metadata(row) for row in rows]


def get_learning_image(database_path: Path, image_id: str) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            "SELECT * FROM learning_images WHERE image_id=?", (image_id,)
        ).fetchone()
    if row is None:
        return None
    return {**_image_metadata(row), "imageBytes": row["image_bytes"]}


def create_learning_image_region(database_path: Path, region: dict[str, Any]) -> None:
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                """INSERT INTO image_regions(
                       region_id, image_id, x, y, width, height, ocr_status, extracted_text,
                       diagram_id, concept_id, concept_name, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                ),
            )


def get_learning_image_region(
    database_path: Path, image_id: str, region_id: str
) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            """SELECT region_id, image_id, x, y, width, height, ocr_status, extracted_text,
                      diagram_id, concept_id, concept_name, created_at
               FROM image_regions WHERE image_id=? AND region_id=?""",
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
    database_path: Path, region_id: str, citations: list[dict[str, Any]]
) -> None:
    import json

    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute("DELETE FROM image_region_citations WHERE region_id=?", (region_id,))
            connection.executemany(
                """INSERT INTO image_region_citations(
                       region_id, citation_id, position, retrieval_json
                   ) VALUES (?, ?, ?, ?)""",
                [
                    (region_id, item["citationId"], index, json.dumps(item["retrieval"]))
                    for index, item in enumerate(citations)
                ],
            )


def get_learning_image_detail(database_path: Path, image_id: str) -> dict[str, Any] | None:
    import json

    with closing(connect_database(database_path)) as connection:
        image = connection.execute(
            """SELECT image_id, display_filename, content_type, width, height, byte_size,
                      sha256, created_at, ocr_status, extracted_text
               FROM learning_images WHERE image_id=?""",
            (image_id,),
        ).fetchone()
        if image is None:
            return None
        regions = connection.execute(
            """SELECT region_id, image_id, x, y, width, height, ocr_status, extracted_text,
                      diagram_id, concept_id, concept_name, created_at
               FROM image_regions WHERE image_id=? ORDER BY created_at, region_id""",
            (image_id,),
        ).fetchall()
        result = _image_metadata(image)
        result["regions"] = []
        for row in regions:
            citations = connection.execute(
                """SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
                          c.chunk_index, c.char_start, c.char_end, c.text,
                          irc.retrieval_json
                   FROM image_region_citations irc
                   JOIN chunks c ON c.chunk_id=irc.citation_id
                   JOIN documents d ON d.document_id=c.document_id
                   WHERE irc.region_id=? ORDER BY irc.position""",
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


def delete_learning_image(database_path: Path, image_id: str) -> bool:
    with closing(connect_database(database_path)) as connection:
        with connection:
            result = connection.execute("DELETE FROM learning_images WHERE image_id=?", (image_id,))
    return result.rowcount > 0


def knowledge_graph_chunks(
    database_path: Path,
    *,
    document_ids: list[str] | None,
    limit: int,
    after_chunk_id: str | None = None,
) -> list[dict[str, Any]]:
    sql = """SELECT c.chunk_id, c.document_id, d.display_name, c.page_number, c.text
             FROM chunks c JOIN documents d ON d.document_id=c.document_id"""
    parameters: list[Any] = []
    if after_chunk_id is not None:
        sql += " WHERE c.chunk_id > ?"
        parameters.append(after_chunk_id)
    if document_ids is not None:
        if not document_ids:
            return []
        sql += " AND " if after_chunk_id is not None else " WHERE "
        sql += "c.document_id IN (" + ",".join("?" for _ in document_ids) + ")"
        parameters.extend(document_ids)
    sql += " ORDER BY c.chunk_id LIMIT ?"
    parameters.append(limit)
    with closing(connect_database(database_path)) as connection:
        return [dict(row) for row in connection.execute(sql, parameters)]


def store_knowledge_extraction(
    database_path: Path, chunk: dict[str, Any], extraction: dict[str, Any]
) -> None:
    """Atomically store concepts and evidence-backed candidate relations for one chunk."""
    import json

    with closing(connect_database(database_path)) as connection:
        with connection:
            resolved: dict[str, str] = {}
            for concept in extraction["concepts"]:
                row = connection.execute(
                    "SELECT concept_id, aliases_json FROM knowledge_concepts "
                    "WHERE normalized_name=?",
                    (concept["normalized_name"],),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO knowledge_concepts("
                        "concept_id, canonical_name, normalized_name, aliases_json) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            concept["concept_id"],
                            concept["name"],
                            concept["normalized_name"],
                            json.dumps(concept["aliases"]),
                        ),
                    )
                    concept_id = concept["concept_id"]
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
                    "concept_id, chunk_id, page_number, supporting_text) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        concept_id,
                        chunk["chunk_id"],
                        chunk["page_number"],
                        concept["supporting_text"],
                    ),
                )
            for relation in extraction["relationships"]:
                source_id = resolved[relation["source_normalized"]]
                target_id = resolved[relation["target_normalized"]]
                relationship_id = relation["relationship_id"]
                connection.execute(
                    """INSERT OR IGNORE INTO knowledge_relationships(
                       relationship_id, source_concept_id, target_concept_id, relation,
                       chunk_id, page_number, supporting_text, status
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate')""",
                    (
                        relationship_id,
                        source_id,
                        target_id,
                        relation["relation"],
                        chunk["chunk_id"],
                        chunk["page_number"],
                        relation["supporting_text"],
                    ),
                )


def list_knowledge_concepts(database_path: Path) -> list[dict[str, Any]]:
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            """SELECT c.concept_id, c.canonical_name, c.aliases_json,
                      count(DISTINCT e.chunk_id) AS evidence_count,
                      count(DISTINCT r.relationship_id) AS relationship_count
               FROM knowledge_concepts c
               LEFT JOIN knowledge_concept_evidence e ON e.concept_id=c.concept_id
               LEFT JOIN knowledge_relationships r ON r.source_concept_id=c.concept_id
                    OR r.target_concept_id=c.concept_id
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


def get_knowledge_concept(database_path: Path, concept_id: str) -> dict[str, Any] | None:
    import json

    with closing(connect_database(database_path)) as connection:
        concept = connection.execute(
            "SELECT * FROM knowledge_concepts WHERE concept_id=?", (concept_id,)
        ).fetchone()
        if concept is None:
            return None
        evidence = connection.execute(
            """SELECT e.chunk_id, c.document_id, d.display_name, e.page_number,
                      e.supporting_text
               FROM knowledge_concept_evidence e JOIN chunks c ON c.chunk_id=e.chunk_id
               JOIN documents d ON d.document_id=c.document_id
               WHERE e.concept_id=? ORDER BY c.document_id, e.page_number, e.chunk_id""",
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


def verify_knowledge_relationship(database_path: Path, relationship_id: str) -> bool:
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                "UPDATE knowledge_relationships SET status='human_verified' "
                "WHERE relationship_id=? AND status='candidate'",
                (relationship_id,),
            )
            row = connection.execute(
                "SELECT status FROM knowledge_relationships WHERE relationship_id=?",
                (relationship_id,),
            ).fetchone()
    return row is not None and row["status"] == "human_verified"


def search_chunks(
    database_path: Path,
    fts_query: str,
    document_ids: list[str] | None,
    limit: int,
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
    sql += " ORDER BY rank ASC, c.document_id ASC, c.page_number ASC, c.chunk_index ASC LIMIT ?"
    parameters.append(limit)

    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(sql, parameters).fetchall()
    return [dict(row) for row in rows]


def create_quiz_set(database_path: Path, quiz: dict[str, Any]) -> None:
    """Persist generated questions and foreign-key citations atomically."""
    import json

    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                "INSERT INTO quiz_sets(quiz_id, topic_id, topic, created_at) VALUES (?, ?, ?, ?)",
                (quiz["quiz_id"], quiz["topic_id"], quiz["topic"], quiz["created_at"]),
            )
            for position, question in enumerate(quiz["questions"]):
                connection.execute(
                    """
                    INSERT INTO quiz_questions(
                        question_id, quiz_id, position, question_type, prompt,
                        options_json, answer_key, accepted_answers_json, explanation
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                for citation_position, citation in enumerate(question["citations"]):
                    connection.execute(
                        """
                        INSERT INTO quiz_citations(
                            question_id, citation_id, position, retrieval_json
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            question["question_id"],
                            citation["citationId"],
                            citation_position,
                            json.dumps(citation["retrieval"]),
                        ),
                    )


def get_quiz_set(
    database_path: Path, quiz_id: str, *, review_incorrect: bool = False
) -> dict[str, Any] | None:
    """Load a quiz with current source provenance and latest attempt outcomes."""
    import json

    with closing(connect_database(database_path)) as connection:
        quiz = connection.execute(
            "SELECT quiz_id, topic_id, topic, created_at FROM quiz_sets WHERE quiz_id = ?",
            (quiz_id,),
        ).fetchone()
        if quiz is None:
            return None
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
            WHERE q.quiz_id=?
            ORDER BY q.position
            """,
            (quiz_id,),
        ).fetchall()
        questions = []
        for row in rows:
            if review_incorrect and row["latest_outcome"] not in {"incorrect", "ambiguous"}:
                continue
            citation_rows = connection.execute(
                """
                SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
                       c.chunk_index, c.char_start, c.char_end, c.text, qc.retrieval_json
                FROM quiz_citations qc
                JOIN chunks c ON c.chunk_id=qc.citation_id
                JOIN documents d ON d.document_id=c.document_id
                WHERE qc.question_id=? ORDER BY qc.position
                """,
                (row["question_id"],),
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


def get_quiz_question(database_path: Path, question_id: str) -> dict[str, Any] | None:
    import json

    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            "SELECT * FROM quiz_questions WHERE question_id=?", (question_id,)
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
) -> None:
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO quiz_attempts(attempt_id, question_id, outcome, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (attempt_id, question_id, outcome, created_at),
            )
            question = connection.execute(
                """SELECT s.topic_id, s.topic FROM quiz_questions q
                   JOIN quiz_sets s ON s.quiz_id=q.quiz_id WHERE q.question_id=?""",
                (question_id,),
            ).fetchone()
            if question is not None:
                from datetime import UTC, datetime, timedelta

                interval_days = {"incorrect": 1, "ambiguous": 1, "correct": 7}[outcome]
                attempted_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if attempted_at.tzinfo is None:
                    attempted_at = attempted_at.replace(tzinfo=UTC)
                due_at = (attempted_at + timedelta(days=interval_days)).astimezone(UTC).isoformat()
                connection.execute(
                    """INSERT INTO topic_review_items(
                           question_id, topic_id, topic, last_attempt_id, last_outcome,
                           attempted_at, due_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )


def start_study_session(
    database_path: Path, *, session_id: str, topic_id: str, topic: str, started_at: str
) -> dict[str, Any]:
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                "INSERT INTO study_sessions("
                "session_id, topic_id, topic, started_at) VALUES (?, ?, ?, ?)",
                (session_id, topic_id, topic, started_at),
            )
    return {
        "sessionId": session_id,
        "topicId": topic_id,
        "topic": topic,
        "startedAt": started_at,
        "completedAt": None,
    }


def complete_study_session(
    database_path: Path, *, session_id: str, completed_at: str
) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                "UPDATE study_sessions SET completed_at=? "
                "WHERE session_id=? AND completed_at IS NULL",
                (completed_at, session_id),
            )
            row = connection.execute(
                "SELECT session_id, topic_id, topic, started_at, completed_at "
                "FROM study_sessions WHERE session_id=?",
                (session_id,),
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


def get_learning_analytics_data(database_path: Path) -> dict[str, Any]:
    import json

    with closing(connect_database(database_path)) as connection:
        attempts = [
            dict(row)
            for row in connection.execute(
                """SELECT a.rowid AS sequence, a.attempt_id AS attemptId,
                          a.question_id AS questionId,
                          a.outcome, a.created_at AS createdAt, q.prompt,
                          s.topic_id AS topicId, s.topic
                   FROM quiz_attempts a JOIN quiz_questions q ON q.question_id=a.question_id
                   JOIN quiz_sets s ON s.quiz_id=q.quiz_id
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
                "SELECT * FROM study_sessions ORDER BY started_at DESC, rowid DESC"
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
                "SELECT * FROM topic_review_items ORDER BY due_at, question_id"
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
                "SELECT concept_id, canonical_name, aliases_json FROM knowledge_concepts"
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
    return {
        "attempts": attempts,
        "sessions": sessions,
        "reviews": reviews,
        "topicSources": topic_sources,
        "concepts": concepts,
    }


def get_learning_progress(database_path: Path) -> dict[str, Any]:
    """Return local topic mastery based on each question's latest attempt and recent history."""
    with closing(connect_database(database_path)) as connection:
        topic_rows = connection.execute(
            """
            WITH latest AS (
                SELECT a.question_id, a.outcome,
                       row_number() OVER (
                           PARTITION BY a.question_id
                           ORDER BY a.created_at DESC, a.rowid DESC
                       ) AS rn
                FROM quiz_attempts a
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
        attempt_total = int(connection.execute("SELECT count(*) FROM quiz_attempts").fetchone()[0])
        recent_rows = connection.execute(
            """
            SELECT a.attempt_id, a.outcome, a.created_at, q.prompt, s.topic_id, s.topic
            FROM quiz_attempts a
            JOIN quiz_questions q ON q.question_id=a.question_id
            JOIN quiz_sets s ON s.quiz_id=q.quiz_id
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
) -> list[dict[str, Any]]:
    """Read a bounded chunk batch for incremental embedding indexing."""
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT c.chunk_id, c.text, e.model_id, e.model_revision, e.config_hash,
                   e.dimensions, e.vector
            FROM chunks AS c
            LEFT JOIN chunk_embeddings AS e ON e.chunk_id = c.chunk_id
            WHERE (? IS NULL OR c.chunk_id > ?)
            ORDER BY c.chunk_id ASC
            LIMIT ?
            """,
            (after_chunk_id, after_chunk_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def store_chunk_embeddings(
    database_path: Path,
    entries: list[dict[str, Any]],
) -> None:
    """Atomically upsert a batch of embeddings without touching source chunks or FTS."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.executemany(
                """
                INSERT INTO chunk_embeddings(
                    chunk_id, model_id, model_revision, config_hash, dimensions, vector
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    model_id=excluded.model_id,
                    model_revision=excluded.model_revision,
                    config_hash=excluded.config_hash,
                    dimensions=excluded.dimensions,
                    vector=excluded.vector
                """,
                [
                    (
                        entry["chunk_id"],
                        entry["model_id"],
                        entry["model_revision"],
                        entry["config_hash"],
                        entry["dimensions"],
                        entry["vector"],
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
) -> dict[str, int]:
    """Return total, current, stale, and missing vector counts for one config."""
    with closing(connect_database(database_path)) as connection:
        total = int(connection.execute("SELECT count(*) FROM chunks").fetchone()[0])
        vectors = connection.execute(
            "SELECT config_hash, dimensions, vector FROM chunk_embeddings"
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
) -> list[dict[str, Any]]:
    """Exact cosine search over normalized vectors, bounded in memory by the result limit."""
    import heapq

    sql = """
        SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
               c.chunk_index, c.char_start, c.char_end, c.text, e.vector
        FROM chunk_embeddings AS e
        JOIN chunks AS c ON c.chunk_id = e.chunk_id
        JOIN documents AS d ON d.document_id = c.document_id
        WHERE e.config_hash = ? AND e.dimensions = ? AND length(e.vector) = ?
    """
    parameters: list[Any] = [config_hash, dimensions, dimensions * 4]
    if document_ids is not None:
        if not document_ids:
            return []
        placeholders = ",".join("?" for _ in document_ids)
        sql += f" AND c.document_id IN ({placeholders})"
        parameters.extend(document_ids)

    heap: list[tuple[float, str, dict[str, Any]]] = []
    with closing(connect_database(database_path)) as connection:
        for row in connection.execute(sql, parameters):
            vector_bytes = row["vector"]
            if not is_valid_normalized_vector_blob(vector_bytes, dimensions):
                continue
            vector = struct.unpack(f"<{dimensions}f", vector_bytes)
            similarity = math.fsum(a * b for a, b in zip(query_vector, vector, strict=True))
            item = {key: row[key] for key in row.keys() if key != "vector"}
            item["similarity"] = similarity
            key = str(row["chunk_id"])
            heapq.heappush(heap, (similarity, key, item))
            if len(heap) > limit:
                heapq.heappop(heap)
    return [item for _, _, item in sorted(heap, key=lambda value: (-value[0], value[1]))]
