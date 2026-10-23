"""Backup and recovery tests using isolated synthetic SQLite databases."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import zipfile
from pathlib import Path

import pytest

from app.auth import create_session, hash_password
from app.backup import (
    BackupError,
    create_backup,
    prune_backups,
    restore_backup,
    validate_backup,
)
from app.storage import (
    SCHEMA_VERSION,
    create_learning_image_region,
    create_user,
    get_learning_image,
    get_learning_image_region,
    initialize_storage,
    list_documents,
    list_learning_images,
    store_document,
    store_learning_image,
)


def _make_database(path: Path) -> tuple[str, str, str]:
    initialize_storage(path)
    first_user = create_user(path, "backup-user-a", hash_password("test-password-a"))
    second_user = create_user(path, "backup-user-b", hash_password("test-password-b"))
    created_at = "2026-10-22T00:00:00+00:00"
    store_document(
        path,
        {
            "document_id": "document-a",
            "display_name": "private-a.pdf",
            "sha256": hashlib.sha256(b"a").hexdigest(),
            "byte_size": 1,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": created_at,
            "parser_version": "test",
        },
        [
            {
                "chunk_id": "chunk-a",
                "page_number": 1,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": 13,
                "text": "private text a",
            }
        ],
        user_id=first_user,
    )
    store_document(
        path,
        {
            "document_id": "document-b",
            "display_name": "private-b.pdf",
            "sha256": hashlib.sha256(b"b").hexdigest(),
            "byte_size": 1,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": created_at,
            "parser_version": "test",
        },
        [
            {
                "chunk_id": "chunk-b",
                "page_number": 1,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": 13,
                "text": "private text b",
            }
        ],
        user_id=second_user,
    )

    image_bytes = b"synthetic-image-a"
    store_learning_image(
        path,
        {
            "imageId": "image-a",
            "displayFilename": "private-a.png",
            "contentType": "image/png",
            "width": 1,
            "height": 1,
            "sha256": hashlib.sha256(image_bytes).hexdigest(),
            "createdAt": created_at,
            "ocrStatus": "empty",
            "extractedText": None,
        },
        image_bytes,
        user_id=first_user,
    )
    create_learning_image_region(
        path,
        {
            "imageId": "image-a",
            "regionId": "region-a",
            "geometry": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5},
            "ocrStatus": "empty",
            "extractedText": None,
            "diagramId": None,
            "conceptId": None,
            "conceptName": None,
            "createdAt": created_at,
        },
        user_id=first_user,
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO chunk_embeddings(
                   chunk_id, model_id, model_revision, config_hash, dimensions, vector, user_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "chunk-a",
                "test-model",
                "test-revision",
                "test-config",
                2,
                struct.pack("<2f", 0.25, 0.75),
                first_user,
            ),
        )
    session_token, _ = create_session(path, first_user, ttl_seconds=600)
    return first_user, second_user, session_token


def test_backup_restore_preserves_records_indexes_images_and_ownership(tmp_path):
    database = tmp_path / "source.sqlite3"
    first_user, second_user, session_token = _make_database(database)
    backup_dir = tmp_path / "private-backups"

    backup = create_backup(database, backup_dir)
    assert backup["path"].is_file()
    assert backup["database_size_bytes"] > 0
    validation = validate_backup(backup["path"])
    assert validation["valid"] is True
    assert validation["schema_version"] == SCHEMA_VERSION

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 1

    restored = tmp_path / "recovery" / "restored.sqlite3"
    assert restore_backup(backup["path"], restored, dry_run=True)["restored"] is False
    assert not restored.exists()
    assert restore_backup(backup["path"], restored)["restored"] is True

    assert [row["document_id"] for row in list_documents(restored, first_user)] == ["document-a"]
    assert [row["document_id"] for row in list_documents(restored, second_user)] == ["document-b"]
    assert [row["imageId"] for row in list_learning_images(restored, first_user)] == ["image-a"]
    assert list_learning_images(restored, second_user) == []
    assert get_learning_image(restored, "image-a", first_user)["imageBytes"] == b"synthetic-image-a"
    assert get_learning_image(restored, "image-a", second_user) is None
    assert get_learning_image_region(restored, "image-a", "region-a", first_user) is not None
    assert get_learning_image_region(restored, "image-a", "region-a", second_user) is None

    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'private'"
            ).fetchone()[0]
            == 2
        )
        assert connection.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM image_regions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 0
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    assert session_token


def test_restore_refuses_to_overwrite_existing_destination(tmp_path):
    database = tmp_path / "source.sqlite3"
    _make_database(database)
    backup = create_backup(database, tmp_path / "backups")["path"]
    destination = tmp_path / "existing.sqlite3"
    destination.write_bytes(b"keep this file")

    with pytest.raises(BackupError, match="refusing to overwrite"):
        restore_backup(backup, destination)
    assert destination.read_bytes() == b"keep this file"


def test_validate_rejects_corrupt_incomplete_and_incompatible_archives(tmp_path):
    database = tmp_path / "source.sqlite3"
    _make_database(database)
    backup = create_backup(database, tmp_path / "backups")["path"]

    truncated = tmp_path / "truncated.wlbackup"
    truncated.write_bytes(backup.read_bytes()[:40])
    with pytest.raises(BackupError):
        validate_backup(truncated)

    incompatible = tmp_path / "incompatible.wlbackup"
    with zipfile.ZipFile(backup) as source:
        manifest = json.loads(source.read("manifest.json"))
        manifest["schema_version"] = SCHEMA_VERSION + 1
        with zipfile.ZipFile(incompatible, "w", compression=zipfile.ZIP_DEFLATED) as target:
            target.writestr("database.sqlite3", source.read("database.sqlite3"))
            target.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(BackupError, match="unsupported"):
        validate_backup(incompatible)

    incomplete = tmp_path / "incomplete.wlbackup"
    with zipfile.ZipFile(incomplete, "w") as target:
        target.writestr("manifest.json", "{}")
    with pytest.raises(BackupError, match="missing or unexpected"):
        validate_backup(incomplete)


def test_retention_only_removes_generated_files_in_configured_directory(tmp_path):
    database = tmp_path / "source.sqlite3"
    _make_database(database)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backups = [create_backup(database, backup_dir)["path"] for _ in range(3)]
    unrelated = backup_dir / "keep-me.wlbackup"
    unrelated.write_text("not generated by WonderLearn", encoding="utf-8")
    other_directory = tmp_path / "outside"
    other_directory.mkdir()
    outside = create_backup(database, other_directory)["path"]

    removed = prune_backups(backup_dir, keep_last=1)

    assert len(removed) == 2
    assert sum(path.exists() for path in backups) == 1
    assert unrelated.exists()
    assert outside.exists()


def test_restore_dry_run_validates_without_creating_destination(tmp_path):
    database = tmp_path / "source.sqlite3"
    _make_database(database)
    backup = create_backup(database, tmp_path / "backups")["path"]
    destination = tmp_path / "not-created" / "restored.sqlite3"

    result = restore_backup(backup, destination, dry_run=True)

    assert result["valid"] is True
    assert not destination.exists()
    assert not destination.parent.exists()


def test_empty_database_backup_is_valid_and_restorable(tmp_path):
    database = tmp_path / "empty.sqlite3"
    initialize_storage(database)
    backup = create_backup(database, tmp_path / "backups")["path"]
    restored = tmp_path / "restored.sqlite3"

    assert validate_backup(backup)["valid"] is True
    assert restore_backup(backup, restored)["restored"] is True
    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_validate_rejects_database_checksum_mismatch(tmp_path):
    database = tmp_path / "source.sqlite3"
    _make_database(database)
    backup = create_backup(database, tmp_path / "backups")["path"]
    corrupted = tmp_path / "corrupted.wlbackup"

    with zipfile.ZipFile(backup) as source:
        database_bytes = bytearray(source.read("database.sqlite3"))
        database_bytes[-1] ^= 0x01
        with zipfile.ZipFile(corrupted, "w", compression=zipfile.ZIP_DEFLATED) as target:
            target.writestr("database.sqlite3", database_bytes)
            target.writestr("manifest.json", source.read("manifest.json"))

    with pytest.raises(BackupError, match="checksum"):
        validate_backup(corrupted)
