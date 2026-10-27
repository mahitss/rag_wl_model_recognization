"""Deterministic evidence verification for AI-generated content resources.

Provides citation provenance validation, claim-support checking against
retrieved evidence, and an optional local-model semantic estimate that is
always labelled as an estimate, never as proof.

Deterministic checks are the source of truth for a section's verification
status (verified / needs_review / insufficient_evidence). The optional local
model is advisory only: it can flag concerns but cannot clear them. Human
review (via the verify endpoint) is authoritative and overrides both.
"""

from __future__ import annotations

import json
import re
import secrets
import unicodedata
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.errors import ApiError
from app.storage import _verify_source_chunks, connect_database, get_content_resource

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "what",
    "which",
    "with",
}

# A claim counts as supported when at least this share of its non-stop-word
# tokens appear in the cited evidence text.
SUPPORTED_COVERAGE = 0.35
# A section counts as well supported overall at this coverage.
WELL_SUPPORTED_COVERAGE = 0.65

SECTION_TYPES = {"heading", "body", "flashcard", "question", "summary"}


class VerificationError(ApiError):
    """Raised when content validation cannot run safely."""


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    return set(re.findall(r"[\w]+", normalized, flags=re.UNICODE)) - _STOP_WORDS


def _coverage(candidate: str, evidence: str) -> float:
    """Fraction of candidate's non-stop-word tokens present in evidence."""
    candidate_tokens = _tokens(candidate)
    if not candidate_tokens:
        return 0.0
    evidence_tokens = _tokens(evidence)
    overlap = len(candidate_tokens & evidence_tokens)
    return overlap / len(candidate_tokens)


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text or "") if part.strip()]


def _strip_markers(text: str) -> str:
    return re.sub(r"\[E\d+\]", "", text or "")


def _citation_markers(text: str) -> list[str]:
    return re.findall(r"\[(E\d+)\]", text or "")


def validate_citations(
    database_path: Path,
    citations: list[dict[str, Any]],
    user_id: str | None,
) -> tuple[bool, list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Validate that every citation references a real, owned chunk.

    Also verifies page/chunk/char-range provenance matches the stored chunk.
    Returns (all_valid, invalid_details, chunks_by_id).
    """
    invalid: list[dict[str, Any]] = []
    chunks_by_id: dict[str, dict[str, Any]] = {}
    citation_ids: list[str] = [
        str(c["citationId"])
        for c in citations
        if isinstance(c, dict) and c.get("citationId") is not None
    ]
    if not citation_ids:
        return True, invalid, chunks_by_id
    chunks = _verify_source_chunks(database_path, citation_ids, user_id)
    for chunk in chunks:
        chunks_by_id[chunk["chunk_id"]] = chunk
    for citation in citations:
        if not isinstance(citation, dict):
            invalid.append({"citationId": None, "reason": "citation is not an object"})
            continue
        citation_id = citation.get("citationId")
        if citation_id is None:
            invalid.append({"citationId": None, "reason": "missing citationId"})
            continue
        citation_id_str = str(citation_id)
        stored_chunk: dict[str, Any] | None = chunks_by_id.get(citation_id_str)
        if stored_chunk is None:
            invalid.append(
                {
                    "citationId": citation_id_str,
                    "reason": "chunk not found or not owned by this user",
                }
            )
            continue
        problems: list[str] = []
        # Citation dicts use camelCase; stored chunks use snake_case.
        if (
            stored_chunk["page_number"] != citation.get("pageNumber")
            or stored_chunk["chunk_index"] != citation.get("chunkIndex")
            or stored_chunk["char_start"] != citation.get("charStart")
            or stored_chunk["char_end"] != citation.get("charEnd")
        ):
            problems.append("page/chunk/char-range provenance mismatch")
        if stored_chunk["text"].strip() != (citation.get("text") or "").strip():
            problems.append("citation text does not match stored chunk text")
        if problems:
            invalid.append({"citationId": citation_id_str, "reason": "; ".join(problems)})
    return (len(invalid) == 0, invalid, chunks_by_id)


def validate_section(
    section: dict[str, Any], chunks_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Validate one section's claims against its cited evidence.

    Pure function: does not touch the database. Returns a dict with
    citation_validity, evidence_coverage, schema_validity,
    unsupported_claim_count, question_answer_correctness, and per-claim
    support details.
    """
    citations = section.get("citations") or []
    content = section.get("content") or ""
    section_type = section.get("sectionType")

    # ---- Schema validity ----
    citation_ids: list[str] = [
        str(c["citationId"])
        for c in citations
        if isinstance(c, dict) and c.get("citationId") is not None
    ]
    schema_validity = bool(
        isinstance(section_type, str)
        and section_type in SECTION_TYPES
        and isinstance(section.get("sectionTitle"), str)
        and section["sectionTitle"].strip()
        and isinstance(content, str)
        and content.strip()
        and isinstance(citations, list)
        and len(citations) == len({str(cid) for cid in citation_ids if cid})
        and all(isinstance(c, dict) and c.get("citationId") for c in citations)
    )
    # Every citation marker [E1] refers to a citation by 1-based position.
    all_markers = [m for s in _split_sentences(content) for m in _citation_markers(s)]
    markers_in_range = all(1 <= int(m[1:]) <= len(citations) for m in all_markers)
    schema_validity = schema_validity and markers_in_range

    # ---- Citation validity (provenance) ----
    citation_validity = all(cid in chunks_by_id for cid in citation_ids if cid)

    # ---- Evidence coverage & claim support ----
    # E-labels are 1-based positions into the section's citation list.
    chunk_by_position = [
        chunks_by_id[str(c["citationId"])]
        for c in citations
        if isinstance(c, dict)
        and c.get("citationId") is not None
        and str(c["citationId"]) in chunks_by_id
    ]
    combined_evidence = " ".join(chunk["text"] for chunk in chunk_by_position)
    content_stripped = _strip_markers(content)
    content_coverage = _coverage(content_stripped, combined_evidence)

    unsupported_claims: list[dict[str, Any]] = []
    for sentence in _split_sentences(content):
        sentence_stripped = _strip_markers(sentence)
        markers = _citation_markers(sentence)
        if not markers:
            # A sentence with no citation is an unsupported claim.
            unsupported_claims.append(
                {
                    "claim": sentence,
                    "reason": "no citation",
                    "coverage": 0.0,
                }
            )
            continue
        # Resolve E-labels to the cited chunks (1-based positions).
        cited_texts_for_sentence = []
        markers_valid = True
        for m in markers:
            idx = int(m[1:]) - 1
            if idx < 0 or idx >= len(citations):
                markers_valid = False
                break
            cited_chunk_id = citations[idx].get("citationId")
            if cited_chunk_id in chunks_by_id:
                cited_texts_for_sentence.append(chunks_by_id[cited_chunk_id]["text"])
        if not markers_valid or not cited_texts_for_sentence:
            unsupported_claims.append(
                {
                    "claim": sentence,
                    "reason": "citation marker out of range or unresolved",
                    "coverage": 0.0,
                }
            )
            continue
        cited_text = " ".join(cited_texts_for_sentence)
        cov = _coverage(sentence_stripped, cited_text)
        if cov < SUPPORTED_COVERAGE:
            unsupported_claims.append(
                {
                    "claim": sentence,
                    "reason": "insufficient support in cited evidence",
                    "coverage": cov,
                }
            )

    # Question-answer correctness: for question sections, does the answer
    # (content) have support in the cited evidence?
    question_answer_correctness: float | None = None
    if section_type == "question":
        question_answer_correctness = content_coverage

    return {
        "citation_validity": citation_validity,
        "schema_validity": schema_validity,
        "evidence_coverage": round(content_coverage, 4),
        "unsupported_claim_count": len(unsupported_claims),
        "unsupported_claims": unsupported_claims,
        "question_answer_correctness": (
            round(question_answer_correctness, 4)
            if question_answer_correctness is not None
            else None
        ),
    }


def _verdict(
    citation_validity: bool,
    schema_validity: bool,
    evidence_coverage: float,
    unsupported_claim_count: int,
    question_answer_correctness: float | None,
) -> str:
    """Deterministic verification status. Semantic estimate is advisory only."""
    if not citation_validity or not schema_validity:
        return "insufficient_evidence"
    if unsupported_claim_count > 0 or evidence_coverage < WELL_SUPPORTED_COVERAGE:
        return "needs_review"
    if (
        question_answer_correctness is not None
        and question_answer_correctness < WELL_SUPPORTED_COVERAGE
    ):
        return "needs_review"
    return "verified"


def validate_resource(
    database_path: Path,
    *,
    resource_id: str,
    user_id: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Run the verification pipeline over a content resource.

    Runs deterministic checks first. If a local tutor is configured, an
    additional semantic-estimate pass is recorded as advisory only (it can
    flag concerns but never upgrade a deterministic result).

    Stores a content_resource_validations row and updates each section's
    verification_status / verification_method / verified_at / verified_by.
    Human review (PATCH .../verify) is authoritative and overrides this.

    Returns the validation record.
    """
    resource = get_content_resource(database_path, resource_id=resource_id, user_id=user_id)
    if resource is None:
        raise ApiError(
            404,
            "CONTENT_RESOURCE_NOT_FOUND",
            "Content resource not found or access denied.",
        )

    sections = resource.get("sections") or []
    # Build chunks_by_id from every cited chunk across all sections,
    # ownership-checked.
    citations = [
        c
        for section in sections
        for c in (section.get("citations") or [])
        if isinstance(c, dict) and c.get("citationId")
    ]
    citation_validity, invalid_citations, chunks_by_id = validate_citations(
        database_path, citations, user_id
    )

    section_results: list[dict[str, Any]] = []
    for section in sections:
        section_result = validate_section(section, chunks_by_id)
        section_results.append(
            {
                "sectionId": section.get("sectionId"),
                **section_result,
            }
        )

    # Aggregate.
    total_sections = len(section_results)
    evidence_coverage = (
        round(sum(s["evidence_coverage"] for s in section_results) / total_sections, 4)
        if total_sections
        else 0.0
    )
    unsupported_claim_count = sum(s["unsupported_claim_count"] for s in section_results)
    qa_values = [
        s["question_answer_correctness"]
        for s in section_results
        if s["question_answer_correctness"] is not None
    ]
    question_answer_correctness = (
        round(sum(qa_values) / len(qa_values), 4) if qa_values else None
    )
    schema_validity = all(
        s["schema_validity"] for s in section_results
    ) if section_results else True

    # ---- Optional local-model semantic estimate (advisory only) ----
    semantic_note: str | None = None
    semantic_downgrade = False
    if settings is not None and getattr(settings, "tutor_configured", False):
        try:
            from app.tutor import local_chat_completion

            for section in sections:
                if not section.get("content"):
                    continue
                cited_text = " ".join(
                    chunks_by_id[str(c["citationId"])]["text"]
                    for c in (section.get("citations") or [])
                    if isinstance(c, dict)
                    and c.get("citationId") is not None
                    and str(c["citationId"]) in chunks_by_id
                )
                if not cited_text:
                    continue
                try:
                    raw = local_chat_completion(
                        settings,
                        system_prompt="You are a careful fact-checker. Answer with one word only.",
                        user_payload={
                            "claim": section["content"],
                            "evidence": cited_text,
                        },
                    )
                    verdict = raw.strip().lower()
                    if "unsupported" in verdict:
                        semantic_downgrade = True
                        semantic_note = (
                            f"Local model (estimate) flagged section "
                            f"{section.get('sectionId')} as unsupported."
                        )
                        break
                except Exception:
                    # Local model error is advisory; ignore and continue.
                    semantic_note = "Local model semantic check unavailable; estimate skipped."
        except Exception:
            semantic_note = "Local model semantic check unavailable; estimate skipped."

    # Deterministic status (source of truth).
    status = _verdict(
        citation_validity,
        schema_validity,
        evidence_coverage,
        unsupported_claim_count,
        question_answer_correctness,
    )
    # Semantic estimate can only downgrade verified -> needs_review (conservative).
    if (
        status == "verified"
        and semantic_downgrade
        and settings is not None
        and getattr(settings, "tutor_configured", False)
    ):
        status = "needs_review"
        if semantic_note:
            semantic_note = (
                (semantic_note + " " if semantic_note else "")
                + "Deterministic result was verified; semantic estimate downgraded to needs_review."
            )

    validation_id = f"val_{secrets.token_urlsafe(12)}"
    now = datetime.now(UTC).isoformat()
    details: dict[str, Any] = {
        "invalid_citations": invalid_citations,
        "section_results": section_results,
    }
    if semantic_note:
        details["semantic_estimate"] = semantic_note

    verification_method = "deterministic"
    if settings is not None and getattr(settings, "tutor_configured", False):
        verification_method = "deterministic_semantic_estimate"

    validation_record = {
        "validationId": validation_id,
        "resourceId": resource_id,
        "userId": user_id,
        "citationValidity": citation_validity,
        "evidenceCoverage": evidence_coverage,
        "schemaValidity": schema_validity,
        "unsupportedClaimCount": unsupported_claim_count,
        "questionAnswerCorrectness": question_answer_correctness,
        "verificationMethod": verification_method,
        "details": details,
        "verifiedBy": None,
        "verifiedAt": now,
        "createdAt": now,
    }

    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO content_resource_validations(
                    validation_id, resource_id, user_id, citation_validity,
                    evidence_coverage, schema_validity, unsupported_claim_count,
                    question_answer_correctness, verification_method,
                    details_json, verified_by, verified_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    validation_id,
                    resource_id,
                    user_id,
                    int(citation_validity),
                    evidence_coverage,
                    int(schema_validity),
                    unsupported_claim_count,
                    question_answer_correctness,
                    verification_method,
                    json.dumps(details, separators=(",", ":"), sort_keys=True),
                    None,
                    now,
                    now,
                ),
            )
            # Persist per-section verification status.
            for section_result in section_results:
                section_id = section_result["sectionId"]
                section_status = _verdict(
                    section_result["citation_validity"],
                    section_result["schema_validity"],
                    section_result["evidence_coverage"],
                    section_result["unsupported_claim_count"],
                    section_result["question_answer_correctness"],
                )
                section_citation_ids = [
                    c.get("citationId")
                    for c in (
                        next(
                            (
                                s
                                for s in sections
                                if s.get("sectionId") == section_id
                            ),
                            None,
                        )
                        or {}
                    ).get("citations", [])
                    if isinstance(c, dict)
                ]
                section_details = {
                    "invalid_citations": [
                        c
                        for c in invalid_citations
                        if c.get("citationId") in section_citation_ids
                    ],
                    "unsupported_claims": section_result["unsupported_claims"],
                }
                if semantic_note:
                    section_details["semantic_estimate"] = semantic_note
                connection.execute(
                    """
                    UPDATE content_resource_sections
                    SET verification_status = ?,
                        verification_method = 'deterministic',
                        verification_details_json = ?,
                        verified_at = ?,
                        verified_by = NULL
                    WHERE section_id = ? AND resource_id = ?
                    """,
                    (
                        section_status,
                        json.dumps(section_details, separators=(",", ":"), sort_keys=True),
                        now,
                        section_id,
                        resource_id,
                    ),
                )

    validation_record["sections"] = section_results
    validation_record["status"] = status
    return validation_record