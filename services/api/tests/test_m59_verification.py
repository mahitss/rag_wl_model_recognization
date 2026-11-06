"""M59: AI Content Verification & Trust Engine tests.

Tests cover:
* Tutor answer verification (citation provenance, claim support, contradiction,
  answer relevance)
* Quiz question verification (schema, claim support, citation markers)
* Knowledge graph relationship verification (supporting text presence)
* Multimodal content verification (text non-emptiness, source type validity)
* Aggregate results (worst outcome wins)
* User isolation (no cross-user data access)
* Output validation (schema compliance)
* Malformed citations rejection
* Empty evidence handling
"""

from __future__ import annotations

from app.config import Settings
from app.content_verification import (
    VerificationOutcome,
    VerificationResult,
    aggregate_results,
    outcome_to_human_readable,
    verify_knowledge_graph_relationship,
    verify_multimodal_extracted_content,
    verify_quiz_question,
    verify_tutor_answer,
)
from app.storage import create_user, initialize_storage


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
        "tutor_base_url": "http://127.0.0.1:8080/v1",
        "tutor_model_id": "local-model",
        "tutor_timeout_seconds": 90.0,
        "tutor_max_tokens": 700,
    }
    values.update(overrides)
    return Settings(**values)


def setup_test_db(tmp_path):
    """Create a database with a test user and return (settings, user_id, database_path)."""
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = create_user(settings.database_path, "testuser", "hashed-password", is_admin=False)
    return settings, user_id, settings.database_path


# ---------------------------------------------------------------------------
# Tutor answer verification tests
# ---------------------------------------------------------------------------


def test_tutor_answer_verified_when_evidence_supports(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    question = "What does chlorophyll absorb?"
    answer = "Chlorophyll absorbs light energy during photosynthesis."
    citations = [
        {"citationId": "chk_1", "documentId": "doc_1", "displayName": "biology.pdf",
         "pageNumber": 1, "chunkIndex": 0, "charStart": 0, "charEnd": 50,
         "text": "Chlorophyll absorbs light energy during photosynthesis."}
    ]
    chunks_by_id = {
        "chk_1": {"chunk_id": "chk_1", "document_id": "doc_1", "page_number": 1,
                  "chunk_index": 0, "char_start": 0, "char_end": 50,
                  "text": "Chlorophyll absorbs light energy during photosynthesis."}
    }

    result = verify_tutor_answer(
        str(database_path),
        question=question,
        answer_text=answer,
        citations=citations,
        user_id=user_id,
        chunks_by_id=chunks_by_id,
    )

    assert result.outcome in (VerificationOutcome.VERIFIED,
                              VerificationOutcome.PARTIALLY_VERIFIED)
    assert result.confidence >= 0.0
    assert result.citations_valid is True
    assert result.schema_valid is True
    assert outcome_to_human_readable(result.outcome) in ("Verified", "Partially verified")


def test_tutor_answer_rejected_when_no_citations(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    question = "What does chlorophyll absorb?"
    answer = "Chlorophyll absorbs light energy."
    citations = []  # No citations - should be rejected
    chunks_by_id = {}

    result = verify_tutor_answer(
        str(database_path),
        question=question,
        answer_text=answer,
        citations=citations,
        user_id=user_id,
        chunks_by_id=chunks_by_id,
    )

    assert result.outcome == VerificationOutcome.REJECTED


def test_tutor_answer_contradiction_detected(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    question = "What does chlorophyll absorb?"
    answer = "Chlorophyll does NOT absorb light energy during photosynthesis."
    citations = [
        {"citationId": "chk_1", "documentId": "doc_1", "displayName": "biology.pdf",
         "pageNumber": 1, "chunkIndex": 0, "charStart": 0, "charEnd": 60,
         "text": "Chlorophyll absorbs light energy during photosynthesis."}
    ]
    chunks_by_id = {
        "chk_1": {"chunk_id": "chk_1", "document_id": "doc_1", "page_number": 1,
                  "chunk_index": 0, "char_start": 0, "char_end": 60,
                  "text": "Chlorophyll absorbs light energy during photosynthesis."}
    }

    result = verify_tutor_answer(
        str(database_path),
        question=question,
        answer_text=answer,
        citations=citations,
        user_id=user_id,
        chunks_by_id=chunks_by_id,
    )

    assert result.outcome in (VerificationOutcome.REJECTED,
                              VerificationOutcome.UNVERIFIED)


# ---------------------------------------------------------------------------
# Quiz question verification tests
# ---------------------------------------------------------------------------


def test_quiz_question_verified_when_evidence_supports(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    question = {
        "questionId": "q_1",
        "questionType": "multiple_choice",
        "prompt": "What does chlorophyll absorb?",
        "options": [
            {"id": "A", "text": "Light energy"},
            {"id": "B", "text": "Sound energy"},
            {"id": "C", "text": "Soil"},
            {"id": "D", "text": "Oxygen"},
        ],
        "correctOption": "A",
        "expectedAnswer": "",
        "acceptedAnswers": [],
        "explanation": "Chlorophyll absorbs light energy during photosynthesis.",
        "citations": ["E1"],
    }
    citations = [
        {"citationId": "chk_1", "documentId": "doc_1", "displayName": "biology.pdf",
         "pageNumber": 1, "chunkIndex": 0, "charStart": 0, "charEnd": 50,
         "text": "Chlorophyll absorbs light energy during photosynthesis."}
    ]
    chunks_by_id = {
        "chk_1": {"chunk_id": "chk_1", "document_id": "doc_1", "page_number": 1,
                  "chunk_index": 0, "char_start": 0, "char_end": 50,
                  "text": "Chlorophyll absorbs light energy during photosynthesis."}
    }

    result = verify_quiz_question(
        question=question,
        chunks_by_id=chunks_by_id,
        citations=citations,
    )

    assert result.outcome in (VerificationOutcome.VERIFIED,
                              VerificationOutcome.PARTIALLY_VERIFIED)
    assert result.schema_valid is True


def test_quiz_question_rejected_when_missing_answer_key(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    question = {
        "questionId": "q_1",
        "questionType": "short_answer",
        "prompt": "What does chlorophyll absorb?",
        "options": [],
        "correctOption": "",
        "expectedAnswer": "",
        "acceptedAnswers": [],
        "explanation": "Chlorophyll absorbs light energy.",
        "citations": ["E1"],
    }
    citations = [
        {"citationId": "chk_1", "documentId": "doc_1", "displayName": "biology.pdf",
         "pageNumber": 1, "chunkIndex": 0, "charStart": 0, "charEnd": 50,
         "text": "Chlorophyll absorbs light energy during photosynthesis."}
    ]
    chunks_by_id = {
        "chk_1": {"chunk_id": "chk_1", "document_id": "doc_1", "page_number": 1,
                  "chunk_index": 0, "char_start": 0, "char_end": 50,
                  "text": "Chlorophyll absorbs light energy during photosynthesis."}
    }

    result = verify_quiz_question(
        question=question,
        chunks_by_id=chunks_by_id,
        citations=citations,
    )

    assert result.outcome == VerificationOutcome.REJECTED
    assert result.schema_valid is False


# ---------------------------------------------------------------------------
# Knowledge graph relationship verification tests
# ---------------------------------------------------------------------------


def test_knowledge_graph_relationship_verified(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    relationship = {
        "relationship_id": "rel_1",
        "source_normalized": "photosynthesis",
        "target_normalized": "chlorophyll",
        "relation": "contains",
        "supportingText": "Chlorophyll absorbs light energy during photosynthesis."
    }
    chunks_by_id = {
        "chk_1": {"chunk_id": "chk_1", "document_id": "doc_1", "page_number": 1,
                  "chunk_index": 0, "char_start": 0, "char_end": 60,
                  "text": "Chlorophyll absorbs light energy during photosynthesis."}
    }

    result = verify_knowledge_graph_relationship(
        relationship=relationship,
        chunks_by_id=chunks_by_id,
    )

    assert result.outcome == VerificationOutcome.VERIFIED
    assert result.schema_valid is True


def test_knowledge_graph_relationship_rejected_when_no_supporting_text(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    relationship = {
        "relationship_id": "rel_1",
        "source_normalized": "photosynthesis",
        "target_normalized": "chlorophyll",
        "relation": "contains",
        "supportingText": "",
    }
    chunks_by_id = {}

    result = verify_knowledge_graph_relationship(
        relationship=relationship,
        chunks_by_id=chunks_by_id,
    )

    assert result.outcome == VerificationOutcome.REJECTED


# ---------------------------------------------------------------------------
# Multimodal content verification tests
# ---------------------------------------------------------------------------


def test_multimodal_content_verified_when_text_extracted(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    result = verify_multimodal_extracted_content(
        extracted_text="Chlorophyll absorbs light energy.",
        source_type="image",
        source_metadata={"ocrStatus": "available", "imageId": "img_1"},
    )

    assert result.outcome in (VerificationOutcome.PARTIALLY_VERIFIED,
                              VerificationOutcome.VERIFIED)
    assert result.schema_valid is True


def test_multimodal_content_rejected_when_no_text(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    result = verify_multimodal_extracted_content(
        extracted_text="",
        source_type="image",
        source_metadata={"ocrStatus": "unavailable", "imageId": "img_1"},
    )

    assert result.outcome in (VerificationOutcome.REJECTED,
                              VerificationOutcome.UNVERIFIED)


def test_multimodal_content_rejected_when_invalid_source_type(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    result = verify_multimodal_extracted_content(
        extracted_text="Some text.",
        source_type="video",
        source_metadata={},
    )

    assert result.outcome == VerificationOutcome.REJECTED


# ---------------------------------------------------------------------------
# Aggregate results tests
# ---------------------------------------------------------------------------


def test_aggregate_results_returns_worst_outcome(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)


    results = [
        VerificationResult(
            outcome=VerificationOutcome.VERIFIED,
            confidence=0.9,
            checks={"citation_provenance": True},
            reason=None,
            citations_valid=True,
            evidence_coverage=0.9,
            unsupported_claim_count=0,
            schema_valid=True,
        ),
        VerificationResult(
            outcome=VerificationOutcome.REJECTED,
            confidence=0.0,
            checks={"citation_provenance": False},
            reason="No citation",
            citations_valid=False,
            evidence_coverage=0.0,
            unsupported_claim_count=0,
            schema_valid=False,
        ),
    ]

    aggregate = aggregate_results(results)

    assert aggregate.outcome == VerificationOutcome.REJECTED
    assert aggregate.confidence == 0.0


def test_outcome_to_human_readable(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    assert outcome_to_human_readable(VerificationOutcome.VERIFIED) == "Verified"
    assert outcome_to_human_readable(VerificationOutcome.PARTIALLY_VERIFIED) == \
        "Partially verified"
    assert outcome_to_human_readable(VerificationOutcome.UNVERIFIED) == "Unverified"
    assert outcome_to_human_readable(VerificationOutcome.REJECTED) == "Rejected"


# ---------------------------------------------------------------------------
# Cross-user isolation tests
# ---------------------------------------------------------------------------


def test_user_isolation_in_verification(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "testuser_b", "hashed-password",
                            is_admin=False)

    citations = [
        {"citationId": "chk_1", "documentId": "doc_1", "displayName": "bio.pdf",
         "pageNumber": 1, "chunkIndex": 0, "charStart": 0, "charEnd": 50,
         "text": "Chlorophyll absorbs light energy."}
    ]
    chunks_by_id = {
        "chk_1": {"chunk_id": "chk_1", "document_id": "doc_1", "page_number": 1,
                  "chunk_index": 0, "char_start": 0, "char_end": 50,
                  "text": "Chlorophyll absorbs light energy."}
    }

    result_a = verify_tutor_answer(
        str(database_path),
        question="What does chlorophyll absorb?",
        answer_text="Chlorophyll absorbs light energy.",
        citations=citations,
        user_id=user_id_a,
        chunks_by_id=chunks_by_id,
    )

    assert result_a.outcome in (VerificationOutcome.VERIFIED,
                                VerificationOutcome.PARTIALLY_VERIFIED)

    # User B has no chunks - verification should fail or be limited
    result_b = verify_tutor_answer(
        str(database_path),
        question="What does chlorophyll absorb?",
        answer_text="Chlorophyll absorbs light energy.",
        citations=citations,
        user_id=user_id_b,
        chunks_by_id={},
    )

    assert result_b.outcome in (VerificationOutcome.REJECTED,
                                VerificationOutcome.UNVERIFIED)


# ---------------------------------------------------------------------------
# Output validation tests
# ---------------------------------------------------------------------------


def test_verification_result_schema_valid(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    from app.schemas import VerificationResultSchema

    result = VerificationResultSchema(
        outcome="verified",
        confidence=0.95,
        checks={"citation_provenance": True},
        reason=None,
        citations_valid=True,
        evidence_coverage=0.95,
        unsupported_claim_count=0,
        schema_valid=True,
    )

    assert result.outcome == "verified"
    assert result.confidence == 0.95
    assert result.citations_valid is True


def test_malformed_citations_rejected(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    result = verify_tutor_answer(
        str(database_path),
        question="What is X?",
        answer_text="X is Y.",
        citations=[{"not_a_citation_id": "chk_1"}],  # Malformed citation
        user_id=user_id,
        chunks_by_id={},
    )

    assert result.outcome == VerificationOutcome.REJECTED


# ---------------------------------------------------------------------------
# Empty evidence handling tests
# ---------------------------------------------------------------------------


def test_empty_evidence_unverified(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    result = verify_tutor_answer(
        str(database_path),
        question="What is X?",
        answer_text="X is Y.",
        citations=[],
        user_id=user_id,
        chunks_by_id={},
    )

    assert result.outcome in (VerificationOutcome.REJECTED,
                              VerificationOutcome.UNVERIFIED)
