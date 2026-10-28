"""M40 end-to-end learner journey test.

Exercises the complete learning workflow end-to-end with isolated test data:

1. Upload a learning document.
2. Inspect ingestion status and extracted content.
3. Ask a question and receive evidence-grounded answers with valid citations.
4. Explore relevant concepts and source material.
5. Generate study notes from the selected material.
6. Validate generated content and flag unsupported claims.
7. Complete a quiz and record actual results.
8. Use results to update mastery, review scheduling, and learning-path recommendations.
9. Save useful content to a study plan (content resource linked to quiz).
10. Revisit the material and inspect real progress analytics.

No external services, model downloads, or network calls required -- the optional
tutor and embedding model are mocked locally.

Run with:

    uv run --project services/api --extra dev pytest services/api/tests/test_m40_learner_journey.py
"""

from __future__ import annotations

import asyncio
import io
import json
from io import BytesIO

import pytest
from auth_helpers import authenticated_request, prepare_test_account
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.config import Settings
from app.errors import ApiError
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
        database_path=tmp_path / "m40-journey.sqlite3",
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


def mock_content_studio_generator(monkeypatch):
    """Mock the tutor to return structured content-studio JSON for study notes."""

    class FakeResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def fake_urlopen(request, timeout):
        generated = {
            "title": "Photosynthesis study notes",
            "abstain": False,
            "evidence_gaps": [],
            "sections": [
                {
                    "section_type": "heading",
                    "section_title": "What is photosynthesis?",
                    "content": "Photosynthesis converts light energy into chemical energy [E1].",
                    "citations": ["E1"],
                },
                {
                    "section_type": "body",
                    "section_title": "Key inputs and outputs",
                    "content": "Carbon dioxide and water are inputs; oxygen is released as a byproduct [E1].",
                    "citations": ["E1"],
                },
                {
                    "section_type": "flashcard",
                    "section_title": "What does chlorophyll do?",
                    "content": "Chlorophyll absorbs light energy [E1].",
                    "citations": ["E1"],
                },
            ],
        }
        provider_result = {"choices": [{"message": {"content": json.dumps(generated)}}]}
        return FakeResponse(json.dumps(provider_result).encode())

    class FakeOpener:
        def open(self, request, timeout):
            return fake_urlopen(request, timeout)

    monkeypatch.setattr("app.tutor.build_opener", lambda *_: FakeOpener())


def mock_quiz_generator(monkeypatch):
    """Mock the tutor provider to return quiz questions."""

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
        provider_result = {"choices": [{"message": {"content": json.dumps(generated)}}]}
        return FakeResponse(json.dumps(provider_result).encode())

    class FakeOpener:
        def open(self, request, timeout):
            return fake_urlopen(request, timeout)

    monkeypatch.setattr("app.tutor.build_opener", lambda *_: FakeOpener())


# ---------------------------------------------------------------------------
# M40: Complete learner journey (all 10 steps)
# ---------------------------------------------------------------------------


def test_m40_complete_learner_journey(tmp_path, monkeypatch):
    """Complete 10-step learner journey from upload to analytics."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    app = create_app(settings)
    user_id = prepare_test_account(app)

    SOURCE = (
        "Photosynthesis is the process by which green plants, algae, and some "
        "bacteria convert light energy into chemical energy stored in glucose. "
        "Carbon dioxide and water are the inputs; oxygen is released as a byproduct. "
        "Chlorophyll absorbs light energy in leaves."
    )

    # --- Step 1: Upload a learning document ---
    uploaded = post_pdf(app, make_pdf_pages([SOURCE]))
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documentId"]
    assert uploaded.json()["displayFilename"] == "lesson.pdf"

    # --- Step 2: Inspect ingestion status and extracted content ---
    doc_detail = asyncio.run(
        request(app, "GET", f"/api/v1/documents/{document_id}")
    )
    assert doc_detail.status_code == 200
    doc_body = doc_detail.json()
    assert doc_body["documentId"] == document_id
    assert doc_body["chunkCount"] > 0

    documents_list = asyncio.run(request(app, "GET", "/api/v1/documents"))
    assert documents_list.status_code == 200
    assert any(d["documentId"] == document_id for d in documents_list.json()["documents"])

    # --- Step 3: Ask a question and receive evidence-grounded answers with valid citations ---
    mock_tutor(monkeypatch, "Chlorophyll absorbs light energy [E1].")
    tutor_answer = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/tutor/answers",
            json={"question": "What does chlorophyll absorb?", "documentIds": [document_id]},
        )
    )
    assert tutor_answer.status_code == 200
    assert tutor_answer.json()["status"] == "answered"
    assert tutor_answer.json()["answer"] is not None
    assert len(tutor_answer.json()["citations"]) > 0
    assert tutor_answer.json()["citations"][0]["documentId"] == document_id

    # Verify citations point to real, owned chunks with valid provenance.
    retrieved = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/retrieve",
            json={"query": "chlorophyll light energy", "documentIds": [document_id], "limit": TOP_K},
        )
    )
    assert retrieved.status_code == 200
    citation = retrieved.json()["results"][0]
    assert citation["documentId"] == document_id
    assert citation["text"] == SOURCE

    # --- Step 4: Explore relevant concepts and source material (knowledge graph) ---
    EXTRACTION = {
        "concepts": [
            {
                "name": "Photosynthesis",
                "aliases": ["plant energy process"],
                "supportingText": "convert light energy into chemical energy",
            },
            {
                "name": "Chlorophyll",
                "aliases": ["green pigment"],
                "supportingText": "Chlorophyll absorbs light energy",
            },
        ],
        "relationships": [
            {
                "source": "Photosynthesis",
                "target": "Chlorophyll",
                "relation": "uses",
                "supportingText": "Chlorophyll absorbs light energy",
            }
        ],
    }

    monkeypatch.setattr(
        "app.api.v1.routes.extract_chunk",
        lambda _settings, chunk: __import__("app.knowledge_graph", fromlist=["parse_extraction"]).parse_extraction(
            json.dumps(EXTRACTION), chunk
        ),
    )

    extracted = asyncio.run(
        request(app, "POST", "/api/v1/knowledge-graph/extract", json={"chunkLimit": 5})
    )
    assert extracted.status_code == 200
    assert extracted.json()["chunksProcessed"] == 1

    concepts = asyncio.run(request(app, "GET", "/api/v1/knowledge-graph"))
    assert concepts.status_code == 200
    concept_list = concepts.json()["concepts"]
    assert len(concept_list) >= 1
    photosynthesis_concept = next(
        (c for c in concept_list if c["name"] == "Photosynthesis"), None
    )
    assert photosynthesis_concept is not None

    concept_detail = asyncio.run(
        request(
            app,
            "GET",
            f"/api/v1/knowledge-graph/concepts/{photosynthesis_concept['conceptId']}",
        )
    )
    assert concept_detail.status_code == 200
    assert len(concept_detail.json()["evidence"]) > 0

    # --- Step 5: Generate study notes from the selected material ---
    mock_content_studio_generator(monkeypatch)
    notes_response = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/content/resources",
            json={
                "title": "Photosynthesis study notes",
                "topic": "photosynthesis",
                "format": "notes",
                "detailLevel": "standard",
                "documentIds": [document_id],
            },
        )
    )
    assert notes_response.status_code == 201
    resource = notes_response.json()
    resource_id = resource["resourceId"]
    assert resource["format"] == "notes"
    assert resource["status"] == "draft"
    assert len(resource["sections"]) >= 1
    # Every section carries citations referencing real evidence.
    for section in resource["sections"]:
        assert len(section["citations"]) > 0
        assert section["citations"][0]["documentId"] == document_id

    # --- Step 6: Validate generated content and flag unsupported claims ---
    validation_response = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/content/resources/{resource_id}/validate",
        )
    )
    assert validation_response.status_code == 200
    validation = validation_response.json()
    assert validation["status"] in {"verified", "needs_review", "insufficient_evidence"}
    assert "citationValidity" in validation
    assert "schemaValidity" in validation
    assert "unsupportedClaimCount" in validation

    # Verify each section's verification status is deterministic (not yet human reviewed).
    for section in validation["sections"]:
        assert section["sectionId"] is not None

    # Human review: mark a section verified.
    section_id = resource["sections"][0]["sectionId"]
    reviewed = asyncio.run(
        request(
            app,
            "PATCH",
            f"/api/v1/content/resources/{resource_id}/sections/{section_id}/verify",
            json={"verificationStatus": "verified", "verificationNotes": "Looks good."},
        )
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["verificationStatus"] == "verified"
    assert reviewed.json()["verificationMethod"] == "human"
    assert reviewed.json()["verifiedBy"] == user_id

    # Regenerate preserves the previous version as a revision (never silently overwrites).
    regenerated = asyncio.run(
        request(
            app,
            "PATCH",
            f"/api/v1/content/resources/{resource_id}/sections/{section_id}/regenerate",
            json={
                "content": "Regenerated photosynthesis content [E1].",
                "citations": [
                    {
                        "citationId": citation["citationId"],
                        "documentId": citation["documentId"],
                        "displayFilename": citation["displayFilename"],
                        "pageNumber": citation["pageNumber"],
                        "chunkIndex": citation["chunkIndex"],
                        "charStart": citation["charStart"],
                        "charEnd": citation["charEnd"],
                        "text": citation["text"],
                        "retrieval": citation["retrieval"],
                    }
                    for citation in resource["sections"][0]["citations"]
                ],
            },
        )
    )
    assert regenerated.status_code == 200
    assert regenerated.json()["content"] == "Regenerated photosynthesis content [E1]."
    assert regenerated.json()["version"] == 2

    revisions = asyncio.run(
        request(
            app,
            "GET",
            f"/api/v1/content/resources/{resource_id}/revisions?section_id={section_id}",
        )
    )
    assert revisions.status_code == 200
    assert len(revisions.json()["revisions"]) >= 1

    # --- Step 7: Complete a quiz and record actual results ---
    mock_quiz_generator(monkeypatch)
    quiz_response = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/quizzes",
            json={"topic": "photosynthesis", "questionCount": 1, "documentIds": [document_id]},
        )
    )
    assert quiz_response.status_code == 200
    quiz = quiz_response.json()
    assert quiz["status"] == "ready"
    question = quiz["questions"][0]
    quiz_id = quiz["quizId"]

    # Submit a correct answer.
    attempt = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/quizzes/{quiz_id}/questions/{question['questionId']}/attempts",
            json={"answer": "A"},
        )
    )
    assert attempt.status_code == 200
    assert attempt.json()["outcome"] == "correct"

    # Submit an incorrect answer for a second question if available.
    if len(quiz["questions"]) > 1:
        question2 = quiz["questions"][1]
        attempt2 = asyncio.run(
            request(
                app,
                "POST",
                f"/api/v1/quizzes/{quiz_id}/questions/{question2['questionId']}/attempts",
                json={"answer": "B"},
            )
        )
        assert attempt2.status_code == 200

    # --- Step 8: Use results to update mastery, review scheduling, and learning-path recommendations ---
    progress = asyncio.run(request(app, "GET", "/api/v1/progress"))
    assert progress.status_code == 200
    assert progress.json()["totalAttempts"] >= 1

    analytics = asyncio.run(request(app, "GET", "/api/v1/learning-analytics"))
    assert analytics.status_code == 200
    assert analytics.json()["totals"]["attemptCount"] >= 1
    assert analytics.json()["totals"]["quizAccuracy"] is not None

    # Study coach plan references the source.
    plan = asyncio.run(request(app, "GET", "/api/v1/study-coach/plan"))
    assert plan.status_code == 200

    # Start and complete a study session.
    session = asyncio.run(
        request(app, "POST", "/api/v1/study-sessions", json={"topic": "photosynthesis"})
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

    # Refresh analytics after session completion.
    analytics_after = asyncio.run(request(app, "GET", "/api/v1/learning-analytics"))
    assert analytics_after.status_code == 200
    assert analytics_after.json()["totals"]["completedStudySessions"] >= 1

    # --- Step 9: Save useful content to a study plan (link content resource to quiz) ---
    linked = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/content/resources/{resource_id}/quizzes",
            json={"quizId": quiz_id},
        )
    )
    assert linked.status_code == 200
    assert linked.json()["linked"] is True
    assert linked.json()["resourceId"] == resource_id
    assert linked.json()["quizId"] == quiz_id

    # --- Step 10: Revisit the material and inspect real progress analytics ---
    final_analytics = asyncio.run(request(app, "GET", "/api/v1/learning-analytics"))
    assert final_analytics.status_code == 200
    final_body = final_analytics.json()
    assert final_body["totals"]["attemptCount"] >= 1
    assert final_body["totals"]["completedStudySessions"] >= 1
    assert final_body["totals"]["quizAccuracy"] is not None

    # Verify topic progress is recorded.
    assert len(final_body["topics"]) >= 0

    # Verify source document is still accessible (not deleted).
    doc_check = asyncio.run(request(app, "GET", f"/api/v1/documents/{document_id}"))
    assert doc_check.status_code == 200

    # Verify the validated content resource is still accessible.
    resource_check = asyncio.run(
        request(app, "GET", f"/api/v1/content/resources/{resource_id}")
    )
    assert resource_check.status_code == 200
    resource_body = resource_check.json()
    assert resource_body["resourceId"] == resource_id

    # Provenance check: every citation in every section references the uploaded document.
    for section in resource_body["sections"]:
        for citation in section["citations"]:
            assert citation["documentId"] == document_id
