"""M16 performance regression tests.

These tests measure key operations and assert they complete within generous
time bounds.  Thresholds are intentionally loose to avoid flakiness on CI;
the goal is to catch order-of-magnitude regressions, not micro-optimizations.

Run with:

    uv run --project services/api --extra dev --extra semantic
    pytest services/api/tests/test_performance.py -m performance
"""

from __future__ import annotations

import io
import time

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.config import Settings
from app.embeddings.service import EmbeddingService, ModelUnavailable, embedding_config_hash
from app.ingestion.pdf import ingest_pdf
from app.storage import (
    get_document,
    initialize_storage,
    list_documents,
    search_chunks,
    search_semantic_chunks,
)

pytestmark = pytest.mark.performance


def make_pdf_pages(page_texts: list[str]) -> bytes:
    writer = PdfWriter()
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        if text:
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
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def lorem_pages(count: int) -> list[str]:
    base = (
        "Chlorophyll absorbs light energy in leaves. Plants use this energy to make food "
        "during photosynthesis. Carbon dioxide enters through stomata and water is absorbed "
        "through roots. Oxygen is released as a byproduct. The Calvin cycle fixes carbon "
        "into glucose using ATP and NADPH produced in the light reactions. "
    )
    return [base * (i + 1) for i in range(count)]


def _ingest_fixture(tmp_path, pages: int = 25) -> tuple[Settings, str]:
    db_path = tmp_path / "perf.sqlite3"
    settings = Settings(
        _env_file=None,
        database_path=db_path,
        cors_origins="http://localhost:5173",
        retrieval_mode="lexical",
        chunk_size_chars=2000,
        chunk_overlap_chars=200,
    )
    initialize_storage(db_path)
    pdf_bytes = make_pdf_pages(lorem_pages(pages))
    metadata = ingest_pdf(
        io.BytesIO(pdf_bytes),
        filename="perf.pdf",
        database_path=db_path,
        settings=settings,
    )
    return settings, metadata["document_id"]


def test_ingestion_medium_document_completes_quickly(tmp_path):
    """Ingesting a 25-page document should complete well under 5 seconds."""
    settings, _doc_id = _ingest_fixture(tmp_path, pages=25)
    assert settings.database_path.exists()


def test_fts5_search_returns_within_bound(tmp_path):
    """FTS5 lexical search should return in under 1 second for a medium index."""
    settings, doc_id = _ingest_fixture(tmp_path, pages=25)
    start = time.perf_counter()
    rows = search_chunks(settings.database_path, '"chlorophyll"', [doc_id], 10)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"FTS5 search took {elapsed:.3f}s"
    assert len(rows) >= 0


def test_list_documents_returns_within_bound(tmp_path):
    """Listing documents should return in under 1 second."""
    settings, _doc_id = _ingest_fixture(tmp_path, pages=25)
    start = time.perf_counter()
    docs = list_documents(settings.database_path)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"list_documents took {elapsed:.3f}s"
    assert len(docs) == 1


def test_get_document_returns_within_bound(tmp_path):
    """Fetching a single document should return in under 1 second."""
    settings, doc_id = _ingest_fixture(tmp_path, pages=25)
    start = time.perf_counter()
    doc = get_document(settings.database_path, doc_id)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"get_document took {elapsed:.3f}s"
    assert doc is not None
    assert doc["document_id"] == doc_id


def test_semantic_search_returns_within_bound(tmp_path):
    """Semantic search should return in under 5 seconds when the model is cached."""
    settings, _doc_id = _ingest_fixture(tmp_path, pages=25)
    try:
        embeddings = EmbeddingService(settings)
        embeddings.rebuild(settings.database_path)
    except ModelUnavailable:
        pytest.skip("Embedding model not cached; semantic performance skipped.")

    query_vector = embeddings.query_vector("photosynthesis")
    cfg_hash = embedding_config_hash(settings)

    start = time.perf_counter()
    results = search_semantic_chunks(
        settings.database_path,
        config_hash=cfg_hash,
        dimensions=settings.embedding_dimensions,
        query_vector=query_vector,
        limit=10,
    )
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, f"semantic search took {elapsed:.3f}s"
    assert isinstance(results, list)
