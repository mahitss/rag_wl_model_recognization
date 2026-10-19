import asyncio
import io
import json
import sqlite3
from io import BytesIO

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.config import Settings
from app.errors import ApiError
from app.ingestion.chunking import chunk_page_text
from app.ingestion.pdf import ingest_pdf, safe_display_filename
from app.main import create_app
from app.storage import get_document, initialize_storage, search_chunks


def make_pdf(*pages: str, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    for text in pages:
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
    if encrypted:
        writer.encrypt("password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def make_settings(tmp_path, **overrides):
    return Settings(
        _env_file=None,
        database_path=tmp_path / "wonderlearn.sqlite3",
        cors_origins="http://localhost:5173",
        **overrides,
    )


async def request_with_lifespan(app, method, path, **kwargs):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.request(method, path, **kwargs)


def post_pdf(tmp_path, pdf: bytes, filename="lesson.pdf", **overrides):
    app = create_app(make_settings(tmp_path, **overrides))
    return asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/documents",
            files={"file": (filename, pdf, "application/pdf")},
        )
    )


def test_multi_page_pdf_ingestion_preserves_page_provenance(tmp_path):
    response = post_pdf(tmp_path, make_pdf("The moon has craters.", "Mars is a planet."))
    assert response.status_code == 201
    document_id = response.json()["documentId"]
    rows = search_chunks(make_settings(tmp_path).database_path, '"moon"', [document_id], 5)
    assert len(rows) == 1
    assert rows[0]["page_number"] == 1
    assert "moon" in rows[0]["text"]
    rows = search_chunks(make_settings(tmp_path).database_path, '"Mars"', [document_id], 5)
    assert rows[0]["page_number"] == 2
    app = create_app(make_settings(tmp_path))
    status = asyncio.run(request_with_lifespan(app, "GET", f"/api/v1/documents/{document_id}"))
    assert status.status_code == 200
    assert status.json()["status"] == "indexed"
    assert status.json()["pageCount"] == 2


def test_chunking_is_deterministic_word_bounded_and_page_local():
    text = "alpha beta gamma delta epsilon zeta eta theta"
    first = list(chunk_page_text(text, page_number=4, chunk_size=16, overlap=4))
    second = list(chunk_page_text(text, page_number=4, chunk_size=16, overlap=4))
    assert first == second
    assert all(item["page_number"] == 4 for item in first)
    assert all(item["text"] in text for item in first)
    assert all(item["char_end"] - item["char_start"] <= 16 for item in first)
    assert list(chunk_page_text("  \n", page_number=1, chunk_size=10, overlap=1)) == []


def test_invalid_content_and_textless_pdf_are_rejected(tmp_path):
    invalid = post_pdf(tmp_path, b"not a pdf", "spoof.pdf")
    malformed = post_pdf(tmp_path, b"%PDF-1.7\nnot really a PDF", "malformed.pdf")
    textless = post_pdf(tmp_path, make_pdf(""), "scan.pdf")
    assert invalid.status_code == 415
    assert invalid.json()["error"]["code"] == "UNSUPPORTED_FORMAT"
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "INVALID_PDF"
    assert textless.status_code == 422
    assert textless.json()["error"]["code"] == "NO_EXTRACTABLE_TEXT"


def test_encrypted_and_empty_files_are_rejected(tmp_path):
    encrypted = post_pdf(tmp_path, make_pdf("secret", encrypted=True))
    empty = post_pdf(tmp_path, b"")
    assert encrypted.status_code == 422
    assert encrypted.json()["error"]["code"] == "ENCRYPTED_PDF"
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "EMPTY_FILE"


def test_upload_limit_is_enforced(tmp_path):
    response = post_pdf(tmp_path, make_pdf("too large"), max_upload_bytes=10)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_duplicate_content_returns_existing_document_reference(tmp_path):
    pdf = make_pdf("same bytes for both uploads")
    first = post_pdf(tmp_path, pdf, "first.pdf")
    duplicate = post_pdf(tmp_path, pdf, "second.pdf")
    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["details"]["existingDocumentId"] == first.json()["documentId"]


def test_retrieval_answer_citations_and_query_validation(tmp_path):
    response = post_pdf(tmp_path, make_pdf("The water cycle includes evaporation."))
    document_id = response.json()["documentId"]
    app = create_app(make_settings(tmp_path))
    result = asyncio.run(
        request_with_lifespan(
            app, "POST", "/api/v1/retrieve", json={"query": "evaporation", "limit": 5}
        )
    )
    assert result.status_code == 200
    citation = result.json()["results"][0]
    assert citation["documentId"] == document_id
    assert citation["pageNumber"] == 1
    assert citation["citationId"].startswith("chk_")
    fts_syntax = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/retrieve",
            json={"query": "OR (evaporation* NEAR/2 cycle) AND"},
        )
    )
    assert fts_syntax.status_code == 200
    assert fts_syntax.json()["results"][0]["documentId"] == document_id
    answer = asyncio.run(
        request_with_lifespan(app, "POST", "/api/v1/answers", json={"question": "evaporation?"})
    )
    assert answer.json()["status"] == "evidence_found"
    assert answer.json()["answerType"] == "extractive_evidence"
    assert answer.json()["citations"][0]["citationId"] == citation["citationId"]
    no_match = asyncio.run(
        request_with_lifespan(
            app, "POST", "/api/v1/answers", json={"question": "quantum astronomy"}
        )
    )
    assert no_match.json()["status"] == "insufficient_evidence"
    assert no_match.json()["answer"] is None
    invalid = asyncio.run(
        request_with_lifespan(app, "POST", "/api/v1/retrieve", json={"query": "!!!"})
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_QUERY"
    empty = asyncio.run(
        request_with_lifespan(app, "POST", "/api/v1/retrieve", json={"query": "  "})
    )
    assert empty.status_code == 422
    common_words = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/retrieve",
            json={"query": "What is the boiling point of nitrogen?"},
        )
    )
    assert common_words.status_code == 200
    assert common_words.json()["results"] == []


def test_delete_removes_evidence_and_allows_reingestion(tmp_path):
    pdf = make_pdf("Evidence to delete remains searchable.")
    uploaded = post_pdf(tmp_path, pdf)
    document_id = uploaded.json()["documentId"]
    app = create_app(make_settings(tmp_path))
    removed = asyncio.run(request_with_lifespan(app, "DELETE", f"/api/v1/documents/{document_id}"))
    assert removed.status_code == 200
    assert get_document(make_settings(tmp_path).database_path, document_id) is None
    search = asyncio.run(
        request_with_lifespan(app, "POST", "/api/v1/retrieve", json={"query": "searchable"})
    )
    assert search.json()["results"] == []
    missing = asyncio.run(request_with_lifespan(app, "DELETE", f"/api/v1/documents/{document_id}"))
    assert missing.status_code == 404
    reingested = post_pdf(tmp_path, pdf)
    assert reingested.status_code == 201
    assert reingested.json()["documentId"] != document_id


def test_failed_chunk_transaction_leaves_no_document_or_fts_rows(tmp_path):
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    connection = sqlite3.connect(settings.database_path)
    connection.execute(
        "CREATE TRIGGER fail_chunk BEFORE INSERT ON chunks "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    connection.commit()
    connection.close()
    with pytest.raises(ApiError) as error:
        ingest_pdf(
            io.BytesIO(make_pdf("rollback transaction")),
            filename="x.pdf",
            database_path=settings.database_path,
            settings=settings,
        )
    assert error.value.code == "STORAGE_ERROR"
    connection = sqlite3.connect(settings.database_path)
    assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == 0
    connection.close()


def test_filename_paths_are_reduced_to_safe_display_name():
    assert safe_display_filename(r"..\..\secret\lesson.pdf") == "lesson.pdf"


def test_grounded_tutor_returns_only_real_retrieved_citations(tmp_path, monkeypatch):
    uploaded = post_pdf(tmp_path, make_pdf("Chlorophyll absorbs light energy in leaves."))
    document_id = uploaded.json()["documentId"]
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    app = create_app(settings)

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
                                "answer": "Chlorophyll absorbs light energy [E1].",
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
    result = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/tutor/answers",
            json={
                "question": "What does chlorophyll do?",
                "diagramId": "photosynthesis-cycle",
                "conceptId": "biology.photosynthesis.chlorophyll",
                "documentIds": [document_id],
            },
        )
    )
    body = result.json()
    assert result.status_code == 200
    assert body["status"] == "answered"
    assert body["citations"][0]["documentId"] == document_id
    assert body["citations"][0]["displayFilename"] == "lesson.pdf"
    assert body["citations"][0]["pageNumber"] == 1
    assert body["citations"][0]["citationId"].startswith("chk_")

    unknown_concept = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/tutor/answers",
            json={
                "question": "What happens?",
                "diagramId": "photosynthesis-cycle",
                "conceptId": "client.forged.concept",
            },
        )
    )
    assert unknown_concept.status_code == 404
    assert unknown_concept.json()["error"]["code"] == "CONCEPT_NOT_FOUND"


def test_tutor_empty_evidence_abstains_and_configured_model_failure_is_safe(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    empty = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/tutor/answers",
            json={"question": "Explain evaporation."},
        )
    )
    assert empty.status_code == 200
    assert empty.json()["status"] == "insufficient_evidence"
    assert empty.json()["answer"] is None
    assert empty.json()["citations"] == []

    post_pdf(tmp_path, make_pdf("Evaporation changes liquid water into water vapor."))
    unavailable = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/tutor/answers",
            json={"question": "What is evaporation?"},
        )
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "TUTOR_MODEL_UNAVAILABLE"


def test_quiz_attempts_persist_explain_with_real_citations_and_support_retry(tmp_path, monkeypatch):
    uploaded = post_pdf(
        tmp_path,
        make_pdf(
            "Chlorophyll absorbs light energy in leaves. Plants use this energy to make food."
        ),
    )
    document_id = uploaded.json()["documentId"]
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    app = create_app(settings)
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
            },
            {
                "type": "short_answer",
                "question": "What kind of energy does chlorophyll absorb?",
                "options": [],
                "correctOption": "",
                "expectedAnswer": "light energy",
                "acceptedAnswers": ["energy from light"],
                "explanation": "Chlorophyll absorbs light energy.",
                "citations": ["E1"],
            },
        ]
    }

    class FakeResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    class FakeOpener:
        def open(self, request, timeout):
            return FakeResponse(
                json.dumps({"choices": [{"message": {"content": json.dumps(generated)}}]}).encode()
            )

    monkeypatch.setattr("app.tutor.build_opener", lambda *_: FakeOpener())
    created = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/quizzes",
            json={
                "topic": "chlorophyll",
                "questionCount": 2,
                "documentIds": [document_id],
            },
        )
    )
    assert created.status_code == 200
    quiz = created.json()
    assert quiz["status"] == "ready"
    assert len(quiz["questions"]) == 2
    first = quiz["questions"][0]
    assert first["citations"] == []
    assert first["explanation"] is None
    quiz_id = quiz["quizId"]

    async def submit(question_id, answer):
        return await request_with_lifespan(
            app,
            "POST",
            f"/api/v1/quizzes/{quiz_id}/questions/{question_id}/attempts",
            json={"answer": answer},
        )

    wrong = asyncio.run(submit(first["questionId"], "B"))
    assert wrong.status_code == 200
    wrong_body = wrong.json()
    assert wrong_body["outcome"] == "incorrect"
    assert wrong_body["isCorrect"] is False
    assert wrong_body["expectedAnswer"] == "Light energy"
    citation = wrong_body["citations"][0]
    assert citation["documentId"] == document_id
    assert citation["displayFilename"] == "lesson.pdf"
    assert citation["pageNumber"] == 1
    assert citation["citationId"].startswith("chk_")

    review = asyncio.run(
        request_with_lifespan(app, "GET", f"/api/v1/quizzes/{quiz_id}?review_incorrect=true")
    )
    assert [item["questionId"] for item in review.json()["questions"]] == [first["questionId"]]
    retry = asyncio.run(submit(first["questionId"], "A"))
    assert retry.json()["outcome"] == "correct"
    assert retry.json()["attemptCount"] == 2

    ambiguous = asyncio.run(submit(quiz["questions"][1]["questionId"], "chlorophyll uses energy"))
    assert ambiguous.json()["outcome"] == "ambiguous"
    assert ambiguous.json()["isCorrect"] is None

    progress = asyncio.run(request_with_lifespan(app, "GET", "/api/v1/progress"))
    progress_body = progress.json()
    assert progress_body["totalAttempts"] == 3
    topic = progress_body["topics"][0]
    assert topic["attemptedQuestions"] == 2
    assert topic["correctQuestions"] == 1
    assert topic["ambiguousQuestions"] == 1
    assert topic["needsReview"] is True
    assert len(progress_body["recentAttempts"]) == 3

    restarted_app = create_app(settings)
    persisted = asyncio.run(request_with_lifespan(restarted_app, "GET", "/api/v1/progress"))
    assert persisted.json()["totalAttempts"] == 3

    removed = asyncio.run(request_with_lifespan(app, "DELETE", f"/api/v1/documents/{document_id}"))
    assert removed.status_code == 200
    forgotten = asyncio.run(request_with_lifespan(app, "GET", "/api/v1/progress"))
    assert forgotten.json()["totalAttempts"] == 0


def test_release_journey_upload_to_diagram_quiz_coach_analytics_and_source_deletion(
    tmp_path, monkeypatch
):
    upload = post_pdf(
        tmp_path,
        make_pdf(
            "Chlorophyll absorbs light energy in leaves. "
            "Plants use this energy to make food during photosynthesis."
        ),
        "biology.pdf",
    )
    assert upload.status_code == 201
    document_id = upload.json()["documentId"]
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    app = create_app(settings)
    query = asyncio.run(
        request_with_lifespan(
            app, "POST", "/api/v1/retrieve", json={"query": "chlorophyll light", "limit": 5}
        )
    )
    assert query.status_code == 200
    assert query.json()["results"][0]["documentId"] == document_id

    diagram_evidence = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/diagrams/photosynthesis-cycle/regions/chlorophyll/evidence",
            json={"limit": 5},
        )
    )
    assert diagram_evidence.status_code == 200
    assert diagram_evidence.json()["results"][0]["pageNumber"] == 1
    assert diagram_evidence.json()["results"][0]["documentId"] == document_id

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
                "explanation": "The source says chlorophyll absorbs light energy.",
                "citations": ["E1"],
            }
        ]
    }

    class FakeResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    class FakeOpener:
        def open(self, request, timeout):
            return FakeResponse(
                json.dumps({"choices": [{"message": {"content": json.dumps(generated)}}]}).encode()
            )

    monkeypatch.setattr("app.tutor.build_opener", lambda *_: FakeOpener())
    quiz_response = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/quizzes",
            json={"topic": "chlorophyll", "questionCount": 1, "documentIds": [document_id]},
        )
    )
    assert quiz_response.status_code == 200
    quiz = quiz_response.json()
    question = quiz["questions"][0]
    attempt = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            f"/api/v1/quizzes/{quiz['quizId']}/questions/{question['questionId']}/attempts",
            json={"answer": "B"},
        )
    )
    assert attempt.status_code == 200
    assert attempt.json()["outcome"] == "incorrect"
    assert attempt.json()["citations"][0]["documentId"] == document_id

    session = asyncio.run(
        request_with_lifespan(app, "POST", "/api/v1/study-sessions", json={"topic": "chlorophyll"})
    )
    assert session.status_code == 201
    completed = asyncio.run(
        request_with_lifespan(
            app, "POST", f"/api/v1/study-sessions/{session.json()['sessionId']}/complete"
        )
    )
    assert completed.status_code == 200

    plan = asyncio.run(request_with_lifespan(app, "GET", "/api/v1/study-coach/plan"))
    assert plan.status_code == 200
    recommendation = plan.json()["recommendations"][0]
    assert recommendation["quizTopic"] == "chlorophyll"
    assert recommendation["citations"][0]["documentId"] == document_id
    assert recommendation["citations"][0]["pageNumber"] == 1
    assert any("incorrect" in reason for reason in recommendation["reasons"])
    assert any("study session" in reason for reason in recommendation["reasons"])

    analytics = asyncio.run(request_with_lifespan(app, "GET", "/api/v1/learning-analytics"))
    assert analytics.json()["totals"]["attemptCount"] == 1
    assert analytics.json()["totals"]["completedStudySessions"] == 1
    removed = asyncio.run(request_with_lifespan(app, "DELETE", f"/api/v1/documents/{document_id}"))
    assert removed.status_code == 200
    after_delete = asyncio.run(request_with_lifespan(app, "GET", "/api/v1/study-coach/plan"))
    assert after_delete.json()["recommendations"] == []
    after_analytics = asyncio.run(request_with_lifespan(app, "GET", "/api/v1/learning-analytics"))
    assert after_analytics.json()["totals"]["attemptCount"] == 0
    assert after_analytics.json()["totals"]["completedStudySessions"] == 1


def test_quiz_without_retrieved_evidence_does_not_call_model(tmp_path):
    app = create_app(make_settings(tmp_path))
    response = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/quizzes",
            json={"topic": "unsupported subject", "questionCount": 2},
        )
    )
    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_evidence"
    assert response.json()["questions"] == []


def test_quiz_requires_local_model_when_evidence_exists(tmp_path):
    post_pdf(tmp_path, make_pdf("Chlorophyll absorbs light energy in leaves."))
    app = create_app(make_settings(tmp_path))
    response = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/quizzes",
            json={"topic": "chlorophyll", "questionCount": 1},
        )
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TUTOR_MODEL_UNAVAILABLE"
