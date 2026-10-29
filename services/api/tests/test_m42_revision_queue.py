"""M41 revision queue regression tests.

Tests the flashcard/quiz revision queue with spaced-repetition scheduling:
- Due/overdue item retrieval with user isolation
- Review submission (remembered/partially/forgotten) with interval scheduling
- Duplicate review submissions overwrite previous
- Deleted source documents remove sections from queue
- Cross-user isolation

Run with:

    uv run --project services/api --extra dev pytest services/api/tests/test_m42_revision_queue.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from auth_helpers import authenticated_request, prepare_test_account
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
    record_quiz_attempt,
    store_document,
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
    output = __import__("io").BytesIO()
    writer.write(output)
    return output.getvalue()


def make_settings(tmp_path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        database_path=tmp_path / "m42-revision.sqlite3",
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


def revision_queue(app, limit: int = 20, offset: int = 0):
    return asyncio.run(
        request(
            app,
            "GET",
            f"/api/v1/revision/queue?limit={limit}&offset={offset}",
        )
    )


def submit_review(app, item_type: str, item_id: str, result: str, confidence_rating: int | None = None):
    return asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/revision/review/{item_type}/{item_id}",
            json={"result": result, "confidenceRating": confidence_rating},
        )
    )


# ---------------------------------------------------------------------------
# M42: Revision queue tests
# ---------------------------------------------------------------------------


def test_revision_queue_flashcard_and_quiz_items(tmp_path):
    """Revision queue returns due flashcard sections and quiz questions."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    # Upload a document
    uploaded = post_pdf(
        app,
        make_pdf_pages(["Chlorophyll absorbs light energy in leaves during photosynthesis."]),
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documentId"]

    # Create a content resource with a flashcard section
    resource = create_content_resource(
        settings.database_path,
        user_id=user_id,
        title="Photosynthesis notes",
        description=None,
        format="flashcards",
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

    # Create a flashcard section that is already due (reviewed long ago)
    past_date = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    create_content_section(
        settings.database_path,
        resource_id=resource_id,
        user_id=user_id,
        section_type="flashcard",
        section_title="What is chlorophyll?",
        content="Chlorophyll absorbs light energy.",
        citations=[
            {
                "citationId": "chunk1",
                "documentId": document_id,
                "displayFilename": "lesson.pdf",
                "pageNumber": 1,
                "chunkIndex": 0,
                "charStart": 0,
                "charEnd": 50,
                "text": "Chlorophyll absorbs light energy in leaves during photosynthesis.",
                "retrieval": {"method": "fts5_bm25", "rank": 1},
            }
        ],
    )

    # Record a past review so it's due
    from app.storage import connect_database
    import sqlite3

    with connect_database(settings.database_path) as conn:
        # First, we need to get the actual section_id - let's query it
        section_row = conn.execute(
            "SELECT section_id FROM content_resource_sections WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        section_id = section_row["section_id"]

        # Insert a flashcard review item that is due now
        conn.execute(
            """INSERT INTO flashcard_review_items(
                   section_id, resource_id, section_title, last_result,
                   confidence_rating, reviewed_at, due_at, user_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                section_id,
                resource_id,
                "What is chlorophyll?",
                "forgotten",
                None,
                past_date,
                past_date,
                user_id,
            ),
        )

    result = revision_queue(app)
    assert result.status_code == 200
    body = result.json()
    assert body["total"] >= 1
    flashcard_items = [r for r in body["items"] if r["itemType"] == "flashcard"]
    assert len(flashcard_items) >= 1
    assert flashcard_items[0]["itemId"] == section_id


def test_revision_queue_quiz_question_due(tmp_path):
    """Quiz questions that need review appear in the queue."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    uploaded = post_pdf(
        app,
        make_pdf_pages(["Chlorophyll absorbs light energy in leaves."]),
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documentId"]

    # Create a quiz
    create_quiz_set(
        settings.database_path,
        {
            "quiz_id": "quiz_photo",
            "topic_id": "topic_photo",
            "topic": "photosynthesis",
            "created_at": "2026-01-01T00:00:00Z",
            "questions": [
                {
                    "question_id": "q1",
                    "quiz_id": "quiz_photo",
                    "position": 0,
                    "question_type": "multiple_choice",
                    "prompt": "What does chlorophyll absorb?",
                    "options": [{"id": "A", "text": "Light energy"}],
                    "answer_key": "A",
                    "accepted_answers": [],
                    "explanation": "Chlorophyll absorbs light energy.",
                    "citations": [],
                    "user_id": user_id,
                }
            ],
        },
        user_id=user_id,
    )

    # Record a quiz attempt (incorrect → due in 1 day)
    from app.storage import record_quiz_attempt
    attempt_id = "attempt_q1"
    record_quiz_attempt(
        settings.database_path,
        attempt_id=attempt_id,
        question_id="q1",
        outcome="incorrect",
        created_at="2026-01-01T00:00:00Z",
        user_id=user_id,
    )

    result = revision_queue(app)
    assert result.status_code == 200
    body = result.json()
    quiz_items = [r for r in body["items"] if r["itemType"] == "quiz"]
    assert len(quiz_items) >= 1
    assert quiz_items[0]["itemId"] == "q1"
    assert quiz_items[0]["title"] == "What does chlorophyll absorb?"


def test_review_submission_schedules_next_review(tmp_path):
    """Submitting a review schedules the next review with correct interval."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    uploaded = post_pdf(
        app,
        make_pdf_pages(["Chlorophyll absorbs light energy."]),
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documentId"]

    resource = create_content_resource(
        settings.database_path,
        user_id=user_id,
        title="Test resource",
        description=None,
        format="notes",
        detail_level="standard",
        source_type="document",
        source_refs=[],
    )
    resource_id = resource["resourceId"]

    create_content_section(
        settings.database_path,
        resource_id=resource_id,
        user_id=user_id,
        section_type="flashcard",
        section_title="Test flashcard",
        content="Test content about chlorophyll.",
        citations=[],
    )

    from app.storage import connect_database
    section_id = None
    with connect_database(settings.database_path) as conn:
        row = conn.execute(
            "SELECT section_id FROM content_resource_sections WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        section_id = row["section_id"]

    # Submit review with "remembered" → should schedule 7 days out
    result = submit_review(app, "flashcard", section_id, "remembered", confidence_rating=5)
    assert result.status_code == 200
    body = result.json()
    assert body["sectionId"] == section_id
    assert body["result"] == "remembered"
    assert body["confidenceRating"] == 5

    # Verify the due date is 7 days from now
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    due = datetime.fromisoformat(body["dueAt"].replace("Z", "+00:00"))
    expected_due = now + timedelta(days=7)
    assert abs((due - expected_due).total_seconds()) < 60, f"Expected ~{expected_due}, got {due}"

    # Submit review with "forgotten" → should schedule 1 day out
    result2 = submit_review(app, "flashcard", section_id, "forgotten", confidence_rating=1)
    assert result2.status_code == 200
    body2 = result2.json()
    assert body2["result"] == "forgotten"
    due2 = datetime.fromisoformat(body2["dueAt"].replace("Z", "+00:00"))
    expected_due2 = now + timedelta(days=1)
    assert abs((due2 - expected_due2).total_seconds()) < 60


def test_review_submission_duplicate_overwrites(tmp_path):
    """Submitting a review twice overwrites the previous result."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    uploaded = post_pdf(
        app,
        make_pdf_pages(["Test content."]),
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documentId"]

    resource = create_content_resource(
        settings.database_path,
        user_id=user_id,
        title="Test",
        description=None,
        format="notes",
        detail_level="standard",
        source_type="document",
        source_refs=[],
    )
    resource_id = resource["resourceId"]

    create_content_section(
        settings.database_path,
        resource_id=resource_id,
        user_id=user_id,
        section_type="flashcard",
        section_title="Test card",
        content="Test content.",
        citations=[],
    )

    from app.storage import connect_database
    section_id = None
    with connect_database(settings.database_path) as conn:
        row = conn.execute(
            "SELECT section_id FROM content_resource_sections WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        section_id = row["section_id"]

    # First review: remembered
    r1 = submit_review(app, "flashcard", section_id, "remembered", confidence_rating=5)
    assert r1.status_code == 200
    assert r1.json()["result"] == "remembered"

    # Second review: forgotten (overwrites)
    r2 = submit_review(app, "flashcard", section_id, "forgotten", confidence_rating=1)
    assert r2.status_code == 200
    assert r2.json()["result"] == "forgotten"
    assert r2.json()["confidenceRating"] == 1


def test_review_unknown_section_returns_404(tmp_path):
    """Reviewing a non-existent section returns 404."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    result = submit_review(app, "flashcard", "nonexistent_section", "remembered")
    assert result.status_code == 404


def test_review_unknown_quiz_returns_404(tmp_path):
    """Reviewing a non-existent quiz question returns 404."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    result = submit_review(app, "quiz", "nonexistent_question", "remembered")
    assert result.status_code == 404


def test_revision_queue_cross_user_isolation(tmp_path):
    """User B cannot see User A's revision queue items."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    uploaded = post_pdf(
        app,
        make_pdf_pages(["Chlorophyll absorbs light energy."]),
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documentId"]

    resource = create_content_resource(
        settings.database_path,
        user_id=user_id,
        title="Test",
        description=None,
        format="notes",
        detail_level="standard",
        source_type="document",
        source_refs=[],
    )
    resource_id = resource["resourceId"]

    create_content_section(
        settings.database_path,
        resource_id=resource_id,
        user_id=user_id,
        section_type="flashcard",
        section_title="User A card",
        content="Test.",
        citations=[],
    )

    from app.storage import connect_database
    section_id = None
    with connect_database(settings.database_path) as conn:
        row = conn.execute(
            "SELECT section_id FROM content_resource_sections WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        section_id = row["section_id"]

    # Record a review for user A's section (past reviewed_at → past due_at)
    from app.storage import record_flashcard_review
    past_review = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    record_flashcard_review(
        settings.database_path,
        section_id=section_id,
        result="forgotten",
        confidence_rating=None,
        user_id=user_id,
        reviewed_at=past_review,
    )

    # User A's queue should have the item
    result_a = revision_queue(app)
    assert result_a.status_code == 200
    body_a = result_a.json()
    flashcard_a = [r for r in body_a["items"] if r["itemType"] == "flashcard"]
    assert len(flashcard_a) >= 1

    # User B's queue should be empty (different database)
    settings_b = make_settings(tmp_path / "user_b")
    app_b = create_app(settings_b)
    prepare_test_account(app_b)

    result_b = revision_queue(app_b)
    assert result_b.status_code == 200
    assert result_b.json()["total"] == 0


def test_deleted_document_removes_sections_from_queue(tmp_path):
    """Deleting a document removes its sections from the revision queue."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    uploaded = post_pdf(
        app,
        make_pdf_pages(["Test content."]),
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documentId"]

    resource = create_content_resource(
        settings.database_path,
        user_id=user_id,
        title="Test",
        description=None,
        format="notes",
        detail_level="standard",
        source_type="document",
        source_refs=[
            {
                "type": "document",
                "documentId": document_id,
                "displayFilename": "test.pdf",
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
        section_type="flashcard",
        section_title="Test card",
        content="Test content.",
        citations=[
            {
                "citationId": "chunk1",
                "documentId": document_id,
                "displayFilename": "test.pdf",
                "pageNumber": 1,
                "chunkIndex": 0,
                "charStart": 0,
                "charEnd": 20,
                "text": "Test content.",
                "retrieval": {"method": "fts5_bm25", "rank": 1},
            }
        ],
    )

    from app.storage import connect_database
    section_id = None
    with connect_database(settings.database_path) as conn:
        row = conn.execute(
            "SELECT section_id FROM content_resource_sections WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        section_id = row["section_id"]

    # Record a review (past reviewed_at → past due_at)
    from app.storage import record_flashcard_review
    past_review = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    record_flashcard_review(
        settings.database_path,
        section_id=section_id,
        result="forgotten",
        confidence_rating=None,
        user_id=user_id,
        reviewed_at=past_review,
    )

    # Queue should have the item
    result_before = revision_queue(app)
    assert result_before.status_code == 200
    assert result_before.json()["total"] >= 1

    # Delete the document
    deleted = asyncio.run(
        request(app, "DELETE", f"/api/v1/documents/{document_id}")
    )
    assert deleted.status_code == 200

    # Queue should now be empty
    result_after = revision_queue(app)
    assert result_after.status_code == 200
    assert result_after.json()["total"] == 0


def test_review_quiz_question(tmp_path):
    """Reviewing a quiz question schedules next review."""
    settings = make_settings(tmp_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)

    uploaded = post_pdf(
        app,
        make_pdf_pages(["Chlorophyll absorbs light energy."]),
    )
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documentId"]

    create_quiz_set(
        settings.database_path,
        {
            "quiz_id": "quiz_photo",
            "topic_id": "topic_photo",
            "topic": "photosynthesis",
            "created_at": "2026-01-01T00:00:00Z",
            "questions": [
                {
                    "question_id": "q1",
                    "quiz_id": "quiz_photo",
                    "position": 0,
                    "question_type": "multiple_choice",
                    "prompt": "What does chlorophyll absorb?",
                    "options": [{"id": "A", "text": "Light energy"}],
                    "answer_key": "A",
                    "accepted_answers": [],
                    "explanation": "Chlorophyll absorbs light energy.",
                    "citations": [],
                    "user_id": user_id,
                }
            ],
        },
        user_id=user_id,
    )

    # Record a quiz attempt first (creates topic_review_items entry)
    from app.storage import record_quiz_attempt
    record_quiz_attempt(
        settings.database_path,
        attempt_id="attempt_q1",
        question_id="q1",
        outcome="incorrect",
        created_at="2026-01-01T00:00:00Z",
        user_id=user_id,
    )

    # Review the quiz question
    result = submit_review(app, "quiz", "q1", "remembered", confidence_rating=4)
    assert result.status_code == 200
    assert result.json()["questionId"] == "q1"
    assert result.json()["result"] == "remembered"
    assert result.json()["confidenceRating"] == 4
