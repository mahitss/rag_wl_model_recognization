"""M59: AI Content Verification & Trust Engine API routes.

Endpoints for verifying AI-generated content against source evidence:

* POST   /api/v1/verify/tutor/answer     - Verify a tutor answer
* POST   /api/v1/verify/quiz/question    - Verify a quiz question
* POST   /api/v1/verify/knowledge-graph/relationship - Verify a KG relationship
* POST   /api/v1/verify/multimodal/content - Verify extracted multimodal content
* POST   /api/v1/verify/batch            - Batch verification of multiple items

All endpoints require authentication. Verification is deterministic and
user-scoped; no external model calls are made for the core checks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.v1.routes import _database_path
from app.auth import AuthUser, require_auth
from app.content_verification import (
    VerificationOutcome,
    aggregate_results,
    outcome_to_human_readable,
    verify_knowledge_graph_relationship,
    verify_multimodal_extracted_content,
    verify_quiz_question,
    verify_tutor_answer,
)
from app.schemas import (
    BatchVerificationRequest,
    BatchVerificationResponse,
    VerificationResultSchema,
    VerifyKnowledgeGraphRelationshipRequest,
    VerifyKnowledgeGraphRelationshipResponse,
    VerifyMultimodalContentRequest,
    VerifyMultimodalContentResponse,
    VerifyQuizQuestionRequest,
    VerifyQuizQuestionResponse,
    VerifyTutorAnswerRequest,
    VerifyTutorAnswerResponse,
)
from app.storage import _verify_source_chunks

router = APIRouter(prefix="/api/v1/verify", tags=["verification"])


def _chunks_lookup(
    database_path: str,
    citation_ids: list[str],
    user_id: str | None,
) -> dict[str, dict[str, Any]]:
    """Fetch chunk records for citation IDs, enforcing user ownership."""
    chunks = _verify_source_chunks(database_path, citation_ids, user_id)
    return {str(c["chunk_id"]): c for c in chunks}


@router.post(
    "/tutor/answer",
    response_model=VerifyTutorAnswerResponse,
)
async def verify_tutor_answer_route(
    request: Request,
    payload: VerifyTutorAnswerRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Verify a tutor answer against its cited evidence.

    Checks citation provenance, claim support, contradiction detection,
    and answer relevance. Returns a verification outcome (verified /
    partially_verified / unverified / rejected) with per-check details.
    """
    database_path = str(_database_path(request))
    user_id = current_user.user_id

    citations = payload.citations
    citation_ids = [
        str(c["citationId"])
        for c in citations
        if isinstance(c, dict) and c.get("citationId")
    ]

    # Fetch chunks for citation provenance verification.
    chunks_by_id = _chunks_lookup(database_path, citation_ids, user_id)

    result = verify_tutor_answer(
        database_path,
        question=payload.question,
        answer_text=payload.answer_text,
        citations=citations,
        user_id=user_id,
        chunks_by_id=chunks_by_id,
    )

    verification_id = f"val_{uuid.uuid4().hex}"
    now = datetime.now(UTC).isoformat()

    return {
        "verification_id": verification_id,
        "question": payload.question,
        "answer_text": payload.answer_text,
        "outcome": result.outcome,
        "confidence": result.confidence,
        "checks": result.checks,
        "reason": result.reason,
        "citations_valid": result.citations_valid,
        "evidence_coverage": result.evidence_coverage,
        "unsupported_claim_count": result.unsupported_claim_count,
        "schema_valid": result.schema_valid,
        "human_readable": outcome_to_human_readable(result.outcome),
        "verified_at": now,
    }


@router.post(
    "/quiz/question",
    response_model=VerifyQuizQuestionResponse,
)
async def verify_quiz_question_route(
    request: Request,
    payload: VerifyQuizQuestionRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Verify a quiz question against its cited evidence.

    Checks citation provenance, claim support (prompt, answer key,
    explanation), and schema validity (4 options for MC, correct answer
    present for short answer).
    """
    database_path = str(_database_path(request))
    user_id = current_user.user_id

    citations = payload.citations
    citation_ids = [
        str(c["citationId"])
        for c in citations
        if isinstance(c, dict) and c.get("citationId")
    ]

    chunks_by_id = _chunks_lookup(database_path, citation_ids, user_id)

    result = verify_quiz_question(
        question=payload.question,
        chunks_by_id=chunks_by_id,
        citations=citations,
    )

    verification_id = f"val_{uuid.uuid4().hex}"
    now = datetime.now(UTC).isoformat()

    return {
        "verification_id": verification_id,
        "question_type": payload.question.get("questionType", ""),
        "outcome": result.outcome,
        "confidence": result.confidence,
        "checks": result.checks,
        "reason": result.reason,
        "citations_valid": result.citations_valid,
        "evidence_coverage": result.evidence_coverage,
        "unsupported_claim_count": result.unsupported_claim_count,
        "schema_valid": result.schema_valid,
        "human_readable": outcome_to_human_readable(result.outcome),
        "verified_at": now,
    }


@router.post(
    "/knowledge-graph/relationship",
    response_model=VerifyKnowledgeGraphRelationshipResponse,
)
async def verify_knowledge_graph_relationship_route(
    request: Request,
    payload: VerifyKnowledgeGraphRelationshipRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Verify a knowledge-graph relationship against its supporting text.

    Each relationship must have a verbatim supportingText quote that exists
    in its source chunk.
    """
    database_path = str(_database_path(request))
    user_id = current_user.user_id

    chunks_by_id = _chunks_lookup(database_path, [], user_id)

    result = verify_knowledge_graph_relationship(
        relationship=payload.relationship,
        chunks_by_id=chunks_by_id,
    )

    verification_id = f"val_{uuid.uuid4().hex}"
    now = datetime.now(UTC).isoformat()

    return {
        "verification_id": verification_id,
        "relationship_id": payload.relationship.get("relationship_id", ""),
        "outcome": result.outcome,
        "confidence": result.confidence,
        "checks": result.checks,
        "reason": result.reason,
        "citations_valid": result.citations_valid,
        "evidence_coverage": result.evidence_coverage,
        "unsupported_claim_count": result.unsupported_claim_count,
        "schema_valid": result.schema_valid,
        "human_readable": outcome_to_human_readable(result.outcome),
        "verified_at": now,
    }


@router.post(
    "/multimodal/content",
    response_model=VerifyMultimodalContentResponse,
)
async def verify_multimodal_content_route(
    request: Request,
    payload: VerifyMultimodalContentRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Verify extracted multimodal content (images, PDFs, documents).

    Checks text non-emptiness, source type validity, and OCR availability.
    Does not perform factual verification (no evidence to compare against
    for raw extracted content).
    """

    result = verify_multimodal_extracted_content(
        extracted_text=payload.extracted_text,
        source_type=payload.source_type,
        source_metadata=payload.source_metadata,
    )

    verification_id = f"val_{uuid.uuid4().hex}"
    now = datetime.now(UTC).isoformat()

    return {
        "verification_id": verification_id,
        "source_type": payload.source_type,
        "outcome": result.outcome,
        "confidence": result.confidence,
        "checks": result.checks,
        "reason": result.reason,
        "citations_valid": result.citations_valid,
        "evidence_coverage": result.evidence_coverage,
        "unsupported_claim_count": result.unsupported_claim_count,
        "schema_valid": result.schema_valid,
        "human_readable": outcome_to_human_readable(result.outcome),
        "verified_at": now,
    }


@router.post(
    "/batch",
    response_model=BatchVerificationResponse,
)
async def verify_batch_route(
    request: Request,
    payload: BatchVerificationRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Batch verification of multiple content items.

    Accepts a list of verification requests, each with a ``type`` field
    indicating the verification type ("tutor", "quiz", "knowledge_graph",
    "multimodal") and the corresponding payload. Returns results for each
    item plus an overall outcome.
    """
    database_path = str(_database_path(request))
    user_id = current_user.user_id

    results: list[dict[str, Any]] = []

    for item in payload.verifications:
        vtype = item.get("type", "")

        if vtype == "tutor":
            req = VerifyTutorAnswerRequest(**item.get("payload", {}))
            citation_ids = [
                str(c["citationId"])
                for c in req.citations
                if isinstance(c, dict) and c.get("citationId")
            ]
            chunks_by_id = _chunks_lookup(database_path, citation_ids, user_id)
            result = verify_tutor_answer(
                database_path,
                question=req.question,
                answer_text=req.answer_text,
                citations=req.citations,
                user_id=user_id,
                chunks_by_id=chunks_by_id,
            )
            results.append(
                {
                    "type": "tutor",
                    "outcome": result.outcome,
                    "confidence": result.confidence,
                    "reason": result.reason,
                    "citations_valid": result.citations_valid,
                    "evidence_coverage": result.evidence_coverage,
                    "unsupported_claim_count": result.unsupported_claim_count,
                    "schema_valid": result.schema_valid,
                }
            )
        elif vtype == "quiz":
            req = VerifyQuizQuestionRequest(**item.get("payload", {}))
            result = verify_quiz_question(
                question=req.question,
                chunks_by_id={},
                citations=req.citations,
            )
            results.append(
                {
                    "type": "quiz",
                    "outcome": result.outcome,
                    "confidence": result.confidence,
                    "reason": result.reason,
                    "citations_valid": result.citations_valid,
                    "evidence_coverage": result.evidence_coverage,
                    "unsupported_claim_count": result.unsupported_claim_count,
                    "schema_valid": result.schema_valid,
                }
            )
        elif vtype == "knowledge_graph":
            req = VerifyKnowledgeGraphRelationshipRequest(**item.get("payload", {}))
            result = verify_knowledge_graph_relationship(
                relationship=req.relationship,
                chunks_by_id={},
            )
            results.append(
                {
                    "type": "knowledge_graph",
                    "outcome": result.outcome,
                    "confidence": result.confidence,
                    "reason": result.reason,
                    "citations_valid": result.citations_valid,
                    "evidence_coverage": result.evidence_coverage,
                    "unsupported_claim_count": result.unsupported_claim_count,
                    "schema_valid": result.schema_valid,
                }
            )
        elif vtype == "multimodal":
            req = VerifyMultimodalContentRequest(**item.get("payload", {}))
            result = verify_multimodal_extracted_content(
                extracted_text=req.extracted_text,
                source_type=req.source_type,
                source_metadata=req.source_metadata,
            )
            results.append(
                {
                    "type": "multimodal",
                    "outcome": result.outcome,
                    "confidence": result.confidence,
                    "reason": result.reason,
                    "citations_valid": result.citations_valid,
                    "evidence_coverage": result.evidence_coverage,
                    "unsupported_claim_count": result.unsupported_claim_count,
                    "schema_valid": result.schema_valid,
                }
            )
        else:
            results.append(
                {
                    "type": vtype or "unknown",
                    "outcome": VerificationOutcome.REJECTED,
                    "confidence": 0.0,
                    "reason": f"Unknown verification type: {vtype}",
                    "citations_valid": False,
                    "evidence_coverage": 0.0,
                    "unsupported_claim_count": 0,
                    "schema_valid": False,
                }
            )

    # Aggregate overall outcome.
    verification_results = [
        VerificationResultSchema(
            outcome=r["outcome"],
            confidence=r["confidence"],
            checks={},
            reason=r["reason"],
            citations_valid=r["citations_valid"],
            evidence_coverage=r["evidence_coverage"],
            unsupported_claim_count=r["unsupported_claim_count"],
            schema_valid=r["schema_valid"],
        )
        for r in results
    ]
    aggregate = aggregate_results(verification_results)

    total_verified = sum(1 for r in results if r["outcome"] == VerificationOutcome.VERIFIED)
    total_rejected = sum(1 for r in results if r["outcome"] == VerificationOutcome.REJECTED)

    now = datetime.now(UTC).isoformat()

    return {
        "results": [
            {
                "outcome": r["outcome"],
                "confidence": r["confidence"],
                "reason": r["reason"],
                "citations_valid": r["citations_valid"],
                "evidence_coverage": r["evidence_coverage"],
                "unsupported_claim_count": r["unsupported_claim_count"],
                "schema_valid": r["schema_valid"],
            }
            for r in results
        ],
        "overall_outcome": aggregate.outcome,
        "total_verified": total_verified,
        "total_rejected": total_rejected,
        "verified_at": now,
    }
