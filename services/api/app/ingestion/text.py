"""Bounded local text and Markdown document validation, extraction, and indexing orchestration."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from app.config import Settings
from app.errors import ApiError
from app.ingestion.chunking import chunk_page_text
from app.storage import store_document


def safe_text_filename(filename: str | None) -> str:
    candidate = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    candidate = "".join(char for char in candidate if char.isprintable() and ord(char) != 127)
    candidate = candidate.strip(" .")[:200]
    return candidate or "uploaded.txt"


def _hash_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while block := stream.read(64 * 1024):
        digest.update(block)
    return digest.hexdigest()


def _parse_text(stream: BinaryIO, settings: Settings) -> tuple[int, list[dict[str, int | str]]]:
    try:
        raw = stream.read()
        extracted_char_count = len(raw)
        if extracted_char_count > settings.max_extracted_chars:
            raise ApiError(
                413,
                "EXTRACTED_TEXT_LIMIT_EXCEEDED",
                "The document contains more text than the configured limit.",
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        if not text.strip():
            raise ApiError(
                422,
                "NO_EXTRACTABLE_TEXT",
                "No extractable text was found in the document.",
            )
        all_chunks: list[dict[str, int | str]] = []
        all_chunks.extend(
            chunk_page_text(
                text,
                page_number=1,
                chunk_size=settings.chunk_size_chars,
                overlap=settings.chunk_overlap_chars,
            )
        )
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(
            422,
            "INVALID_TEXT",
            "The uploaded content could not be parsed as a text document.",
        ) from exc

    if not all_chunks:
        raise ApiError(
            422,
            "NO_EXTRACTABLE_TEXT",
            "No extractable text was found in the document.",
        )
    return 1, all_chunks


def ingest_text(
    stream: BinaryIO,
    *,
    filename: str | None,
    database_path: Path,
    settings: Settings,
    user_id: str | None = None,
) -> dict[str, object]:
    stream.seek(0, 2)
    byte_size = stream.tell()
    if byte_size == 0:
        raise ApiError(422, "EMPTY_FILE", "The uploaded file is empty.")
    if byte_size > settings.max_upload_bytes:
        raise ApiError(
            413,
            "FILE_TOO_LARGE",
            f"The upload exceeds the {settings.max_upload_bytes}-byte limit.",
        )

    stream.seek(0)
    header = stream.read(4)
    stream.seek(0)
    if (
        header.startswith(b"\xef\xbb\xbf")
        or header.startswith(b"\xff\xfe")
        or header.startswith(b"\xfe\xff")
    ):
        raise ApiError(
            415,
            "UNSUPPORTED_ENCODING",
            "The uploaded file uses a non-UTF-8 BOM encoding that is not supported.",
        )

    stream.seek(0)
    sha256 = _hash_stream(stream)
    stream.seek(0)
    page_count, drafts = _parse_text(stream, settings)

    document_id = f"doc_{uuid.uuid4().hex}"
    chunks: list[dict[str, int | str]] = []
    for draft in drafts:
        chunks.append({**draft, "chunk_id": f"chk_{uuid.uuid4().hex}"})

    metadata: dict[str, object] = {
        "document_id": document_id,
        "display_name": safe_text_filename(filename),
        "sha256": sha256,
        "byte_size": byte_size,
        "page_count": page_count,
        "chunk_count": len(chunks),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "parser_version": "wonderlearn-text/1.0",
        "format": "text",
    }
    try:
        duplicate_id = store_document(database_path, metadata, chunks, user_id=user_id)
    except OSError as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    except sqlite3.Error as exc:
        raise ApiError(503, "STORAGE_ERROR", "The document could not be saved locally.") from exc

    if duplicate_id:
        raise ApiError(
            409,
            "DUPLICATE_DOCUMENT",
            "This document has already been indexed.",
            {"existingDocumentId": duplicate_id},
        )
    return metadata
