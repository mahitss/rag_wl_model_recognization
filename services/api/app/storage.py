"""SQLite schema, readiness checks, and transactional document persistence."""

import math
import sqlite3
import struct
from contextlib import closing
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2


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
            return True


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
            SELECT c.chunk_id, c.text, e.model_id, e.model_revision, e.config_hash
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
            or len(vector_bytes) != dimensions * 4
        ):
            continue
        vector = struct.unpack(f"<{dimensions}f", vector_bytes)
        norm = math.hypot(*vector)
        if all(math.isfinite(value) for value in vector) and math.isclose(norm, 1.0, abs_tol=1e-3):
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
    import math
    import struct

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
            if len(vector_bytes) != dimensions * 4:
                continue
            vector = struct.unpack(f"<{dimensions}f", vector_bytes)
            norm = math.hypot(*vector)
            if not all(math.isfinite(value) for value in vector) or not math.isclose(
                norm, 1.0, abs_tol=1e-3
            ):
                continue
            similarity = math.fsum(a * b for a, b in zip(query_vector, vector, strict=True))
            item = {key: row[key] for key in row.keys() if key != "vector"}
            item["similarity"] = similarity
            key = str(row["chunk_id"])
            heapq.heappush(heap, (similarity, key, item))
            if len(heap) > limit:
                heapq.heappop(heap)
    return [item for _, _, item in sorted(heap, key=lambda value: (-value[0], value[1]))]
