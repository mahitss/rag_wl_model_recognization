"""Bounded local PDF validation, text extraction, hashing, and indexing orchestration."""

import hashlib
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from pypdf import PdfReader
from pypdf import __version__ as pypdf_version

from app.config import Settings
from app.errors import ApiError
from app.ingestion.chunking import chunk_page_text
from app.storage import store_document

QUERY_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "called",
    "do",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "what",
    "which",
    "with",
}


def safe_display_filename(filename: str | None) -> str:
    candidate = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    candidate = "".join(char for char in candidate if char.isprintable() and ord(char) != 127)
    candidate = candidate.strip(" .")[:200]
    return candidate or "uploaded.pdf"


def _hash_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while block := stream.read(64 * 1024):
        digest.update(block)
    return digest.hexdigest()


def _parse_pdf(stream: BinaryIO, settings: Settings) -> tuple[int, list[dict[str, int | str]]]:
    try:
        reader = PdfReader(stream, strict=False)
        if reader.is_encrypted:
            raise ApiError(422, "ENCRYPTED_PDF", "Password-protected PDFs are not supported.")

        page_count = len(reader.pages)
        if page_count == 0:
            raise ApiError(422, "EMPTY_PDF", "The PDF contains no pages.")
        if page_count > settings.max_pdf_pages:
            raise ApiError(
                413,
                "PDF_PAGE_LIMIT_EXCEEDED",
                f"The PDF exceeds the {settings.max_pdf_pages}-page limit.",
            )

        all_chunks: list[dict[str, int | str]] = []
        extracted_char_count = 0
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            extracted_char_count += len(text)
            if extracted_char_count > settings.max_extracted_chars:
                raise ApiError(
                    413,
                    "EXTRACTED_TEXT_LIMIT_EXCEEDED",
                    "The PDF contains more extractable text than the configured limit.",
                )
            all_chunks.extend(
                chunk_page_text(
                    text,
                    page_number=index,
                    chunk_size=settings.chunk_size_chars,
                    overlap=settings.chunk_overlap_chars,
                )
            )
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(
            422,
            "INVALID_PDF",
            "The uploaded content could not be parsed as a PDF.",
        ) from exc

    if not all_chunks:
        raise ApiError(
            422,
            "NO_EXTRACTABLE_TEXT",
            "No extractable text was found. Scanned or image-only PDFs are unsupported; "
            "OCR is not available.",
        )
    return page_count, all_chunks


def ingest_pdf(
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
    header = stream.read(1024)
    if b"%PDF-" not in header:
        raise ApiError(415, "UNSUPPORTED_FORMAT", "The uploaded content is not a PDF file.")

    stream.seek(0)
    sha256 = _hash_stream(stream)
    stream.seek(0)
    page_count, drafts = _parse_pdf(stream, settings)

    document_id = f"doc_{uuid.uuid4().hex}"
    chunks: list[dict[str, int | str]] = []
    for draft in drafts:
        chunks.append({**draft, "chunk_id": f"chk_{uuid.uuid4().hex}"})

    metadata: dict[str, object] = {
        "document_id": document_id,
        "display_name": safe_display_filename(filename),
        "sha256": sha256,
        "byte_size": byte_size,
        "page_count": page_count,
        "chunk_count": len(chunks),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "parser_version": f"pypdf/{pypdf_version}",
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
            "This PDF has already been indexed.",
            {"existingDocumentId": duplicate_id},
        )
    return metadata


def build_fts_query(query: str) -> str:
    terms = [
        term
        for term in re.findall(r"\w+", query, flags=re.UNICODE)
        if term.casefold() not in QUERY_STOP_WORDS
    ][:32]
    if not terms:
        raise ApiError(422, "INVALID_QUERY", "The query must contain at least one searchable term.")
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
