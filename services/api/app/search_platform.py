"""M78: Universal Search & Intelligent Knowledge Discovery service layer.

Provides:
* Search index lifecycle management (create jobs, track progress, mark failures)
* Search evaluation (run deterministic eval cases, compute metrics)
* AI-powered query understanding (intent detection, related topics,
  prerequisite concepts, follow-up suggestions)

All functions are user-scoped and enforce tenant isolation through the
existing storage layer. No paid APIs or external model training is used.
AI interpretations are grounded in persisted evidence and labeled as
suggestions, not proven conclusions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import Settings
from app.errors import ApiError
from app.storage import (
    create_search_evaluation_case,
    create_search_index_job,
    get_indexing_status,
    get_search_index_job,
    list_search_evaluation_cases,
    list_search_evaluation_results,
    list_search_index_jobs,
    record_search_evaluation_result,
    record_search_index_entry,
    record_search_query,
    update_search_index_job,
)
from app.tutor import TutorUnavailable, generate_grounded_answer

# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------


def create_indexing_job(
    database_path: Path,
    *,
    institution_id: str | None,
    job_type: str,
) -> dict[str, Any]:
    """Create a new indexing job."""
    job_id = create_search_index_job(
        database_path,
        institution_id=institution_id,
        job_type=job_type,
    )
    return get_search_index_job(database_path, job_id=job_id)


def get_indexing_job(
    database_path: Path,
    *,
    job_id: str,
) -> dict[str, Any] | None:
    """Get a search index job by ID."""
    return get_search_index_job(database_path, job_id=job_id)


def list_indexing_jobs(
    database_path: Path,
    *,
    institution_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List search index jobs."""
    return list_search_index_jobs(
        database_path,
        institution_id=institution_id,
        status=status,
        limit=limit,
        offset=offset,
    )


def update_indexing_job(
    database_path: Path,
    *,
    job_id: str,
    status: str | None = None,
    total_items: int | None = None,
    indexed_items: int | None = None,
    failed_items: int | None = None,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    """Update a search index job."""
    return update_search_index_job(
        database_path,
        job_id=job_id,
        status=status,
        total_items=total_items,
        indexed_items=indexed_items,
        failed_items=failed_items,
        error_message=error_message,
    )


def record_index_entry(
    database_path: Path,
    *,
    institution_id: str | None,
    user_id: str | None,
    resource_type: str,
    resource_id: str,
    content_hash: str,
    fts5_indexed: bool = False,
    semantic_indexed: bool = False,
) -> dict[str, Any] | None:
    """Record or update a search index entry."""
    return record_search_index_entry(
        database_path,
        institution_id=institution_id,
        user_id=user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        content_hash=content_hash,
        fts5_indexed=fts5_indexed,
        semantic_indexed=semantic_indexed,
    )


def get_index_entry_status(
    database_path: Path,
    *,
    resource_type: str,
    resource_id: str,
) -> dict[str, Any] | None:
    """Get the indexing status for a resource."""
    return get_indexing_status(
        database_path,
        resource_type=resource_type,
        resource_id=resource_id,
    )


# ---------------------------------------------------------------------------
# Search query logging
# ---------------------------------------------------------------------------


def log_search_query(
    database_path: Path,
    *,
    institution_id: str | None,
    user_id: str | None,
    query: str,
    result_count: int,
    resource_types: list[str],
    mode: str,
) -> str:
    """Record an anonymized search query for analytics."""
    return record_search_query(
        database_path,
        institution_id=institution_id,
        user_id=user_id,
        query=query,
        result_count=result_count,
        resource_types=resource_types,
        mode=mode,
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def create_evaluation_case(
    database_path: Path,
    *,
    institution_id: str | None,
    query: str,
    expected_result_ids: list[str],
    expected_topics: list[str],
    metadata: dict[str, Any] | None,
) -> str:
    """Create a deterministic evaluation case."""
    return create_search_evaluation_case(
        database_path,
        institution_id=institution_id,
        query=query,
        expected_result_ids=expected_result_ids,
        expected_topics=expected_topics,
        metadata=metadata,
    )


def list_evaluation_cases(
    database_path: Path,
    *,
    institution_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List search evaluation cases."""
    return list_search_evaluation_cases(
        database_path,
        institution_id=institution_id,
        limit=limit,
        offset=offset,
    )


def record_evaluation_result(
    database_path: Path,
    *,
    institution_id: str | None,
    case_id: str,
    recall_at_k: float | None,
    precision_at_k: float | None,
    mrr: float | None,
    citation_correctness: float | None,
    retrieval_latency_ms: float | None,
    total_results: int,
    relevant_results: int,
    mode: str,
) -> str:
    """Record an evaluation result."""
    return record_search_evaluation_result(
        database_path,
        institution_id=institution_id,
        case_id=case_id,
        recall_at_k=recall_at_k,
        precision_at_k=precision_at_k,
        mrr=mrr,
        citation_correctness=citation_correctness,
        retrieval_latency_ms=retrieval_latency_ms,
        total_results=total_results,
        relevant_results=relevant_results,
        mode=mode,
    )


def list_evaluation_results(
    database_path: Path,
    *,
    institution_id: str | None = None,
    case_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List search evaluation results."""

    return list_search_evaluation_results(
        database_path,
        institution_id=institution_id,
        case_id=case_id,
        limit=limit,
        offset=offset,
    )


def compute_aggregate_metrics(
    results: list[dict[str, Any]],
) -> dict[str, float | None]:
    """Compute aggregate metrics from evaluation results.

    Returns average recall@K, precision@K, MRR, citation correctness,
    and retrieval latency across all results.
    """
    if not results:
        return {
            "avgRecallAtK": None,
            "avgPrecisionAtK": None,
            "avgMRR": None,
            "avgCitationCorrectness": None,
            "avgRetrievalLatencyMs": None,
            "caseCount": 0,
        }

    recall_values = [r["recallAtK"] for r in results if r["recallAtK"] is not None]
    precision_values = [r["precisionAtK"] for r in results if r["precisionAtK"] is not None]
    mrr_values = [r["mrr"] for r in results if r["mrr"] is not None]
    citation_values = [
        r["citationCorrectness"] for r in results if r["citationCorrectness"] is not None
    ]
    latency_values = [
        r["retrievalLatencyMs"] for r in results if r["retrievalLatencyMs"] is not None
    ]

    def _avg(vals: list[float]) -> float | None:
        return sum(vals) / len(vals) if vals else None

    return {
        "avgRecallAtK": _avg(recall_values),
        "avgPrecisionAtK": _avg(precision_values),
        "avgMRR": _avg(mrr_values),
        "avgCitationCorrectness": _avg(citation_values),
        "avgRetrievalLatencyMs": _avg(latency_values),
        "caseCount": len(results),
    }


# ---------------------------------------------------------------------------
# AI-powered query understanding
# ---------------------------------------------------------------------------

_COMMON_STOP_WORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "then", "else", "when",
        "where", "why", "how", "what", "which", "who", "whom", "whose",
        "this", "that", "these", "those", "is", "are", "was", "were",
        "be", "been", "being", "have", "has", "had", "do", "does", "did",
        "will", "would", "could", "should", "may", "might", "can",
        "cannot", "for", "to", "of", "in", "on", "at", "by", "with",
        "from", "as", "into", "through", "during", "before", "after",
        "above", "below", "between", "under", "again", "further",
        "once", "here", "there", "all",
        "any", "both", "each", "few", "more", "most", "other", "some",
        "such", "no", "nor", "not", "only", "own", "same", "so", "than",
        "too", "very", "s", "t",
    }
)


def _extract_key_terms(query: str) -> list[str]:
    """Extract meaningful terms from a query (lowercase, stop-word removed)."""
    words = []
    for word in query.lower().split():
        word = word.strip(".,!?;:\"'()[]{}")
        if word and word not in _COMMON_STOP_WORDS:
            words.append(word)
    return words


def explain_query(
    database_path: Path,
    *,
    query: str,
    context_results: list[dict[str, Any]],
    settings: Settings,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Explain a search query: interpret intent, suggest related topics,
    identify prerequisite concepts, and generate follow-up suggestions.

    Grounded in actual search evidence where available; otherwise returns
    heuristics-based suggestions labeled as such.
    """
    if not query or not query.strip():
        raise ApiError(422, "INVALID_QUERY", "Query must not be empty.")

    query = query.strip()
    key_terms = _extract_key_terms(query)

    interpreted_intent = (
        f"Information seeking about: {', '.join(key_terms[:5])}"
        if key_terms
        else "General information inquiry"
    )

    related_topics: list[str] = []
    seen_topics: set[str] = set()
    for result in context_results:
        topic = result.get("topic") or result.get("topicId")
        if topic and topic not in seen_topics:
            related_topics.append(topic)
            seen_topics.add(topic)

    prerequisite_concepts: list[str] = []
    seen_concepts: set[str] = set()
    for term in key_terms:
        if len(term) > 3 and term not in seen_concepts:
            prerequisite_concepts.append(term)
            seen_concepts.add(term)

    suggested_follow_ups: list[str] = []
    for topic in related_topics[:3]:
        suggested_follow_ups.append(f"How does {topic} relate to {query[:30]}?")
        suggested_follow_ups.append(f"What are examples of {topic}?")

    grounded_answer: str | None = None
    confidence: float | None = None
    if context_results:
        try:
            grounded = generate_grounded_answer(
                question=query,
                context_items=context_results,
            )
            grounded_answer = grounded.get("answer")
            confidence = grounded.get("confidence")
        except (TutorUnavailable, Exception):
            grounded_answer = None
            confidence = None

    return {
        "query": query,
        "interpretedIntent": interpreted_intent,
        "relatedTopics": related_topics[:10],
        "prerequisiteConcepts": prerequisite_concepts[:10],
        "suggestedFollowUps": suggested_follow_ups[:10],
        "groundedAnswer": grounded_answer,
        "confidence": confidence,
    }


def suggest_related_topics(
    database_path: Path,
    *,
    query: str,
    topic_ids: list[str],
    max_results: int = 10,
) -> dict[str, Any]:
    """Suggest related topics for a query.

    Combines keyword-derived topics with any provided topic IDs.
    Does not claim semantic relatedness beyond keyword overlap.
    """
    if not query or not query.strip():
        raise ApiError(422, "INVALID_QUERY", "Query must not be empty.")

    query = query.strip()
    key_terms = _extract_key_terms(query)

    all_topics: list[str] = []
    seen: set[str] = set()
    for topic_id in topic_ids:
        if topic_id and topic_id not in seen:
            all_topics.append(topic_id)
            seen.add(topic_id)
    for term in key_terms:
        if len(term) > 3 and term not in seen:
            all_topics.append(term)
            seen.add(term)

    follow_ups: list[str] = []
    for topic in all_topics[:5]:
        follow_ups.append(f"How does {topic} relate to {query[:40]}?")
        follow_ups.append(f"What are examples of {topic}?")

    return {
        "query": query,
        "relatedTopics": all_topics[:max_results],
        "prerequisiteConcepts": [t for t in all_topics if len(t) > 3][:max_results],
        "suggestedFollowUps": follow_ups[:max_results],
    }


def compute_retrieval_metrics(
    retrieved_ids: list[str],
    relevant_ids: list[str],
    k: int = 10,
) -> dict[str, float | None]:
    """Compute retrieval metrics for a single query.

    Computes recall@K, precision@K, and MRR.
    """
    retrieved_k = retrieved_ids[:k]
    relevant_set = set(relevant_ids)

    retrieved_relevant = [rid for rid in retrieved_k if rid in relevant_set]
    precision_at_k = len(retrieved_relevant) / len(retrieved_k) if retrieved_k else 0.0

    recall_at_k = len(retrieved_relevant) / len(relevant_set) if relevant_set else 0.0

    mrr = 0.0
    for rank, rid in enumerate(retrieved_k, start=1):
        if rid in relevant_set:
            mrr = 1.0 / rank
            break

    return {
        "recallAtK": recall_at_k,
        "precisionAtK": precision_at_k,
        "mrr": mrr,
        "totalRetrieved": len(retrieved_k),
        "relevantRetrieved": len(retrieved_relevant),
        "totalRelevant": len(relevant_set),
    }