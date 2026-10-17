"""Safe lexical query handling and citation-preserving retrieval response assembly."""

import sqlite3
from pathlib import Path
from typing import Any

from app.config import Settings
from app.embeddings.service import (
    EmbeddingService,
    ModelUnavailable,
    embedding_config_hash,
)
from app.errors import ApiError
from app.ingestion.pdf import build_fts_query
from app.storage import search_chunks, search_semantic_chunks


def retrieve(
    database_path: Path,
    *,
    query: str,
    document_ids: list[str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    fts_query = build_fts_query(query)
    try:
        rows = search_chunks(database_path, fts_query, document_ids, limit)
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower() and "syntax" in str(exc).lower():
            raise ApiError(
                422, "INVALID_QUERY", "The search query could not be processed."
            ) from exc
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local search storage is unavailable.") from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local search storage is unavailable.") from exc

    return [
        {
            "citationId": row["chunk_id"],
            "documentId": row["document_id"],
            "displayFilename": row["display_name"],
            "pageNumber": row["page_number"],
            "chunkIndex": row["chunk_index"],
            "charStart": row["char_start"],
            "charEnd": row["char_end"],
            "text": row["text"],
            "retrieval": {"method": "fts5_bm25", "rank": index},
        }
        for index, row in enumerate(rows, start=1)
    ]


def reciprocal_rank_fusion(
    lexical: list[dict[str, Any]], semantic: list[dict[str, Any]], *, k: int
) -> list[dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}
    for method, rows in (("lexical", lexical), ("semantic", semantic)):
        for rank, row in enumerate(rows, start=1):
            chunk_id = row["citationId"]
            item = fused.setdefault(chunk_id, {**row, "_score": 0.0})
            item["_score"] += 1.0 / (k + rank)
            item[f"_{method}_rank"] = rank
            if method == "semantic":
                item["_similarity"] = row["retrieval"]["similarity"]

    results = []
    for rank, item in enumerate(
        sorted(
            fused.values(),
            key=lambda row: (
                -row["_score"],
                row["documentId"],
                row["pageNumber"],
                row["chunkIndex"],
                row["citationId"],
            ),
        ),
        start=1,
    ):
        retrieval: dict[str, Any] = {
            "method": "rrf_hybrid",
            "rank": rank,
            "rrfScore": item["_score"],
        }
        if "_lexical_rank" in item:
            retrieval["lexicalRank"] = item["_lexical_rank"]
        if "_semantic_rank" in item:
            retrieval["semanticRank"] = item["_semantic_rank"]
            retrieval["similarity"] = item["_similarity"]
        results.append(
            {
                **{key: value for key, value in item.items() if not key.startswith("_")},
                "retrieval": retrieval,
            }
        )
    return results


def retrieve_with_mode(
    database_path: Path,
    *,
    query: str,
    document_ids: list[str] | None,
    limit: int,
    mode: str,
    settings: Settings,
    embeddings: EmbeddingService,
) -> dict[str, Any]:
    if mode not in {"lexical", "semantic", "hybrid"}:
        raise ApiError(
            422,
            "INVALID_RETRIEVAL_MODE",
            "Choose lexical, semantic, or hybrid retrieval.",
        )

    lexical_limit = settings.lexical_candidate_limit
    lexical = retrieve(
        database_path,
        query=query,
        document_ids=document_ids,
        limit=lexical_limit,
    )
    if mode == "lexical":
        return {"results": lexical[:limit], "retrievalMode": "lexical", "fallbackReason": None}

    try:
        status = embeddings.status(database_path)
    except (OSError, sqlite3.Error) as exc:
        if mode == "hybrid":
            return {
                "results": lexical[:limit],
                "retrievalMode": "lexical_fallback",
                "fallbackReason": "embedding_index_unavailable",
            }
        raise ApiError(
            503, "STORAGE_UNAVAILABLE", "Local semantic storage is unavailable."
        ) from exc
    index = status["index"]
    if index["missing_chunks"] > 0:
        reason = "embedding_index_incomplete"
        if mode == "semantic":
            raise ApiError(
                409,
                "EMBEDDING_INDEX_INCOMPLETE",
                "The semantic index is missing or stale. Rebuild embeddings "
                "before semantic retrieval.",
            )
        return {
            "results": lexical[:limit],
            "retrievalMode": "lexical_fallback",
            "fallbackReason": reason,
        }

    try:
        query_vector = embeddings.query_vector(query)
        candidates = search_semantic_chunks(
            database_path,
            config_hash=embedding_config_hash(settings),
            dimensions=settings.embedding_dimensions,
            query_vector=query_vector,
            limit=settings.semantic_candidate_limit,
            document_ids=document_ids,
        )
    except ModelUnavailable as exc:
        if mode == "hybrid":
            return {
                "results": lexical[:limit],
                "retrievalMode": "lexical_fallback",
                "fallbackReason": "embedding_model_unavailable",
            }
        raise ApiError(
            503,
            "EMBEDDING_MODEL_UNAVAILABLE",
            "The local embedding model is unavailable. Install and cache the configured model.",
        ) from exc
    except ValueError as exc:
        if mode == "hybrid":
            return {
                "results": lexical[:limit],
                "retrievalMode": "lexical_fallback",
                "fallbackReason": "embedding_model_error",
            }
        raise ApiError(
            503,
            "EMBEDDING_FAILED",
            "The local embedding model failed to embed the query.",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503, "STORAGE_UNAVAILABLE", "Local semantic storage is unavailable."
        ) from exc

    semantic = []
    for rank, row in enumerate(candidates, start=1):
        if row["similarity"] < settings.semantic_min_similarity:
            continue
        semantic.append(
            {
                "citationId": row["chunk_id"],
                "documentId": row["document_id"],
                "displayFilename": row["display_name"],
                "pageNumber": row["page_number"],
                "chunkIndex": row["chunk_index"],
                "charStart": row["char_start"],
                "charEnd": row["char_end"],
                "text": row["text"],
                "retrieval": {
                    "method": "cosine_similarity",
                    "rank": rank,
                    "similarity": row["similarity"],
                },
            }
        )

    if mode == "semantic":
        return {
            "results": semantic[:limit],
            "retrievalMode": "semantic",
            "fallbackReason": None if semantic else "no_candidates_above_threshold",
        }
    if not semantic:
        return {
            "results": lexical[:limit],
            "retrievalMode": "lexical_fallback",
            "fallbackReason": "no_semantic_candidates",
        }
    fused = reciprocal_rank_fusion(lexical, semantic, k=settings.rrf_k)
    return {"results": fused[:limit], "retrievalMode": "hybrid", "fallbackReason": None}


def answer_from_evidence(question: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    if not evidence:
        return {
            "question": question,
            "status": "insufficient_evidence",
            "answerType": "abstention",
            "answer": None,
            "citations": [],
        }

    excerpts = [f"[{index}] {item['text']}" for index, item in enumerate(evidence, start=1)]
    return {
        "question": question,
        "status": "evidence_found",
        "answerType": "extractive_evidence",
        "answer": "Retrieved excerpts (verbatim; not synthesized):\n\n" + "\n\n".join(excerpts),
        "citations": evidence,
    }
