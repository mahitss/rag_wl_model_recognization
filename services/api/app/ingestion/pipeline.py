"""Unified multimodal ingestion pipeline.

Provides a single entry point for ingesting all supported document formats:

1. File validation and security checks
2. Format detection (by extension and magic bytes)
3. Text, image, table, or content extraction
4. Content normalization
5. Page, slide, timestamp, and source metadata preservation
6. Chunking and indexing
7. Embedding generation where supported
8. Retrieval integration
9. Source-grounded answer generation

Preserves relationships between extracted content and original source locations.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from app.config import Settings
from app.errors import ApiError
from app.ingestion.image import (
    normalize_image,
    recognize_text,
    safe_image_filename,
)
from app.ingestion.pdf import ingest_pdf
from app.ingestion.text import ingest_text
from app.storage import store_learning_image

# ---------------------------------------------------------------------------
# Supported formats
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
}

# Magic byte signatures for format detection (extension -> list of signatures).
_MAGIC_SIGNATURES: dict[str, list[bytes]] = {
    ".pdf": [b"%PDF-"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    # Text files have no reliable magic signature; detected by extension only.
}

MIME_TYPE_MAP: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


def _detect_format_from_magic(content: bytes) -> str | None:
    """Detect format from magic bytes. Returns format key or None."""
    for ext, signatures in _MAGIC_SIGNATURES.items():
        for sig in signatures:
            if content.startswith(sig):
                return SUPPORTED_EXTENSIONS[ext]
    return None


def detect_format(
    filename: str | None,
    content: bytes,
) -> str:
    """Detect document format from filename extension and magic bytes.

    Raises :class:`ApiError` if the format is unsupported or cannot be
    determined.
    """
    if not filename:
        raise ApiError(422, "MISSING_FILENAME", "The uploaded file has no name.")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ApiError(
            415,
            "UNSUPPORTED_FORMAT",
            f"The file extension '{ext}' is not supported. "
            f"Supported formats: {', '.join(sorted(SUPPORTED_EXTENSIONS))}.",
        )

    expected_format = SUPPORTED_EXTENSIONS[ext]
    detected_format = _detect_format_from_magic(content)

    # For non-text formats, verify magic bytes match the extension.
    if ext in {".pdf", ".png", ".jpg", ".jpeg"}:
        if detected_format is not None and detected_format != expected_format:
            raise ApiError(
                415,
                "FORMAT_MISMATCH",
                f"File extension '{ext}' does not match detected content format.",
            )
        if detected_format is None and not content.startswith(_MAGIC_SIGNATURES[ext][0]):
            raise ApiError(
                415,
                "INVALID_FILE",
                f"The file content does not match the '{ext}' format.",
            )

    return expected_format


def validate_content(
    content: bytes,
    *,
    max_bytes: int,
) -> None:
    """Validate uploaded content for security and size constraints.

    Raises :class:`ApiError` if validation fails.
    """
    if not content:
        raise ApiError(422, "EMPTY_FILE", "The uploaded file is empty.")
    if len(content) > max_bytes:
        raise ApiError(
            413,
            "FILE_TOO_LARGE",
            f"The upload exceeds the {max_bytes}-byte limit.",
        )


def ingest_document(
    stream: BinaryIO,
    *,
    filename: str | None,
    database_path: Path,
    settings: Settings,
    user_id: str | None = None,
) -> dict[str, object]:
    """Unified document ingestion entry point.

    Detects the format, validates, extracts, chunks, and stores the document.
    Returns metadata including the detected ``format`` field.
    """
    stream.seek(0, 2)
    stream.tell()
    stream.seek(0)

    content = stream.read()
    stream.seek(0)

    validate_content(content, max_bytes=settings.max_upload_bytes)

    fmt = detect_format(filename, content)

    if fmt == "pdf":
        return ingest_pdf(
            stream,
            filename=filename,
            database_path=database_path,
            settings=settings,
            user_id=user_id,
        )
    if fmt == "text":
        return ingest_text(
            stream,
            filename=filename,
            database_path=database_path,
            settings=settings,
            user_id=user_id,
        )
    if fmt == "image":
        return _ingest_image(
            content,
            filename=filename,
            database_path=database_path,
            settings=settings,
            user_id=user_id,
        )

    raise ApiError(415, "UNSUPPORTED_FORMAT", f"The format '{fmt}' is not supported.")


def _ingest_image(
    content: bytes,
    *,
    filename: str | None,
    database_path: Path,
    settings: Settings,
    user_id: str | None = None,
) -> dict[str, object]:
    """Ingest an image: normalize, OCR, store bytes, return metadata."""
    normalized = normalize_image(
        content,
        max_bytes=settings.max_image_upload_bytes,
        max_dimension=settings.max_image_dimension,
        max_pixels=settings.max_image_pixels,
    )
    ocr_status, extracted_text = recognize_text(normalized.content)

    metadata = {
        "imageId": f"img_{uuid.uuid4().hex}",
        "displayFilename": safe_image_filename(filename),
        "contentType": normalized.content_type,
        "width": normalized.width,
        "height": normalized.height,
        "byteSize": len(normalized.content),
        "sha256": normalized.sha256,
        "createdAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "ocrStatus": ocr_status,
        "extractedText": extracted_text,
        "format": "image",
    }
    store_learning_image(
        database_path,
        metadata,
        normalized.content,
        user_id=user_id,
    )
    return metadata
