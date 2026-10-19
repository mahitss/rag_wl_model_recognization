"""Run the small synthetic lexical/semantic/hybrid retrieval comparison."""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "api"))

DATASET_PATH = Path(__file__).with_name("retrieval_eval.json")
TOP_K = 5


def evaluation_settings(
    settings,
    *,
    query_instruction_prefix: str,
    lexical_query_stop_words: set[str],
    modes: tuple[str, ...] = ("lexical", "semantic", "hybrid"),
) -> dict:
    """Describe the effective settings used by this evaluation run."""
    return {
        "model_id": settings.embedding_model_id,
        "model_revision": settings.embedding_model_revision,
        "embedding_dimensions": settings.embedding_dimensions,
        "embedding_batch_size": settings.embedding_batch_size,
        "query_instruction_prefix": query_instruction_prefix,
        "vector_normalization": "L2",
        "similarity_metric": "cosine",
        "default_retrieval_mode": settings.retrieval_mode,
        "retrieval_modes_compared": list(modes),
        "lexical_candidate_limit": settings.lexical_candidate_limit,
        "semantic_candidate_limit": settings.semantic_candidate_limit,
        "evaluation_result_limit": TOP_K,
        "configured_max_retrieval_results": settings.max_retrieval_results,
        "rrf_k": settings.rrf_k,
        "semantic_min_similarity": settings.semantic_min_similarity,
        "chunk_size_chars": settings.chunk_size_chars,
        "chunk_overlap_chars": settings.chunk_overlap_chars,
        "lexical_ranker": "SQLite FTS5 BM25",
        "fts5_tokenizer": "unicode61",
        "lexical_query_term_limit": 32,
        "lexical_query_stop_words": sorted(lexical_query_stop_words),
    }


def runtime_versions(*, include_semantic: bool) -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "fastembed": _package_version("fastembed")
        if include_semantic
        else "not-loaded",
        "onnxruntime": _package_version("onnxruntime")
        if include_semantic
        else "not-loaded",
    }


def _package_version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _load_synthetic_corpus(
    database_path: Path, dataset: dict, initialize_storage, store_document
) -> None:
    initialize_storage(database_path)
    timestamp = datetime.now(UTC).isoformat(timespec="seconds")
    for document in dataset["documents"]:
        page_texts = document["pages"]
        content_hash = hashlib.sha256("\n".join(page_texts).encode("utf-8")).hexdigest()
        document_id = document["id"]
        chunks = [
            {
                "chunk_id": f"{document_id}-p{page_number}",
                "page_number": page_number,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": len(text),
                "text": text,
            }
            for page_number, text in enumerate(page_texts, start=1)
        ]
        store_document(
            database_path,
            {
                "document_id": document_id,
                "display_name": document["filename"],
                "sha256": content_hash,
                "byte_size": len("\n".join(page_texts).encode("utf-8")),
                "page_count": len(page_texts),
                "chunk_count": len(chunks),
                "created_at": timestamp,
                "parser_version": "synthetic-eval/1",
            },
            chunks,
        )


def _metrics(results: list[dict], relevant_ids: set[str]) -> dict[str, float]:
    retrieved = [row["citationId"] for row in results[:TOP_K]]
    relevant_found = sum(chunk_id in relevant_ids for chunk_id in retrieved)
    first_rank = next(
        (
            rank
            for rank, chunk_id in enumerate(retrieved, start=1)
            if chunk_id in relevant_ids
        ),
        None,
    )
    recall = relevant_found / len(relevant_ids) if relevant_ids else 0.0
    return {
        "recallAt5": recall,
        "precisionAt5": relevant_found / TOP_K,
        "reciprocalRank": 1.0 / first_rank if first_rank is not None else 0.0,
    }


def citation_is_valid(row: dict, documents: dict[str, dict]) -> bool:
    """Check retrieved citation provenance against the evaluation corpus manifest."""
    citation_id = row.get("citationId", "")
    document_id = row.get("documentId", "")
    document = documents.get(document_id)
    if document is None or not citation_id.startswith(f"{document_id}-p"):
        return False
    try:
        page_number = int(citation_id.rsplit("-p", 1)[1])
        expected_text = document["pages"][page_number - 1]
    except (ValueError, IndexError):
        return False
    return (
        row.get("displayFilename") == document["filename"]
        and row.get("pageNumber") == page_number
        and row.get("chunkIndex") == 0
        and row.get("text") == expected_text
    )


def validate_dataset(dataset: dict) -> list[str]:
    """Validate synthetic evidence labels against the authored corpus before scoring."""
    issues = []
    documents = {document["id"]: document for document in dataset.get("documents", [])}
    if not dataset.get("datasetVersion") or dataset.get("datasetKind") != "synthetic":
        issues.append("dataset version/kind is missing or not marked synthetic")
    for case_number, case in enumerate(dataset.get("queries", []), start=1):
        relevant = case.get("relevant", [])
        expected_evidence = case.get("expectedEvidence", [])
        expected_sources = case.get("expectedSources", [])
        if not (len(relevant) == len(expected_evidence) == len(expected_sources)):
            issues.append(f"query {case_number}: evidence/source labels do not align")
            continue
        for chunk_id, evidence, source in zip(
            relevant, expected_evidence, expected_sources, strict=True
        ):
            try:
                document_id, page_text = chunk_id.rsplit("-p", 1)
                document = documents[document_id]
                page_number = int(page_text)
                source_text = document["pages"][page_number - 1]
            except (KeyError, ValueError, IndexError):
                issues.append(f"query {case_number}: unknown expected chunk {chunk_id}")
                continue
            if (
                not evidence
                or evidence not in source_text
                or source
                != {
                    "documentId": document_id,
                    "filename": document["filename"],
                    "pageNumber": page_number,
                }
            ):
                issues.append(
                    f"query {case_number}: invalid evidence/provenance for {chunk_id}"
                )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lexical-only",
        action="store_true",
        help="Run the deterministic offline FTS5 benchmark without loading embeddings.",
    )
    args = parser.parse_args()
    from app.config import Settings, get_settings
    from app.embeddings.service import (
        QUERY_INSTRUCTION_PREFIX as query_instruction_prefix,
    )
    from app.embeddings.service import (
        EmbeddingService,
        ModelUnavailable,
    )
    from app.ingestion.pdf import QUERY_STOP_WORDS as lexical_query_stop_words
    from app.retrieval.service import answer_from_evidence, retrieve_with_mode
    from app.storage import initialize_storage, store_document

    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    dataset_issues = validate_dataset(dataset)
    if dataset_issues:
        print(json.dumps({"datasetErrors": dataset_issues}), file=sys.stderr)
        return 2
    base = get_settings()
    settings = Settings(_env_file=None, **base.model_dump())

    with tempfile.TemporaryDirectory(prefix="wonderlearn-eval-") as temporary:
        database_path = Path(temporary) / "eval.sqlite3"
        _load_synthetic_corpus(
            database_path, dataset, initialize_storage, store_document
        )
        service = EmbeddingService(settings)
        tracemalloc.start()
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        modes = ("lexical",) if args.lexical_only else ("lexical", "semantic", "hybrid")
        if args.lexical_only:
            index_result = {
                "index": {
                    "total_chunks": sum(
                        len(doc["pages"]) for doc in dataset["documents"]
                    )
                }
            }
        else:
            try:
                index_result = service.rebuild(database_path)
            except ModelUnavailable as exc:
                print(f"Model unavailable: {exc}", file=sys.stderr)
                return 2
        index_wall_seconds = time.perf_counter() - wall_start
        index_cpu_seconds = time.process_time() - cpu_start
        _, peak_python_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        aggregate = {
            mode: {"recallAt5": 0.0, "precisionAt5": 0.0, "meanReciprocalRank": 0.0}
            for mode in modes
        }
        answerable_count = sum(bool(item["relevant"]) for item in dataset["queries"])
        per_query = []
        document_manifest = {
            document["id"]: document for document in dataset["documents"]
        }
        citation_valid_count = {mode: 0 for mode in modes}
        citation_count = {mode: 0 for mode in modes}
        abstention_correct = {mode: 0 for mode in modes}
        answerable_with_evidence = {mode: 0 for mode in modes}
        unanswerable_count = sum(not item["relevant"] for item in dataset["queries"])
        for query_case in dataset["queries"]:
            query_results = {}
            relevant = set(query_case["relevant"])
            for mode, totals in aggregate.items():
                response = retrieve_with_mode(
                    database_path,
                    query=query_case["query"],
                    document_ids=None,
                    limit=TOP_K,
                    mode=mode,
                    settings=settings,
                    embeddings=service,
                )
                metrics = _metrics(response["results"], relevant)
                for metric, value in metrics.items():
                    key = "meanReciprocalRank" if metric == "reciprocalRank" else metric
                    totals[key] += value
                citations_valid = [
                    citation_is_valid(row, document_manifest)
                    for row in response["results"]
                ]
                citation_valid_count[mode] += sum(citations_valid)
                citation_count[mode] += len(citations_valid)
                answer_response = answer_from_evidence(
                    query_case["query"], response["results"]
                )
                abstained = answer_response["status"] == "insufficient_evidence"
                if not relevant and abstained:
                    abstention_correct[mode] += 1
                if relevant and not abstained:
                    answerable_with_evidence[mode] += 1
                query_results[mode] = {
                    "retrievalMode": response["retrievalMode"],
                    "chunkIds": [row["citationId"] for row in response["results"]],
                    "scores": [
                        row["retrieval"].get("similarity")
                        for row in response["results"]
                    ],
                    "citationValidity": citations_valid,
                    "expectedAbstention": not bool(relevant),
                    "abstained": abstained,
                    "answerStatus": answer_response["status"],
                }
            per_query.append({"query": query_case["query"], "modes": query_results})

        query_count = len(dataset["queries"])
        for values in aggregate.values():
            values["recallAt5"] /= answerable_count
            values["precisionAt5"] /= query_count
            values["meanReciprocalRank"] /= query_count
        for mode, values in aggregate.items():
            values["citationValidityRate"] = (
                citation_valid_count[mode] / citation_count[mode]
                if citation_count[mode]
                else None
            )
            values["unanswerableAbstentionRecall"] = (
                abstention_correct[mode] / unanswerable_count
                if unanswerable_count
                else None
            )
            values["answerableEvidenceCoverage"] = (
                answerable_with_evidence[mode] / answerable_count
                if answerable_count
                else None
            )
            values["citationCount"] = citation_count[mode]
            values["unanswerableCount"] = unanswerable_count
        report = {
            "datasetVersion": dataset["datasetVersion"],
            "datasetKind": dataset["datasetKind"],
            "datasetLabeling": dataset["labeling"],
            "evaluationMode": "lexical_offline"
            if args.lexical_only
            else "local_model_synthetic",
            "datasetSha256": hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest(),
            "modelId": None if args.lexical_only else settings.embedding_model_id,
            "modelRevision": None
            if args.lexical_only
            else settings.embedding_model_revision,
            "dimensions": None if args.lexical_only else settings.embedding_dimensions,
            "runtimeVersions": runtime_versions(include_semantic=not args.lexical_only),
            "effectiveSettings": evaluation_settings(
                settings,
                query_instruction_prefix=query_instruction_prefix,
                lexical_query_stop_words=lexical_query_stop_words,
                modes=modes,
            ),
            "metricConventions": {
                "recallAt5": "macro average over answerable queries only; relevant chunks are exact labeled chunk IDs",
                "precisionAt5": "relevant results divided by 5 for every query; missing slots and unanswerable queries count as non-relevant",
                "meanReciprocalRank": "mean reciprocal first relevant rank across all queries; unanswerable queries contribute zero",
                "citationValidityRate": "retrieved citations with exact document ID, filename, page, chunk index, and source text divided by returned citations",
                "unanswerableAbstentionRecall": "unanswerable synthetic queries with zero returned evidence divided by unanswerable queries; not a tutor faithfulness score",
                "answerableEvidenceCoverage": "answerable synthetic queries with at least one returned evidence passage divided by answerable queries",
            },
            "corpusChunks": index_result["index"]["total_chunks"],
            "indexWallSeconds": round(index_wall_seconds, 3),
            "indexCpuSeconds": round(index_cpu_seconds, 3),
            "peakPythonBytes": peak_python_bytes,
            "metrics": aggregate,
            "queries": per_query,
            "interpretation": "Synthetic smoke results only; no mode is declared superior and these results do not establish real-world learner performance.",
            "limitations": [
                "Small synthetic English corpus; results are not representative of learner documents.",
                "Peak Python allocation excludes ONNX Runtime native memory.",
                "Precision@5 counts missing slots as non-relevant; recall averages answerable queries only.",
                "Citation validity checks API provenance against this synthetic manifest, not semantic correctness of passage relevance.",
                "Abstention measures empty retrieval for labeled unanswerable queries, not whether generated prose is faithful.",
            ],
        }
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
