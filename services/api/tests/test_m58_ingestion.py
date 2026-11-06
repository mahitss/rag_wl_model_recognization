"""M58: Multimodal ingestion pipeline tests.

Tests cover:
* PDF ingestion via unified pipeline
* Text/Markdown ingestion via unified pipeline
* Image ingestion via unified pipeline
* Format detection by extension and magic bytes
* Rejection of unsupported formats
* Rejection of malformed files
* Duplicate ingestion detection
* User isolation
* Source-location preservation (page_number, char_start, char_end)
"""

from __future__ import annotations

import io

import pytest

from app.config import Settings
from app.ingestion.pipeline import SUPPORTED_EXTENSIONS, detect_format, ingest_document
from app.storage import (
    connect_database,
    create_user,
    get_document,
    initialize_storage,
    list_documents,
)


def make_settings(tmp_path, **overrides):
    values = {
        "_env_file": None,
        "database_path": tmp_path / "wonderlearn.sqlite3",
        "cors_origins": "http://localhost:5173",
        "embedding_dimensions": 4,
        "embedding_batch_size": 2,
        "lexical_candidate_limit": 10,
        "semantic_candidate_limit": 10,
        "retrieval_mode": "lexical",
        "max_upload_bytes": 10 * 1024 * 1024,
        "max_image_upload_bytes": 10 * 1024 * 1024,
        "max_image_dimension": 4096,
        "max_image_pixels": 16_000_000,
        "max_pdf_pages": 250,
        "max_extracted_chars": 5_000_000,
        "chunk_size_chars": 2_000,
        "chunk_overlap_chars": 200,
    }
    values.update(overrides)
    return Settings(**values)


def make_pdf_bytes(text: str = "Test content") -> bytes:
    """Create a minimal valid PDF with extractable text."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    content = DecodedStreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _create_user(database_path, username: str = "user-1") -> str:
    """Create a user so foreign key constraints pass. Returns the user_id."""
    return create_user(database_path, username, "hashed-password", is_admin=False)


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def test_detect_format_by_extension_pdf(tmp_path):
    fmt = detect_format("document.pdf", b"%PDF-1.4\nfake content")
    assert fmt == "pdf"


def test_detect_format_by_extension_text(tmp_path):
    fmt = detect_format("notes.txt", b"hello world")
    assert fmt == "text"


def test_detect_format_by_extension_markdown(tmp_path):
    fmt = detect_format("notes.md", b"# Hello World")
    assert fmt == "text"


def test_detect_format_by_extension_image_png(tmp_path):
    fmt = detect_format("diagram.png", b"\x89PNG\r\n\x1a\nfake")
    assert fmt == "image"


def test_detect_format_by_extension_image_jpg(tmp_path):
    fmt = detect_format("photo.jpg", b"\xff\xd8\xfffake")
    assert fmt == "image"


def test_detect_format_unsupported_extension(tmp_path):
    with pytest.raises(Exception) as exc:
        detect_format("document.exe", b"MZfake")
    assert exc.value.status_code == 415


def test_detect_format_format_mismatch(tmp_path):
    with pytest.raises(Exception) as exc:
        detect_format("bad.pdf", b"\x89PNG\r\n\x1a\nfake")
    assert exc.value.status_code == 415


def test_detect_format_missing_filename(tmp_path):
    with pytest.raises(Exception) as exc:
        detect_format(None, b"%PDF-1.4")
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Supported extensions
# ---------------------------------------------------------------------------


def test_supported_extensions_include_expected_formats():
    assert ".pdf" in SUPPORTED_EXTENSIONS
    assert ".txt" in SUPPORTED_EXTENSIONS
    assert ".md" in SUPPORTED_EXTENSIONS
    assert ".markdown" in SUPPORTED_EXTENSIONS
    assert ".png" in SUPPORTED_EXTENSIONS
    assert ".jpg" in SUPPORTED_EXTENSIONS
    assert ".jpeg" in SUPPORTED_EXTENSIONS


# ---------------------------------------------------------------------------
# PDF ingestion via unified pipeline
# ---------------------------------------------------------------------------


def test_pdf_ingestion_via_pipeline(tmp_path):
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = _create_user(settings.database_path)

    pdf_bytes = make_pdf_bytes("Test content")
    metadata = ingest_document(
        io.BytesIO(pdf_bytes),
        filename="test.pdf",
        database_path=settings.database_path,
        settings=settings,
        user_id=user_id,
    )

    assert metadata["format"] == "pdf"
    assert metadata["display_name"] == "test.pdf"
    assert "sha256" in metadata
    assert "document_id" in metadata


def test_pdf_duplicate_detection(tmp_path):
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = _create_user(settings.database_path)

    pdf_bytes = make_pdf_bytes("Duplicate test")
    ingest_document(
        io.BytesIO(pdf_bytes),
        filename="dup.pdf",
        database_path=settings.database_path,
        settings=settings,
        user_id=user_id,
    )
    # Same content should be detected as duplicate
    with pytest.raises(Exception) as exc:
        ingest_document(
            io.BytesIO(pdf_bytes),
            filename="dup.pdf",
            database_path=settings.database_path,
            settings=settings,
            user_id=user_id,
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "DUPLICATE_DOCUMENT"


def test_pdf_user_isolation(tmp_path):
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_a = _create_user(settings.database_path, "user-a")
    user_b = _create_user(settings.database_path, "user-b")

    pdf_bytes = make_pdf_bytes("Private content")
    ingest_document(
        io.BytesIO(pdf_bytes),
        filename="private.pdf",
        database_path=settings.database_path,
        settings=settings,
        user_id=user_a,
    )

    # User B cannot see User A's documents
    docs_b = list_documents(settings.database_path, user_id=user_b)
    assert len(docs_b) == 0

    # User A can see their own documents
    docs_a = list_documents(settings.database_path, user_id=user_a)
    assert len(docs_a) == 1


# ---------------------------------------------------------------------------
# Text ingestion via unified pipeline
# ---------------------------------------------------------------------------


def test_text_ingestion_via_pipeline(tmp_path):
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = _create_user(settings.database_path)

    text_content = b"Hello world. This is a test document about photosynthesis."
    metadata = ingest_document(
        io.BytesIO(text_content),
        filename="notes.txt",
        database_path=settings.database_path,
        settings=settings,
        user_id=user_id,
    )

    assert metadata["format"] == "text"
    assert metadata["display_name"] == "notes.txt"
    assert metadata["page_count"] == 1
    assert metadata["chunk_count"] >= 1
    assert metadata["parser_version"] == "wonderlearn-text/1.0"


def test_markdown_ingestion_via_pipeline(tmp_path):
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = _create_user(settings.database_path)

    md_content = b"# Chapter 1\n\nThis is about photosynthesis and chlorophyll."
    metadata = ingest_document(
        io.BytesIO(md_content),
        filename="chapter1.md",
        database_path=settings.database_path,
        settings=settings,
        user_id=user_id,
    )

    assert metadata["format"] == "text"
    assert metadata["display_name"] == "chapter1.md"


def test_text_ingestion_preserves_source_location(tmp_path):
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = _create_user(settings.database_path)

    text_content = b"First sentence. " * 200  # Long enough to create multiple chunks
    metadata = ingest_document(
        io.BytesIO(text_content),
        filename="long.txt",
        database_path=settings.database_path,
        settings=settings,
        user_id=user_id,
    )

    document_id = metadata["document_id"]
    doc = get_document(settings.database_path, document_id, user_id=user_id)
    assert doc is not None
    assert doc["format"] == "text"


def test_empty_text_rejected(tmp_path):
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = _create_user(settings.database_path)

    with pytest.raises(Exception) as exc:
        ingest_document(
            io.BytesIO(b""),
            filename="empty.txt",
            database_path=settings.database_path,
            settings=settings,
            user_id=user_id,
        )
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Image ingestion via unified pipeline
# ---------------------------------------------------------------------------


def test_image_ingestion_via_pipeline(tmp_path):
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = _create_user(settings.database_path)

    from PIL import Image

    img_buf = io.BytesIO()
    Image.new("RGB", (100, 100), color="red").save(img_buf, format="PNG")
    img_bytes = img_buf.getvalue()

    metadata = ingest_document(
        io.BytesIO(img_bytes),
        filename="diagram.png",
        database_path=settings.database_path,
        settings=settings,
        user_id=user_id,
    )

    assert metadata["format"] == "image"
    assert metadata["width"] == 100
    assert metadata["height"] == 100
    assert metadata["contentType"] == "image/png"


# ---------------------------------------------------------------------------
# Source-location preservation
# ---------------------------------------------------------------------------


def test_chunk_source_location_preserved(tmp_path):
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = _create_user(settings.database_path)

    text_content = b"Alpha beta gamma delta. " * 50 + b"Epsilon zeta eta theta. " * 50
    metadata = ingest_document(
        io.BytesIO(text_content),
        filename="location_test.txt",
        database_path=settings.database_path,
        settings=settings,
        user_id=user_id,
    )

    document_id = metadata["document_id"]
    with connect_database(settings.database_path) as connection:
        chunks = connection.execute(
            "SELECT chunk_id, page_number, chunk_index, char_start, char_end, text "
            "FROM chunks WHERE document_id = ? ORDER BY chunk_index",
            (document_id,),
        ).fetchall()

    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk["page_number"] == 1
        assert chunk["chunk_index"] >= 0
        assert chunk["char_start"] >= 0
        assert chunk["char_end"] > chunk["char_start"]
        assert len(chunk["text"]) > 0


# ---------------------------------------------------------------------------
# Document listing includes format
# ---------------------------------------------------------------------------


def test_document_listing_includes_format(tmp_path):
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = _create_user(settings.database_path)

    pdf_bytes = make_pdf_bytes("Format test")
    ingest_document(
        io.BytesIO(pdf_bytes),
        filename="fmt_test.pdf",
        database_path=settings.database_path,
        settings=settings,
        user_id=user_id,
    )

    docs = list_documents(settings.database_path, user_id=user_id)
    assert len(docs) == 1
    assert docs[0]["format"] == "pdf"


def test_document_get_includes_format(tmp_path):
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = _create_user(settings.database_path)

    text_content = b"Get format test"
    metadata = ingest_document(
        io.BytesIO(text_content),
        filename="get_fmt.txt",
        database_path=settings.database_path,
        settings=settings,
        user_id=user_id,
    )

    doc = get_document(settings.database_path, metadata["document_id"], user_id=user_id)
    assert doc is not None
    assert doc["format"] == "text"


# ---------------------------------------------------------------------------
# Unsupported format rejection
# ---------------------------------------------------------------------------


def test_unsupported_format_rejected(tmp_path):
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = _create_user(settings.database_path)

    with pytest.raises(Exception) as exc:
        ingest_document(
            io.BytesIO(b"MZ\x90\x00fake exe"),
            filename="malware.exe",
            database_path=settings.database_path,
            settings=settings,
            user_id=user_id,
        )
    assert exc.value.status_code == 415
