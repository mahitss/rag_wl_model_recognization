"""Safe lexical query handling and citation-preserving retrieval response assembly."""

import sqlite3
from pathlib import Path
from typing import Any

from app.errors import ApiError
from app.ingestion.pdf import build_fts_query
from app.storage import search_chunks


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
