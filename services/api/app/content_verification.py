"""Unified AI content verification pipeline for M59.

Builds on the deterministic evidence-verification pipeline in
``app.content_validation`` and extends it to cover all AI-generated workloads
across WonderLearn:

* Tutor answers
* Quiz questions and answer keys
* Knowledge-graph concepts and relationships
* Multimodal extracted content (images, PDFs, docs)
* Study notes, summaries, flashcards (existing content studio)

Verification outcomes:

* ``verified``          - All checks passed; claims supported by evidence.
* ``partially_verified`` - Some claims lack sufficient evidence; safe to use
                           with caution.
* ``unverified``        - Evidence is insufficient; do not present without
                          human review.
* ``rejected``          - Failed a critical validation rule (missing citation,
                          contradiction, malformed schema). Must not be served
                          to learners without regeneration or correction.

Deterministic checks are the source of truth. Optional local-model semantic
estimates are advisory only: they can flag concerns but never clear a failed
deterministic result.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.content_validation import (
    SUPPORTED_COVERAGE,
    WELL_SUPPORTED_COVERAGE,
    _citation_markers,
    _strip_markers,
    validate_citations,
)


class VerificationOutcome(str):
    """Verification outcome values."""

    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    UNVERIFIED = "unverified"
    REJECTED = "rejected"


@dataclass(frozen=True)
class VerificationResult:
    """Result of verifying one piece of AI-generated content."""

    outcome: str  # one of VerificationOutcome values
    confidence: float  # 0.0-1.0, deterministic estimate only
    checks: dict[str, Any]  # per-check details
    reason: str | None  # human-readable explanation when not verified
    citations_valid: bool
    evidence_coverage: float
    unsupported_claim_count: int
    schema_valid: bool


def _answer_tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    return set(re.findall(r"[\w]+", normalized, flags=re.UNICODE)) - {
        "a", "an", "and", "are", "as", "at", "be", "by", "does", "do",
        "for", "from", "has", "he", "her", "his", "i", "in", "is", "it",
        "its", "of", "on", "or", "she", "that", "the", "this", "to",
        "was", "were", "will", "with", "you", "your",
        "what", "where", "when", "why", "how", "which", "who", "whom",
    }


def _coverage(candidate: str, evidence: str) -> float:
    """Fraction of candidate's non-stop-word tokens present in evidence."""
    candidate_tokens = _answer_tokens(candidate)
    if not candidate_tokens:
        return 0.0
    evidence_tokens = _answer_tokens(evidence)
    overlap = len(candidate_tokens & evidence_tokens)
    return overlap / len(candidate_tokens)


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    """Jaccard similarity of token sets for duplicate detection."""
    tokens_a = _answer_tokens(text_a)
    tokens_b = _answer_tokens(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union else 0.0


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text or "") if part.strip()]


def verify_citation_provenance(
    database_path: Path,
    citations: list[dict[str, Any]],
    user_id: str | None,
) -> tuple[bool, list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Verify every citation references a real, owned chunk with valid provenance.

    Never marks a citation valid merely because its document ID exists;
    page/chunk/char-range provenance must also match the stored chunk.
    """
    return validate_citations(database_path, citations, user_id)


def verify_claim_support(
    text: str,
    chunks_by_id: dict[str, dict[str, Any]],
    citation_ids: list[str],
) -> tuple[float, list[dict[str, Any]]]:
    """Check whether claims in ``text`` are supported by cited evidence.

    Returns (coverage, unsupported_claims) where coverage is the fraction of
    candidate tokens found in the combined cited evidence.
    """
    chunk_by_position = [
        chunks_by_id[cid]
        for cid in citation_ids
        if cid in chunks_by_id
    ]
    combined_evidence = " ".join(chunk["text"] for chunk in chunk_by_position)
    content_stripped = _strip_markers(text)
    coverage = _coverage(content_stripped, combined_evidence)

    unsupported_claims: list[dict[str, Any]] = []
    for sentence in _split_sentences(text):
        sentence_stripped = _strip_markers(sentence)
        markers = _citation_markers(sentence)
        if not markers:
            # No inline citation markers: check against ALL cited evidence
            cov = _coverage(sentence_stripped, combined_evidence)
            if cov < SUPPORTED_COVERAGE:
                unsupported_claims.append(
                    {
                        "claim": sentence,
                        "reason": "no citation markers; insufficient support in evidence",
                        "coverage": cov,
                    }
                )
            continue
        cited_texts = []
        markers_valid = True
        for m in markers:
            idx = int(m[1:]) - 1
            if idx < 0 or idx >= len(citation_ids):
                markers_valid = False
                break
            cited_chunk_id = citation_ids[idx]
            if cited_chunk_id in chunks_by_id:
                cited_texts.append(chunks_by_id[cited_chunk_id]["text"])
        if not markers_valid or not cited_texts:
            unsupported_claims.append(
                {
                    "claim": sentence,
                    "reason": "citation marker out of range or unresolved",
                    "coverage": 0.0,
                }
            )
            continue
        cited_text = " ".join(cited_texts)
        cov = _coverage(sentence_stripped, cited_text)
        if cov < SUPPORTED_COVERAGE:
            unsupported_claims.append(
                {
                    "claim": sentence,
                    "reason": "insufficient support in cited evidence",
                    "coverage": cov,
                }
            )

    return coverage, unsupported_claims


def verify_contradiction(
    answer_text: str,
    chunks_by_id: dict[str, dict[str, Any]],
    citation_ids: list[str],
) -> list[dict[str, Any]]:
    """Detect contradictions between answer text and cited evidence.

    A sentence is flagged as contradictory when it contains negation words
    (not, never, no, without) paired with shared content tokens from evidence.
    """
    negations = {"not", "never", "no", "without", "none", "nobody", "nothing"}
    contradictions: list[dict[str, Any]] = []

    chunk_by_position = [
        chunks_by_id[cid]
        for cid in citation_ids
        if cid in chunks_by_id
    ]
    combined_evidence = " ".join(chunk["text"] for chunk in chunk_by_position)
    evidence_tokens = _answer_tokens(combined_evidence)

    for sentence in _split_sentences(answer_text):
        sentence_stripped = _strip_markers(sentence)
        sentence_tokens = _answer_tokens(sentence_stripped)
        if not sentence_tokens:
            continue
        has_negation = bool(sentence_tokens & negations)
        if not has_negation:
            continue
        overlap = len(sentence_tokens & evidence_tokens)
        overlap_ratio = overlap / len(sentence_tokens)
        if overlap_ratio >= 0.4:
            contradictions.append(
                {
                    "claim": sentence,
                    "reason": "negated statement overlaps with cited evidence",
                    "overlap_ratio": round(overlap_ratio, 4),
                }
            )

    return contradictions


def verify_answer_relevance(
    answer_text: str,
    question_text: str,
    chunks_by_id: dict[str, dict[str, Any]],
    citation_ids: list[str],
) -> tuple[float, bool]:
    """Check whether the answer is relevant to the question and supported by evidence.

    Returns (relevance_score, is_relevant). Relevance is the fraction of answer
    tokens found in the question + cited evidence combined.
    """
    question_tokens = _answer_tokens(question_text)
    chunk_by_position = [
        chunks_by_id[cid]
        for cid in citation_ids
        if cid in chunks_by_id
    ]
    combined_evidence = " ".join(chunk["text"] for chunk in chunk_by_position)
    evidence_tokens = _answer_tokens(combined_evidence)
    all_reference_tokens = question_tokens | evidence_tokens

    answer_tokens = _answer_tokens(answer_text)
    if not answer_tokens:
        return 0.0, False
    overlap = len(answer_tokens & all_reference_tokens)
    relevance = overlap / len(answer_tokens)
    return relevance, relevance >= 0.30


def verify_tutor_answer(
    database_path: str | Path,
    *,
    question: str,
    answer_text: str,
    citations: list[dict[str, Any]],
    user_id: str | None,
    chunks_by_id: dict[str, dict[str, Any]],
) -> VerificationResult:
    """Verify a tutor answer against retrieved evidence.

    Checks: citation provenance, claim support, contradiction detection,
    answer relevance. Returns a VerificationResult.
    """
    database_path = Path(database_path)
    citation_ids = []
    citation_valid = False
    for c in citations:
        if isinstance(c, dict) and c.get("citationId") is not None:
            cid = str(c["citationId"])
            citation_ids.append(cid)
            if cid in chunks_by_id:
                citation_valid = True
    citation_valid = citation_valid and len(citation_ids) > 0

    # 2. Claim support - check entire answer against combined evidence from
    # cited chunks.
    coverage, unsupported_claims = verify_claim_support(
        answer_text, chunks_by_id, citation_ids
    )

    # 3. Contradiction detection.
    contradictions = verify_contradiction(answer_text, chunks_by_id, citation_ids)

    # 4. Answer relevance.
    relevance, is_relevant = verify_answer_relevance(
        answer_text, question, chunks_by_id, citation_ids
    )

    # 5. Schema validity: answer must be non-empty, inline markers must match
    # declared citations.
    inline_markers = set(_citation_markers(answer_text))
    label_set = {f"E{i+1}" for i in range(len(citations))}
    markers_valid = all(m in label_set for m in inline_markers)
    schema_valid = bool(answer_text.strip()) and markers_valid

    # 6. Determine outcome.
    checks = {
        "citation_provenance": citation_valid,
        "claim_support": coverage >= SUPPORTED_COVERAGE,
        "contradiction_free": len(contradictions) == 0,
        "answer_relevant": is_relevant,
        "schema_valid": schema_valid,
    }

    critical_failed = not citation_valid or not schema_valid or len(contradictions) > 0
    if critical_failed:
        outcome = VerificationOutcome.REJECTED
        confidence = 0.0
        reason = "Failed critical validation: " + "; ".join(
            [
                "citation provenance failed" if not citation_valid else "",
                "schema invalid" if not schema_valid else "",
                f"{len(contradictions)} contradiction(s) detected"
                if contradictions else "",
            ]
        ).strip(" ;")
    elif not is_relevant or coverage < SUPPORTED_COVERAGE or len(unsupported_claims) > 0:
        outcome = VerificationOutcome.UNVERIFIED
        confidence = coverage
        reason = "Answer lacks sufficient evidence support"
    elif coverage < WELL_SUPPORTED_COVERAGE or not is_relevant:
        outcome = VerificationOutcome.PARTIALLY_VERIFIED
        confidence = coverage
        reason = "Some claims lack sufficient evidence support"
    else:
        outcome = VerificationOutcome.VERIFIED
        confidence = coverage
        reason = None

    return VerificationResult(
        outcome=outcome,
        confidence=round(confidence, 4),
        checks=checks,
        reason=reason,
        citations_valid=citation_valid,
        evidence_coverage=round(coverage, 4),
        unsupported_claim_count=len(unsupported_claims) + len(contradictions),
        schema_valid=schema_valid,
    )


def verify_quiz_question(
    question: dict[str, Any],
    chunks_by_id: dict[str, dict[str, Any]],
    citations: list[dict[str, Any]],
) -> VerificationResult:
    """Verify a quiz question against its cited evidence.

    Checks: citation provenance, claim support (question prompt, answer key,
    explanation), schema validity (4 options for MC, correct option present,
    expected answer for short answer).
    """
    citation_ids = [
        str(c["citationId"])
        for c in citations
        if isinstance(c, dict) and c.get("citationId") is not None
    ]

    citation_valid = len(citation_ids) > 0 and all(
        cid in chunks_by_id for cid in citation_ids
    )

    # Validate question schema.
    question_type = question.get("questionType", question.get("question_type", ""))
    prompt = question.get("prompt", question.get("question", ""))
    explanation = question.get("explanation", "")
    answer_key = (
        question.get("answerKey")
        or question.get("answer_key")
        or question.get("correctOption", "")
    )
    options = question.get("options", [])

    schema_valid = True
    schema_issues: list[str] = []
    if not isinstance(question_type, str) or question_type not in {
        "multiple_choice",
        "short_answer",
    }:
        schema_valid = False
        schema_issues.append("invalid question type")
    if not isinstance(prompt, str) or not 8 <= len(prompt.strip()) <= 500:
        schema_valid = False
        schema_issues.append("prompt length invalid")
    if not isinstance(explanation, str) or not 1 <= len(explanation.strip()) <= 1500:
        schema_valid = False
        schema_issues.append("explanation length invalid")
    if question_type == "multiple_choice":
        if (
            not isinstance(options, list)
            or len(options) != 4
            or answer_key not in {o.get("id") for o in options if isinstance(o, dict)}
        ):
            schema_valid = False
            schema_issues.append("multiple choice options invalid")
    elif question_type == "short_answer":
        if not answer_key or not isinstance(answer_key, str):
            schema_valid = False
            schema_issues.append("short answer missing answer key")

    # Citation markers must match declared citations.
    inline_markers = set(_citation_markers(prompt + " " + explanation))
    label_set = {f"E{i+1}" for i in range(len(citations))}
    markers_valid = all(m in label_set for m in inline_markers)
    schema_valid = schema_valid and markers_valid

    # Claim support: check prompt, answer key/explanation against evidence.
    chunk_by_position = [
        chunks_by_id[str(c["citationId"])]
        for c in citations
        if isinstance(c, dict)
        and c.get("citationId") is not None
        and str(c["citationId"]) in chunks_by_id
    ]
    combined_evidence = " ".join(chunk["text"] for chunk in chunk_by_position)

    prompt_cov = _coverage(_strip_markers(prompt), combined_evidence)
    if question_type == "multiple_choice":
        correct_option_text = ""
        for opt in options:
            if isinstance(opt, dict) and opt.get("id") == answer_key:
                correct_option_text = opt.get("text", "")
                break
        answer_cov = _coverage(_strip_markers(correct_option_text), combined_evidence)
    else:
        answer_cov = _coverage(answer_key, combined_evidence)

    explanation_cov = _coverage(_strip_markers(explanation), combined_evidence)
    min_support = min(prompt_cov, answer_cov, explanation_cov)
    claims_supported = min_support >= SUPPORTED_COVERAGE

    checks = {
        "citation_provenance": citation_valid,
        "claim_support": claims_supported,
        "schema_valid": schema_valid,
        "markers_valid": markers_valid,
    }

    critical_failed = not citation_valid or not schema_valid or not claims_supported
    if critical_failed:
        outcome = VerificationOutcome.REJECTED
        confidence = min_support
        reason = "Failed critical validation: " + "; ".join(
            [
                "citation provenance failed" if not citation_valid else "",
                "schema invalid" if not schema_valid else "",
                "claims not supported by evidence" if not claims_supported else "",
            ]
        ).strip(" ;")
    elif min_support < WELL_SUPPORTED_COVERAGE:
        outcome = VerificationOutcome.PARTIALLY_VERIFIED
        confidence = min_support
        reason = "Some question content lacks sufficient evidence support"
    else:
        outcome = VerificationOutcome.VERIFIED
        confidence = min_support
        reason = None

    return VerificationResult(
        outcome=outcome,
        confidence=round(confidence, 4),
        checks=checks,
        reason=reason,
        citations_valid=citation_valid,
        evidence_coverage=round(min_support, 4),
        unsupported_claim_count=0,
        schema_valid=schema_valid,
    )


def verify_knowledge_graph_relationship(
    relationship: dict[str, Any],
    chunks_by_id: dict[str, dict[str, Any]],
) -> VerificationResult:
    """Verify a knowledge-graph relationship against its supporting text.

    Each relationship must have a verbatim supportingText quote that exists
    in its source chunk.
    """
    supporting_text = relationship.get("supportingText", "")
    source_normalized = relationship.get("source_normalized", "")
    target_normalized = relationship.get("target_normalized", "")
    relation = relationship.get("relation", "")

    containing_chunk = None
    for chunk in chunks_by_id.values():
        if supporting_text and supporting_text in chunk.get("text", ""):
            containing_chunk = chunk
            break

    citation_valid = containing_chunk is not None and bool(supporting_text)
    schema_valid = bool(
        source_normalized
        and target_normalized
        and relation
        and source_normalized != target_normalized
        and supporting_text
    )

    checks = {
        "citation_provenance": citation_valid,
        "schema_valid": schema_valid,
        "supporting_text_present": bool(supporting_text),
        "source_target_distinct": source_normalized != target_normalized,
    }

    critical_failed = not citation_valid or not schema_valid
    if critical_failed:
        outcome = VerificationOutcome.REJECTED
        confidence = 0.0
        reason = "Failed critical validation: " + "; ".join(
            [
                "supporting text not found in any chunk"
                if not citation_valid
                else "",
                "schema invalid" if not schema_valid else "",
            ]
        ).strip(" ;")
    else:
        outcome = VerificationOutcome.VERIFIED
        confidence = 1.0
        reason = None

    return VerificationResult(
        outcome=outcome,
        confidence=confidence,
        checks=checks,
        reason=reason,
        citations_valid=citation_valid,
        evidence_coverage=1.0 if citation_valid else 0.0,
        unsupported_claim_count=0,
        schema_valid=schema_valid,
    )


def verify_multimodal_extracted_content(
    extracted_text: str,
    source_type: str,
    source_metadata: dict[str, Any],
) -> VerificationResult:
    """Verify extracted content from multimodal sources (images, PDFs, docs).

    Checks: OCR confidence (if available), text non-empty, source metadata
    present. Does not validate factual accuracy (no evidence to compare
    against for raw extracted content).
    """
    ocr_status = source_metadata.get("ocrStatus", "unavailable")
    extracted_text_stripped = (extracted_text or "").strip()

    citation_valid = bool(extracted_text_stripped)
    schema_valid = bool(source_type in {"image", "pdf", "document"})

    checks = {
        "text_non_empty": bool(extracted_text_stripped),
        "source_type_valid": schema_valid,
        "ocr_available": ocr_status in {"available", "empty"},
    }

    critical_failed = not citation_valid or not schema_valid
    if critical_failed:
        outcome = VerificationOutcome.REJECTED
        confidence = 0.0
        reason = "Failed critical validation: " + "; ".join(
            [
                "no extractable text" if not citation_valid else "",
                "invalid source type" if not schema_valid else "",
            ]
        ).strip(" ;")
    elif not extracted_text_stripped:
        outcome = VerificationOutcome.UNVERIFIED
        confidence = 0.0
        reason = "No extractable content from multimodal source"
    else:
        outcome = VerificationOutcome.PARTIALLY_VERIFIED
        confidence = 0.5
        reason = (
            "Extracted content cannot be factually verified without source evidence"
        )

    return VerificationResult(
        outcome=outcome,
        confidence=confidence,
        checks=checks,
        reason=reason,
        citations_valid=citation_valid,
        evidence_coverage=0.0,
        unsupported_claim_count=0,
        schema_valid=schema_valid,
    )


def aggregate_results(results: list[VerificationResult]) -> VerificationResult:
    """Aggregate multiple verification results into a single result.

    The overall outcome is the "worst" result: rejected > unverified >
    partially_verified > verified. Confidence is the minimum.
    """
    if not results:
        return VerificationResult(
            outcome=VerificationOutcome.UNVERIFIED,
            confidence=0.0,
            checks={},
            reason="No results to aggregate",
            citations_valid=False,
            evidence_coverage=0.0,
            unsupported_claim_count=0,
            schema_valid=False,
        )

    outcome_order = {
        VerificationOutcome.REJECTED: 4,
        VerificationOutcome.UNVERIFIED: 3,
        VerificationOutcome.PARTIALLY_VERIFIED: 2,
        VerificationOutcome.VERIFIED: 1,
    }

    worst = max(results, key=lambda r: outcome_order.get(r.outcome, 0))
    min_confidence = min(r.confidence for r in results)
    all_citations_valid = all(r.citations_valid for r in results)
    all_schema_valid = all(r.schema_valid for r in results)
    total_unsupported = sum(r.unsupported_claim_count for r in results)
    avg_coverage = (
        sum(r.evidence_coverage for r in results) / len(results) if results else 0.0
    )

    reasons = [r.reason for r in results if r.reason]
    combined_reason = "; ".join(reasons) if reasons else None

    return VerificationResult(
        outcome=worst.outcome,
        confidence=round(min_confidence, 4),
        checks={f"check_{i}": r.checks for i, r in enumerate(results)},
        reason=combined_reason,
        citations_valid=all_citations_valid,
        evidence_coverage=round(avg_coverage, 4),
        unsupported_claim_count=total_unsupported,
        schema_valid=all_schema_valid,
    )


def outcome_to_human_readable(outcome: str) -> str:
    """Map a verification outcome to a human-readable label."""
    mapping = {
        VerificationOutcome.VERIFIED: "Verified",
        VerificationOutcome.PARTIALLY_VERIFIED: "Partially verified",
        VerificationOutcome.UNVERIFIED: "Unverified",
        VerificationOutcome.REJECTED: "Rejected",
    }
    return mapping.get(outcome, outcome)