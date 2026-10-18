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
