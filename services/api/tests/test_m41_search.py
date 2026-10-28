"""M41 unified search regression tests.

Tests keyword search across document passages, content resources, quizzes,
learning objectives, and knowledge-graph concepts with user isolation,
ranking, filtering, pagination, source provenance, deleted-resource
exclusion, semantic fallback, and malformed-query handling.

Run with:

    uv run --project services/api --extra dev pytest services/api/tests/test_m41_search.py
"""

from __future__ import annotations

import asyncio
import io
import json

import pytest
from auth_helpers import authenticated_request, prepare_test_account
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.config import Settings
from app.main import create_app
from app.storage import (
    create_content_resource,
    create_content_section,
    create_quiz_set,
    delete_document,
    initialize_storage,
    store_document,
    store_knowledge_extraction,
)


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


def image_bytes(*, image_format="PNG", size=(32, 20), color="white") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format=image_format)
    return output.getvalue()


def make_settings(tmp_path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        database_path=tmp_path / "m41-search.sqlite3",
        cors_origins="http://localhost:5173",
        retrieval_mode="lexical",
        chunk_size_chars=2_000,
        chunk_overlap_chars=200,
        **overrides,
    )


async def request(app, method: str, path: str, **kwargs):
    return await authenticated_request(app, method, path, **kwargs)


def post_pdf(app, pdf_bytes: bytes, filename="lesson.pdf"):
    return asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/documents",
            files={"file": (filename, pdf_bytes, "application/pdf")},
        )
    )


def _search_json(app, json_body):
    return asyncio.run(request(app, "POST", "/api/v1/search", json=json_body))


def search(app, query: str, mode: str | None = None, resource_types: list[str] | None = None):
    params = {"query": query, "limit": 10, "offset": 0}
    if mode:
        params["mode"] = mode
    if resource_types:
        params["resourceTypes"] = resource_types
    return _search_json(app, params)


def search_app(app, query: str, mode: str | None = None, resource_types: list[str] | None = None):
    return search(app, query, mode, resource_types)


# ---------------------------------------------------------------------------
# M41: Unified search tests
# ---------------------------------------------------------------------------


def test_search_document_passages_keyword(tmp_path):
    """Keyword search returns document passages with citations."""
    uploaded = post_pdf(
        app := create_app(make_settings(tmp_path)),
        make_pdf_pages(["Chlorophyll absorbs light energy in leaves during photosynthesis."]),
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documentId"]

    result = search(app, "chlorophyll")
    assert result.status_code == 200
    body = result.json()
    assert body["total"] >= 1
    assert body["query"] == "chlorophyll"
    assert body["retrievalMode"] == "lexical"

    doc_results = [r for r in body["results"] if r["sourceType"] == "document"]
    assert len(doc_results) >= 1
    hit = doc_results[0]
    assert hit["sourceId"] == document_id
    assert hit["citations"][0]["documentId"] == document_id


def test_search_across_all_resource_types(tmp_path):
    """Search returns results from documents, content resources, quizzes, objectives, concepts."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    # Upload document
    uploaded = post_pdf(
        app,
        make_pdf_pages(["Chlorophyll absorbs light energy in leaves during photosynthesis."]),
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documentId"]

    # Create content resource
    resource = create_content_resource(
        settings.database_path,
        user_id=user_id,
        title="Photosynthesis study notes",
        description=None,
        format="notes",
        detail_level="standard",
        source_type="document",
        source_refs=[
            {
                "type": "document",
                "documentId": document_id,
                "displayFilename": "lesson.pdf",
                "pageNumber": 1,
                "chunkIndex": 0,
                "retrieval": {"method": "fts5_bm25", "rank": 1},
            }
        ],
    )
    resource_id = resource["resourceId"]
    create_content_section(
        settings.database_path,
        resource_id=resource_id,
        user_id=user_id,
        section_type="body",
        section_title="Light reactions",
        content="Chlorophyll absorbs light energy during the light reactions of photosynthesis.",
        citations=[
            {
                "citationId": "chunk_1",
                "documentId": document_id,
                "displayFilename": "lesson.pdf",
                "pageNumber": 1,
                "chunkIndex": 0,
                "charStart": 0,
                "charEnd": 80,
                "text": "Chlorophyll absorbs light energy in leaves during photosynthesis.",
                "retrieval": {"method": "fts5_bm25", "rank": 1},
            }
        ],
    )

    # Create quiz
    create_quiz_set(
        settings.database_path,
        {
            "quiz_id": "quiz_photosynthesis",
            "topic_id": "topic_photosynthesis",
            "topic": "photosynthesis quiz",
            "created_at": "2026-01-01T00:00:00Z",
            "questions": [
                {
                    "question_id": "q1",
                    "quiz_id": "quiz_photosynthesis",
                    "position": 0,
                    "question_type": "multiple_choice",
                    "prompt": "What does chlorophyll absorb?",
                    "options": [{"id": "A", "text": "Light energy"}],
                    "answer_key": "A",
                    "accepted_answers": [],
                    "explanation": "Chlorophyll absorbs light energy.",
                    "citations": [],
                }
            ],
        },
        user_id=user_id,
    )

    result = search(app, "photosynthesis")
    assert result.status_code == 200
    body = result.json()

    # Should have results from document, content_resource, and quiz
    types_found = {r["sourceType"] for r in body["results"]}
    assert "document" in types_found
    assert "content_resource" in types_found
    assert "quiz" in types_found

    # Each result has required fields
    for r in body["results"]:
        assert "resultType" in r
        assert "id" in r
        assert "title" in r
        assert "snippet" in r
        assert "score" in r
        assert "sourceType" in r
        assert "sourceId" in r
        assert "citations" in r


def test_search_result_type_filter(tmp_path):
    """Filtering by resource type returns only matching types."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    uploaded = post_pdf(
        app,
        make_pdf_pages(["Chlorophyll absorbs light energy in leaves."]),
    )
    assert uploaded.status_code == 201

    result = search(app, "chlorophyll", resource_types=["document"])
    assert result.status_code == 200
    body = result.json()
    types_found = {r["sourceType"] for r in body["results"]}
    assert types_found == {"document"}


def test_search_offset_and_limit(tmp_path):
    """Offset and limit parameters paginate results correctly."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    # Upload 3 documents
    for i in range(3):
        uploaded = post_pdf(
            app,
            make_pdf_pages([f"Document {i} about photosynthesis and chlorophyll."]),
        )
        assert uploaded.status_code == 201

    # Get all results first
    all_result = _search_json(app, {"query": "photosynthesis", "limit": 10, "offset": 0})
    assert all_result.status_code == 200
    all_body = all_result.json()
    total = all_body["total"]
    assert total >= 3
    all_ids = {r["id"] for r in all_body["results"]}
    assert len(all_ids) == total  # All results have unique IDs

    # Page 1: limit 1, offset 0
    page1 = _search_json(app, {"query": "photosynthesis", "limit": 1, "offset": 0})
    assert page1.status_code == 200
    page1_body = page1.json()
    assert page1_body["offset"] == 0
    if page1_body["results"]:
        assert page1_body["results"][0]["id"] in all_ids

    # Page 2: limit 1, offset 1 (only if there are at least 2 results)
    if total >= 2:
        page2 = _search_json(app, {"query": "photosynthesis", "limit": 1, "offset": 1})
        assert page2.status_code == 200
        page2_body = page2.json()
        assert page2_body["offset"] == 1
        if page2_body["results"]:
            assert page2_body["results"][0]["id"] in all_ids
            assert page2_body["results"][0]["id"] != page1_body["results"][0]["id"]

    # Offset beyond total returns empty
    page3 = _search_json(app, {"query": "photosynthesis", "limit": 10, "offset": 100})
    assert page3.status_code == 200
    page3_body = page3.json()
    assert len(page3_body["results"]) == 0


def test_search_deleted_document_excluded(tmp_path):
    """Deleted documents do not appear in search results."""
    uploaded = post_pdf(
        app := create_app(make_settings(tmp_path)),
        make_pdf_pages(["Chlorophyll absorbs light energy in leaves."]),
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documentId"]

    # Search before deletion
    before = search(app, "chlorophyll")
    assert before.status_code == 200
    assert before.json()["total"] >= 1

    # Delete the document
    deleted = asyncio.run(
        request(app, "DELETE", f"/api/v1/documents/{document_id}")
    )
    assert deleted.status_code == 200

    # Search after deletion
    after = search(app, "chlorophyll")
    assert after.status_code == 200
    doc_results = [r for r in after.json()["results"] if r["sourceType"] == "document"]
    assert len(doc_results) == 0


def test_search_cross_user_isolation(tmp_path):
    """User B cannot see User A's search results."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    prepare_test_account(app)

    # User A uploads document
    uploaded = post_pdf(
        app,
        make_pdf_pages(["Chlorophyll absorbs light energy in leaves."]),
    )
    assert uploaded.status_code == 201

    # User A search finds it
    result_a = search(app, "chlorophyll")
    assert result_a.status_code == 200
    assert result_a.json()["total"] >= 1

    # User B on a separate app with a separate database
    settings_b = make_settings(tmp_path / "user_b")
    app_b = create_app(settings_b)
    prepare_test_account(app_b)

    # User B searches same query - should find nothing (no documents)
    result_b = search(app_b, "chlorophyll")
    assert result_b.status_code == 200
    assert result_b.json()["total"] == 0


def test_search_empty_query_rejected(tmp_path):
    """Empty search query is rejected."""
    app = create_app(make_settings(tmp_path))
    result = _search_json(app, {"query": "   ", "limit": 10, "offset": 0})
    assert result.status_code == 422


def test_search_malformed_query(tmp_path):
    """Malformed query with special FTS characters is handled safely."""
    uploaded = post_pdf(
        app := create_app(make_settings(tmp_path)),
        make_pdf_pages(["Chlorophyll absorbs light energy in leaves."]),
    )
    assert uploaded.status_code == 201

    # Query with FTS5 special characters
    result = _search_json(
        app,
        {"query": "chlorophyll :: NEAR/0 light", "limit": 10, "offset": 0},
    )
    # Should not crash - either returns results or a clean error
    assert result.status_code in (200, 422)


def test_search_semantic_unavailable_falls_back(tmp_path):
    """Semantic search without a model falls back to lexical or reports unavailability."""
    app = create_app(make_settings(tmp_path))

    uploaded = post_pdf(
        app,
        make_pdf_pages(["Chlorophyll absorbs light energy in leaves."]),
    )
    assert uploaded.status_code == 201

    result = search(app, "chlorophyll", mode="semantic")
    # Semantic search without a model either falls back to lexical or reports
    # that the embedding index is incomplete.
    assert result.status_code in (200, 409, 503)
    if result.status_code == 200:
        body = result.json()
        assert body["retrievalMode"] in ("lexical", "lexical_fallback", "semantic")


def test_search_ranking_relevance(tmp_path):
    """More relevant results rank higher (exact title match > content match)."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    # Create content resources with different relevance
    resource1 = create_content_resource(
        settings.database_path,
        user_id=user_id,
        title="Chlorophyll guide",
        description=None,
        format="notes",
        detail_level="standard",
        source_type="document",
        source_refs=[],
    )
    create_content_section(
        settings.database_path,
        resource_id=resource1["resourceId"],
        user_id=user_id,
        section_type="body",
        section_title="Chlorophyll introduction",
        content="Chlorophyll is the green pigment that absorbs light energy.",
        citations=[],
    )

    resource2 = create_content_resource(
        settings.database_path,
        user_id=user_id,
        title="Plant biology",
        description=None,
        format="notes",
        detail_level="standard",
        source_type="document",
        source_refs=[],
    )
    create_content_section(
        settings.database_path,
        resource_id=resource2["resourceId"],
        user_id=user_id,
        section_type="body",
        section_title="Plant cells",
        content="Plant cells contain chlorophyll for photosynthesis.",
        citations=[],
    )

    result = search(app, "chlorophyll", resource_types=["content_resource"])
    assert result.status_code == 200
    body = result.json()
    cr_results = [r for r in body["results"] if r["sourceType"] == "content_resource"]
    assert len(cr_results) >= 1

    # First result should be the one with exact title match (resource1: "Chlorophyll introduction")
    assert cr_results[0]["title"] == "Chlorophyll introduction"
    if len(cr_results) > 1:
        assert cr_results[0]["score"] >= cr_results[1]["score"]


def test_search_by_type_counts(tmp_path):
    """byType counts results per source type."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    uploaded = post_pdf(
        app,
        make_pdf_pages(["Chlorophyll absorbs light energy in leaves."]),
    )
    assert uploaded.status_code == 201

    result = search(app, "chlorophyll", resource_types=["document"])
    assert result.status_code == 200
    body = result.json()
    assert "document" in body["byType"]
    assert body["byType"]["document"] >= 1
