"""M50 data-integrity and database-reliability tests.

Covers interrupted transactions, concurrent writes, failed migrations, cascading
deletion/orphan detection, and backup-under-load. Uses temporary databases only;
never touches application data.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from app.integrity import audit_database
from app.storage import (
    SCHEMA_VERSION,
    connect_database,
    delete_document,
    initialize_storage,
    store_document,
)

NOW = "2024-01-01T00:00:00Z"


def _seed_user(connection, user_id: str = "u1") -> None:
    connection.execute(
        "INSERT INTO users (user_id, username, email, password_hash, is_admin, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, "user1", "u1@example.com", "hash", 0, NOW),
    )


def _ensure_user(db: Path, user_id: str = "u1") -> None:
    with connect_database(db) as connection:
        _seed_user(connection, user_id)
        connection.commit()


def _seed_full_graph(connection, document_id: str = "doc1", user_id: str = "u1") -> None:
    _seed_user(connection, user_id)
    chunk_a_id = f"{document_id}#chunk_a"
    chunk_b_id = f"{document_id}#chunk_b"
    connection.execute(
        "INSERT INTO documents (document_id, display_name, sha256, byte_size, page_count, "
        "chunk_count, created_at, parser_version, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (document_id, "Doc", "sha", 100, 2, 2, NOW, "1", user_id),
    )
    connection.executemany(
        "INSERT INTO chunks (chunk_id, document_id, page_number, chunk_index, char_start, "
        "char_end, text, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (chunk_a_id, document_id, 1, 0, 0, 5, "hello", user_id),
            (chunk_b_id, document_id, 1, 1, 6, 11, "world", user_id),
        ],
    )
    connection.executemany(
        "INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)",
        [(chunk_a_id, "hello"), (chunk_b_id, "world")],
    )
    connection.executemany(
        "INSERT INTO chunk_embeddings (chunk_id, model_id, model_revision, config_hash, "
        "dimensions, vector) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (chunk_a_id, "m", "r", "h", 2, b"\x00" * 8),
            (chunk_b_id, "m", "r", "h", 2, b"\x00" * 8),
        ],
    )
    connection.execute(
        "INSERT INTO content_resources (resource_id, user_id, title, description, format, "
        "detail_level, status, source_type, source_refs_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "res1",
            user_id,
            "Resource",
            None,
            "notes",
            "brief",
            "draft",
            "document",
            f'[{{"documentId":"{document_id}"}}]',
            NOW,
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO knowledge_concepts (concept_id, canonical_name, normalized_name, "
        "aliases_json, user_id) VALUES (?, ?, ?, ?, ?)",
        ("concept1", "Concept", "concept", "[]", user_id),
    )
    connection.execute(
        "INSERT INTO knowledge_concept_evidence (concept_id, chunk_id, page_number, "
        "supporting_text) VALUES (?, ?, ?, ?)",
        ("concept1", chunk_a_id, 1, "evidence"),
    )
    connection.execute(
        "INSERT INTO knowledge_relationships (relationship_id, source_concept_id, "
        "target_concept_id, relation, chunk_id, page_number, supporting_text, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("rel1", "concept1", "concept1", "rel", chunk_a_id, 1, "evidence", "candidate"),
    )


def _document_metadata(
    document_id: str = "doc1",
    user_id: str | None = "u1",
    sha256: str = "sha",
    byte_size: int = 100,
    page_count: int = 2,
    chunk_count: int = 2,
) -> dict:
    return {
        "document_id": document_id,
        "display_name": "Doc",
        "sha256": sha256,
        "byte_size": byte_size,
        "page_count": page_count,
        "chunk_count": chunk_count,
        "created_at": NOW,
        "parser_version": "1",
    }


def _chunks(document_id: str = "doc1", user_id: str = "u1") -> list[dict]:
    chunk_a_id = f"{document_id}#chunk_a"
    chunk_b_id = f"{document_id}#chunk_b"
    return [
        {
            "chunk_id": chunk_a_id,
            "document_id": document_id,
            "page_number": 1,
            "chunk_index": 0,
            "char_start": 0,
            "char_end": 5,
            "text": "hello",
            "user_id": user_id,
        },
        {
            "chunk_id": chunk_b_id,
            "document_id": document_id,
            "page_number": 1,
            "chunk_index": 1,
            "char_start": 6,
            "char_end": 11,
            "text": "world",
            "user_id": user_id,
        },
    ]


# ---------------------------------------------------------------------------
# Interrupted transaction
# ---------------------------------------------------------------------------


def test_interrupted_transaction_leaves_no_partial_rows(tmp_path: Path) -> None:
    db = tmp_path / "wl.db"
    initialize_storage(db)
    _ensure_user(db)

    import app.storage as storage_mod

    call = {"n": 0}
    original_connect = storage_mod.connect_database

    class _RaisingConn:
        """Proxy around a real connection whose first executemany() raises."""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def executemany(self, sql, params):
            call["n"] += 1
            if call["n"] == 1:  # first executemany = chunks insert inside the txn
                raise RuntimeError("simulated mid-ingest failure")
            return self._real.executemany(sql, params)

        def __enter__(self):
            self._real.__enter__()
            return self

        def __exit__(self, *args):
            return self._real.__exit__(*args)

    def fake_connect(path):
        return _RaisingConn(original_connect(path))

    storage_mod.connect_database = fake_connect
    try:
        with pytest.raises(RuntimeError, match="simulated mid-ingest failure"):
            store_document(db, _document_metadata(), _chunks(), user_id="u1")
    finally:
        storage_mod.connect_database = original_connect

    # Transaction rolled back: no document, no chunks, no FTS rows.
    with connect_database(db) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM documents WHERE document_id = 'doc1'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM chunks WHERE document_id = 'doc1'").fetchone()[
                0
            ]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE chunk_id IN ('doc1#chunk_a','doc1#chunk_b')"
            ).fetchone()[0]
            == 0
        )
        report = audit_database(db)
    assert report["ok"], report["findings"]


# ---------------------------------------------------------------------------
# Concurrent writes
# ---------------------------------------------------------------------------


def test_concurrent_writes_leave_database_consistent(tmp_path: Path) -> None:
    db = tmp_path / "wl.db"
    initialize_storage(db)
    _ensure_user(db)

    def store_one(index: int) -> str | None:
        metadata = _document_metadata(document_id=f"doc_concurrent_{index}", sha256=f"sha{index}")
        return store_document(
            db, metadata, _chunks(document_id=f"doc_concurrent_{index}"), user_id="u1"
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(store_one, i) for i in range(16)]
        for future in as_completed(futures):
            future.result()  # raises if a thread hit an unexpected error

    # No corruption, no partial documents: all 16 unique documents stored.
    with connect_database(db) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert count == 16, f"expected 16 documents, found {count}"

    report = audit_database(db)
    assert report["ok"], report["findings"]


# ---------------------------------------------------------------------------
# Failed migrations
# ---------------------------------------------------------------------------


def test_newer_schema_rejected_without_modifying_db(tmp_path: Path) -> None:
    db = tmp_path / "wl.db"
    initialize_storage(db)

    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(RuntimeError, match="newer than this application"):
        initialize_storage(db)

    # Database untouched by the rejected migration attempt.
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "wl.db"
    initialize_storage(db)
    initialize_storage(db)  # second run must be a no-op

    with connect_database(db) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    report = audit_database(db)
    assert report["ok"], report["findings"]


# ---------------------------------------------------------------------------
# Cascading deletion + orphan detection
# ---------------------------------------------------------------------------


def test_cascading_deletion_leaves_no_orphans(tmp_path: Path) -> None:
    db = tmp_path / "wl.db"
    initialize_storage(db)
    with connect_database(db) as connection:
        with connection:
            _seed_full_graph(connection)

    # Delete the document: everything owned by it must disappear cleanly.
    assert delete_document(db, "doc1", user_id="u1") is True

    report = audit_database(db)
    assert report["ok"], report["findings"]

    # Confirm all dependent rows are gone.
    with connect_database(db) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM chunks WHERE document_id = 'doc1'").fetchone()[
                0
            ]
            == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE chunk_id IN ('doc1#chunk_a','doc1#chunk_b')"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM content_resources WHERE resource_id = 'res1'"
            ).fetchone()[0]
            == 0
        )


def test_orphan_detection_catches_planted_orphans(tmp_path: Path) -> None:
    db = tmp_path / "wl.db"
    initialize_storage(db)
    with connect_database(db) as connection:
        with connection:
            _seed_full_graph(connection)

    # Plant an orphan FTS row (simulates desync between chunks and chunks_fts).
    with connect_database(db) as connection:
        connection.execute("INSERT INTO chunks_fts (chunk_id, text) VALUES ('ghost', 'x')")
        connection.commit()

    report = audit_database(db)
    assert report["ok"] is False
    finding = [f for f in report["findings"] if f["check"] == "chunks_fts_orphans"][0]
    assert finding["ok"] is False
    assert finding["detail"] == "1 orphaned FTS rows"


# ---------------------------------------------------------------------------
# Backup under concurrent load
# ---------------------------------------------------------------------------


def test_backup_during_concurrent_writes_is_consistent(tmp_path: Path) -> None:
    db = tmp_path / "wl.db"
    backup_dir = tmp_path / "backups"
    initialize_storage(db)
    _ensure_user(db)

    def store_one(index: int) -> None:
        store_document(
            db,
            _document_metadata(document_id=f"doc_backup_{index}", sha256=f"sb{index}"),
            _chunks(document_id=f"doc_backup_{index}"),
            user_id="u1",
        )

    from app.backup import create_backup, restore_backup, validate_backup

    # Seed some data, then take a backup while more writes run in parallel.
    store_document(
        db,
        _document_metadata(document_id="doc_seed", sha256="s0"),
        _chunks(document_id="doc_seed"),
        user_id="u1",
    )

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(store_one, i) for i in range(8)]
        backup_result = create_backup(db, backup_dir)
        for future in as_completed(futures):
            future.result()

    archive_path = backup_result["path"]
    # Validate the backup (runs quick_check + foreign_key_check on the snapshot).
    validation = validate_backup(archive_path)
    assert validation["valid"] is True

    # Restore to a fresh path and confirm the seeded document survived.
    restore_path = tmp_path / "restored.sqlite3"
    restore_backup(archive_path, restore_path)
    with connect_database(restore_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM documents WHERE document_id = 'doc_seed'"
            ).fetchone()[0]
            == 1
        )
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
