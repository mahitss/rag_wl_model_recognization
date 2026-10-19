"""M20 end-to-end user acceptance tests.

These tests validate complete user journeys through the WonderLearn API using
a real FastAPI application with a temporary SQLite database.  No external
services, model downloads, or network calls are required -- the optional tutor
and embedding model are mocked locally.

Each test exercises multiple API endpoints in sequence, mirroring a real
learner's flow, and verifies that citations, evidence provenance, deletion
cascades, and error handling remain consistent throughout.

Run with:

    uv run --project services/api --extra dev pytest services/api/tests/test_e2e_journey.py
"""

from __future__ import annotations

import asyncio
import io
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.config import Settings
from app.errors import ApiError
from app.knowledge_graph import parse_extraction, stable_concept_id
from app.main import create_app
from app.storage import (
    delete_document,
    initialize_storage,
    store_document,
)

TOP_K = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        database_path=tmp_path / "e2e.sqlite3",
        cors_origins="http://localhost:5173",
        retrieval_mode="lexical",
        chunk_size_chars=2_000,
        chunk_overlap_chars=200,
        **overrides,
    )


async def request(app, method: str, path: str, **kwargs):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.request(method, path, **kwargs)


def post_pdf(app, pdf_bytes: bytes, filename="lesson.pdf"):
    return asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/documents",
            files={"file": (filename, pdf_bytes, "application/pdf")},
        )
    )


def upload_image(app, data: bytes, *, filename="image.png", media_type="image/png"):
    return asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/images",
            files={"file": (filename, data, media_type)},
        )
    )


def seed_document(database_path):
    """Persist a small document directly into storage for image-region tests."""
    initialize_storage(database_path)
    store_document(
        database_path,
        {
            "document_id": "doc_e2e_source",
            "display_name": "water-cycle.pdf",
            "sha256": "b" * 64,
            "byte_size": 80,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "parser_version": "fixture",
        },
        [
            {
                "chunk_id": "chunk_e2e_source",
                "page_number": 3,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": 70,
                "text": "Water evaporates from oceans during the water cycle.",
            }
        ],
    )


def mock_tutor(monkeypatch, answer: str = "Chlorophyll absorbs light energy [E1]."):
    """Mock the loopback tutor provider to return a grounded answer."""

    class FakeResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def fake_urlopen(request, timeout):
        provider_result = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "answer": answer,
                                "citations": ["E1"],
                                "abstain": False,
                            }
                        )
                    }
                }
            ]
        }
        return FakeResponse(json.dumps(provider_result).encode())

    class FakeOpener:
        def open(self, request, timeout):
            return fake_urlopen(request, timeout)

    monkeypatch.setattr("app.tutor.build_opener", lambda *_: FakeOpener())


def mock_quiz_generator(monkeypatch):
    """Mock the loopback tutor provider to return quiz questions."""

    class FakeResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def fake_urlopen(request, timeout):
        generated = {
            "questions": [
                {
                    "type": "multiple_choice",
                    "question": "What does chlorophyll absorb?",
                    "options": [
                        {"id": "A", "text": "Light energy"},
                        {"id": "B", "text": "Sound energy"},
                        {"id": "C", "text": "Soil"},
                        {"id": "D", "text": "Oxygen"},
                    ],
                    "correctOption": "A",
                    "expectedAnswer": "",
                    "acceptedAnswers": [],
                    "explanation": "The passage says chlorophyll absorbs light energy.",
                    "citations": ["E1"],
                }
            ]
        }
        provider_result = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(generated)
                    }
                }
            ]
        }
        return FakeResponse(json.dumps(provider_result).encode())

    class FakeOpener:
        def open(self, request, timeout):
            return fake_urlopen(request, timeout)

    monkeypatch.setattr("app.tutor.build_opener", lambda *_: FakeOpener())


def mock_grounded_answer(monkeypatch, answer: str = "Grounded [E1]."):
    """Mock generate_grounded_answer to return citation objects (not labels)."""

    def fake_generate(*args, **kwargs):
        return {
            "answer": answer,
            "citations": [
                {
                    "citationId": "chunk_e2e_kg",
                    "documentId": "doc_e2e_kg",
                    "displayFilename": "lesson.pdf",
                    "pageNumber": 1,
                    "chunkIndex": 0,
                    "charStart": 0,
                    "charEnd": 10,
                    "text": "source passage",
                    "retrieval": {"method": "fts5_bm25", "rank": 1},
                }
            ],
        }

    monkeypatch.setattr("app.api.v1.routes.generate_grounded_answer", fake_generate)


# ---------------------------------------------------------------------------
# Journey 1: application readiness
# ---------------------------------------------------------------------------


def test_application_health_and_readiness(tmp_path):
    """A freshly started application reports healthy and ready."""
    settings = make_settings(tmp_path)
    app = create_app(settings)

    health = asyncio.run(request(app, "GET", "/api/v1/health"))
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["service"] == "wonderlearn-api"

    ready = asyncio.run(request(app, "GET", "/api/v1/ready"))
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert all(ready.json()["checks"].values())


# ---------------------------------------------------------------------------
# Journey 2: upload document → retrieve → tutor → unanswerable abstention
# ---------------------------------------------------------------------------


def test_document_retrieval_tutor_and_abstention_journey(tmp_path, monkeypatch):
    """Upload a PDF, retrieve evidence, ask the tutor, then verify abstention."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    app = create_app(settings)
    mock_tutor(monkeypatch)
    uploaded = post_pdf(
        app,
        make_pdf_pages(["Chlorophyll absorbs light energy in leaves."]),
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documentId"]

    # Retrieve returns the indexed chunk with provenance.
    retrieved = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/retrieve",
            json={"query": "chlorophyll light", "limit": TOP_K},
        )
    )
    assert retrieved.status_code == 200
    citation = retrieved.json()["results"][0]
    assert citation["documentId"] == document_id
    assert citation["pageNumber"] == 1
    assert citation["text"] == "Chlorophyll absorbs light energy in leaves."

    # Tutor returns an answer grounded in the retrieved citation.
    answered = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/tutor/answers",
            json={"question": "What does chlorophyll absorb?", "documentIds": [document_id]},
        )
    )
    assert answered.status_code == 200
    assert answered.json()["status"] == "answered"
    assert answered.json()["citations"][0]["documentId"] == document_id

    # Unanswerable query abstains honestly.
    abstained = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/answers",
            json={"question": "What is the boiling point of nitrogen?", "limit": TOP_K},
        )
    )
    assert abstained.status_code == 200
    assert abstained.json()["status"] == "insufficient_evidence"
    assert abstained.json()["answer"] is None
    assert abstained.json()["citations"] == []


# ---------------------------------------------------------------------------
# Journey 3: diagram concept evidence provenance
# ---------------------------------------------------------------------------


def test_diagram_concept_evidence_provenance(tmp_path):
    """A curated diagram region returns evidence from the indexed document."""
    uploaded = post_pdf(
        app:=create_app(make_settings(tmp_path)),
        make_pdf_pages(["Chlorophyll absorbs light energy in leaves."]),
    )
    document_id = uploaded.json()["documentId"]

    evidence = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/diagrams/photosynthesis-cycle/regions/chlorophyll/evidence",
            json={"limit": TOP_K},
        )
    )
    assert evidence.status_code == 200
    assert evidence.json()["results"][0]["documentId"] == document_id
    assert evidence.json()["results"][0]["pageNumber"] == 1


# ---------------------------------------------------------------------------
# Journey 4: upload image → create region → region tutor → citations
# ---------------------------------------------------------------------------


def test_image_region_tutor_journey(tmp_path, monkeypatch):
    """Upload an image, create a region, and verify grounded region tutor citations."""
    seed_document(tmp_path / "e2e.sqlite3")
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    app = create_app(settings)

    # Monkeypatch OCR and grounded answer for deterministic region flow.
    monkeypatch.setattr(
        "app.api.v1.routes.recognize_text", lambda _content: ("available", "water evaporates")
    )
    monkeypatch.setattr(
        "app.api.v1.routes.recognize_region",
        lambda _content, _geometry: ("available", "evaporates water cycle"),
    )

    # Upload image.
    created = upload_image(app, image_bytes(), filename="C:\\fake\\water.png")
    assert created.status_code == 201
    image_id = created.json()["imageId"]
    assert created.json()["displayFilename"] == "water.png"

    # Image content is retrievable with cache-control.
    content = asyncio.run(request(app, "GET", f"/api/v1/images/{image_id}/content"))
    assert content.status_code == 200
    assert content.headers["cache-control"] == "private, no-store"

    # Create a region tied to a curated concept.
    region = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/images/{image_id}/regions",
            json={
                "geometry": {"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.5},
                "diagramId": "photosynthesis-cycle",
                "conceptId": "biology.photosynthesis.chlorophyll",
            },
        )
    )
    assert region.status_code == 201
    region_id = region.json()["regionId"]

    # Region evidence is sourced from the seeded document.
    evidence = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/images/{image_id}/regions/{region_id}/evidence",
            json={"limit": TOP_K},
        )
    )
    assert evidence.status_code == 200
    assert evidence.json()["results"][0]["citationId"] == "chunk_e2e_source"

    # Region tutor returns a grounded answer citing the evidence.
    mock_tutor(monkeypatch, "Water evaporates from oceans [E1].")
    tutor = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/images/{image_id}/regions/{region_id}/tutor",
            json={"question": "What happens here?"},
        )
    )
    assert tutor.status_code == 200
    assert tutor.json()["status"] == "answered"
    assert tutor.json()["citations"][0]["citationId"] == "chunk_e2e_source"

    # Deleting the source document clears region citations (deletion consistency).
    assert delete_document(settings.database_path, "doc_e2e_source")
    after_delete = asyncio.run(request(app, "GET", f"/api/v1/images/{image_id}"))
    assert after_delete.json()["regions"][0]["citations"] == []

    # Image itself can be deleted.
    deleted = asyncio.run(request(app, "DELETE", f"/api/v1/images/{image_id}"))
    assert deleted.status_code == 200


# ---------------------------------------------------------------------------
# Journey 5: image upload validation
# ---------------------------------------------------------------------------


def test_image_upload_validation(tmp_path):
    """Malformed, oversized, and invalid images are rejected; valid images accepted."""
    settings = make_settings(tmp_path)
    app = create_app(settings)

    created = upload_image(app, image_bytes())
    assert created.status_code == 201

    # Malformed content rejected.
    malformed = upload_image(app, b"\x89PNG\r\nnot an image", filename="spoof.png")
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "INVALID_IMAGE"

    # Oversized image rejected.
    too_big_app = create_app(make_settings(tmp_path / "limited", max_image_upload_bytes=10))
    too_big = upload_image(too_big_app, image_bytes())
    assert too_big.status_code == 413

    # Dimension limit enforced.
    dim_app = create_app(make_settings(tmp_path / "small-dims", max_image_dimension=16))
    too_wide = upload_image(dim_app, image_bytes(size=(40, 30)))
    assert too_wide.status_code == 413


# ---------------------------------------------------------------------------
# Journey 6: knowledge graph extract → verify → tutor
# ---------------------------------------------------------------------------


def test_knowledge_graph_extract_verify_tutor_journey(tmp_path, monkeypatch):
    """Upload a document, extract graph concepts, verify a relationship, then tutor."""
    SOURCE = "Photosynthesis uses chlorophyll to absorb light energy."
    uploaded = post_pdf(
        app:=create_app(make_settings(tmp_path)),
        make_pdf_pages([SOURCE]),
    )
    document_id = uploaded.json()["documentId"]
    settings = make_settings(tmp_path, tutor_base_url="http://localhost:8080/v1")
    app = create_app(settings)

    EXTRACTION = {
        "concepts": [
            {
                "name": "Photosynthesis",
                "aliases": ["plant energy process"],
                "supportingText": "Photosynthesis uses chlorophyll",
            },
            {
                "name": "Chlorophyll",
                "aliases": ["green pigment"],
                "supportingText": "chlorophyll to absorb light energy",
            },
        ],
        "relationships": [
            {
                "source": "Photosynthesis",
                "target": "Chlorophyll",
                "relation": "uses",
                "supportingText": "Photosynthesis uses chlorophyll",
            }
        ],
    }

    monkeypatch.setattr(
        "app.api.v1.routes.extract_chunk",
        lambda _settings, chunk: parse_extraction(json.dumps(EXTRACTION), chunk),
    )

    # Extract graph candidates.
    extracted = asyncio.run(
        request(app, "POST", "/api/v1/knowledge-graph/extract", json={"chunkLimit": 5})
    )
    assert extracted.status_code == 200
    assert extracted.json()["chunksProcessed"] == 1

    # List concepts.
    concepts = asyncio.run(request(app, "GET", "/api/v1/knowledge-graph"))
    assert concepts.status_code == 200
    assert len(concepts.json()["concepts"]) == 2

    # Concept detail shows relationship.
    concept_id = stable_concept_id("Photosynthesis")
    detail = asyncio.run(
        request(app, "GET", f"/api/v1/knowledge-graph/concepts/{concept_id}")
    )
    assert detail.status_code == 200
    relationship = detail.json()["relationships"][0]
    assert relationship["relation"] == "uses"
    assert relationship["status"] == "candidate"
    assert relationship["supportingText"] == "Photosynthesis uses chlorophyll"

    # Verify the relationship.
    relation_id = relationship["relationshipId"]
    verified = asyncio.run(
        request(app, "POST", f"/api/v1/knowledge-graph/relationships/{relation_id}/verify")
    )
    assert verified.status_code == 200
    assert verified.json()["status"] == "human_verified"

    # Tutor can answer using the graph concept.
    monkeypatch.setattr(
        "app.api.v1.routes.retrieve_with_mode",
        lambda *_args, **kwargs: {
            "results": [
                {
                    "citationId": "chunk_e2e_kg",
                    "documentId": document_id,
                    "displayFilename": "lesson.pdf",
                    "pageNumber": 1,
                    "chunkIndex": 0,
                    "charStart": 0,
                    "charEnd": len(SOURCE),
                    "text": SOURCE,
                    "retrieval": {"method": "fts5_bm25", "rank": 1},
                }
            ],
            "retrievalMode": "lexical",
            "fallbackReason": None,
        },
    )
    mock_grounded_answer(monkeypatch)
    tutor = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/tutor/answers",
            json={"question": "Explain it", "graphConceptId": concept_id},
        )
    )
    assert tutor.status_code == 200
    assert tutor.json()["status"] == "answered"


# ---------------------------------------------------------------------------
# Journey 7: quiz → answer → analytics → coach → delete → consistency
# ---------------------------------------------------------------------------


def test_quiz_analytics_coach_delete_consistency(tmp_path, monkeypatch):
    """Quiz, answer, analytics, coach, then delete — verify cascading consistency."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    app = create_app(settings)
    mock_quiz_generator(monkeypatch)
    uploaded = post_pdf(
        app,
        make_pdf_pages(["Chlorophyll absorbs light energy in leaves."]),
    )
    document_id = uploaded.json()["documentId"]

    # Generate quiz.
    quiz_response = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/quizzes",
            json={"topic": "chlorophyll", "questionCount": 1, "documentIds": [document_id]},
        )
    )
    assert quiz_response.status_code == 200
    quiz = quiz_response.json()
    assert quiz["status"] == "ready"
    question = quiz["questions"][0]
    quiz_id = quiz["quizId"]

    # Submit an incorrect answer.
    attempt = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/quizzes/{quiz_id}/questions/{question['questionId']}/attempts",
            json={"answer": "B"},
        )
    )
    assert attempt.status_code == 200
    assert attempt.json()["outcome"] == "incorrect"

    # Analytics records the attempt.
    analytics = asyncio.run(request(app, "GET", "/api/v1/learning-analytics"))
    assert analytics.json()["totals"]["attemptCount"] == 1

    # Study session.
    session = asyncio.run(
        request(app, "POST", "/api/v1/study-sessions", json={"topic": "chlorophyll"})
    )
    assert session.status_code == 201
    completed = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/study-sessions/{session.json()['sessionId']}/complete",
        )
    )
    assert completed.status_code == 200

    # Coach plan references the source.
    plan = asyncio.run(request(app, "GET", "/api/v1/study-coach/plan"))
    assert plan.status_code == 200
    recommendation = plan.json()["recommendations"][0]
    assert recommendation["citations"][0]["documentId"] == document_id

    # Delete document — recommendations empty, quiz attempts reset, analytics attempts reset.
    removed = asyncio.run(request(app, "DELETE", f"/api/v1/documents/{document_id}"))
    assert removed.status_code == 200

    after_delete_plan = asyncio.run(request(app, "GET", "/api/v1/study-coach/plan"))
    assert after_delete_plan.json()["recommendations"] == []

    after_analytics = asyncio.run(request(app, "GET", "/api/v1/learning-analytics"))
    assert after_analytics.json()["totals"]["attemptCount"] == 0
    assert after_analytics.json()["totals"]["completedStudySessions"] == 1


# ---------------------------------------------------------------------------
# Journey 8: provider failure, malformed input, empty states, database errors
# ---------------------------------------------------------------------------


def test_provider_failure_malformed_input_empty_states(tmp_path):
    """Tutor unavailable, malformed queries, and empty states are handled safely."""
    settings = make_settings(tmp_path)
    app = create_app(settings)

    # Upload a document so tutor has evidence; tutor unconfigured returns 503.
    uploaded = post_pdf(app, make_pdf_pages(["Evaporation changes liquid water."]))
    assert uploaded.status_code == 201

    # Tutor not configured → provider unavailable.
    unavailable = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/tutor/answers",
            json={"question": "What is evaporation?"},
        )
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "TUTOR_MODEL_UNAVAILABLE"

    # Malformed retrieve query.
    invalid_query = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/retrieve",
            json={"query": "!!!", "limit": TOP_K},
        )
    )
    assert invalid_query.status_code == 422
    assert invalid_query.json()["error"]["code"] == "INVALID_QUERY"

    # Empty retrieve query.
    empty_query = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/retrieve",
            json={"query": "   ", "limit": TOP_K},
        )
    )
    assert empty_query.status_code == 422

    # Retrieve with no indexed documents returns empty, not an error.
    no_docs_app = create_app(make_settings(tmp_path / "empty-db"))
    no_docs = asyncio.run(
        request(
            no_docs_app,
            "POST",
            "/api/v1/retrieve",
            json={"query": "chlorophyll", "limit": TOP_K},
        )
    )
    assert no_docs.status_code == 200
    assert no_docs.json()["results"] == []

    # Document not found.
    missing = asyncio.run(request(app, "GET", "/api/v1/documents/does-not-exist"))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"

    # Error response does not leak internals.
    body = missing.json()
    assert "stack" not in str(body).lower()
    assert "traceback" not in str(body).lower()
    assert "sqlite" not in str(body).lower()
    assert "requestId" in body["error"]


def test_database_transaction_rollback(tmp_path):
    """A failed chunk insert leaves no document or FTS rows behind."""
    import sqlite3

    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    connection = sqlite3.connect(settings.database_path)
    connection.execute(
        "CREATE TRIGGER fail_chunk BEFORE INSERT ON chunks "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    connection.commit()
    connection.close()

    from app.ingestion.pdf import ingest_pdf

    with pytest.raises(ApiError) as error:
        ingest_pdf(
            io.BytesIO(make_pdf_pages(["rollback transaction"])),
            filename="x.pdf",
            database_path=settings.database_path,
            settings=settings,
        )
    assert error.value.code == "STORAGE_ERROR"

    connection = sqlite3.connect(settings.database_path)
    assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == 0
    connection.close()


def test_graph_extraction_without_local_model_is_unavailable(tmp_path):
    """Knowledge graph extraction reports unavailable instead of fabricating."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    # Seed a document so there is something to extract.
    initialize_storage(settings.database_path)
    store_document(
        settings.database_path,
        {
            "document_id": "doc_graph_1",
            "display_name": "bio.pdf",
            "sha256": "a" * 64,
            "byte_size": 101,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "parser_version": "fixture",
        },
        [
            {
                "chunk_id": "chunk_graph_1",
                "page_number": 1,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": 20,
                "text": "Photosynthesis uses chlorophyll.",
            }
        ],
    )
    response = asyncio.run(
        request(app, "POST", "/api/v1/knowledge-graph/extract", json={"chunkLimit": 1})
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TUTOR_MODEL_UNAVAILABLE"
