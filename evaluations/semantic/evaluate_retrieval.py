"""Run the small synthetic lexical/semantic/hybrid retrieval comparison."""

import hashlib
import json
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


def main() -> int:
    from app.config import Settings, get_settings
    from app.embeddings.service import EmbeddingService, ModelUnavailable
    from app.retrieval.service import retrieve_with_mode
    from app.storage import initialize_storage, store_document

    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    base = get_settings()
    settings = Settings(
        _env_file=None,
        database_path=Path(tempfile.gettempdir()) / "wonderlearn-m2-eval.sqlite3",
        embedding_cache_path=base.embedding_cache_path,
        embedding_model_id=base.embedding_model_id,
        embedding_model_revision=base.embedding_model_revision,
        retrieval_mode="lexical",
        max_retrieval_results=TOP_K,
        lexical_candidate_limit=20,
        semantic_candidate_limit=20,
    )

    with tempfile.TemporaryDirectory(prefix="wonderlearn-eval-") as temporary:
        database_path = Path(temporary) / "eval.sqlite3"
        _load_synthetic_corpus(
            database_path, dataset, initialize_storage, store_document
        )
        service = EmbeddingService(settings)
        tracemalloc.start()
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
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
            for mode in ("lexical", "semantic", "hybrid")
        }
        answerable_count = sum(bool(item["relevant"]) for item in dataset["queries"])
        per_query = []
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
                query_results[mode] = {
                    "retrievalMode": response["retrievalMode"],
                    "chunkIds": [row["citationId"] for row in response["results"]],
                    "scores": [
                        row["retrieval"].get("similarity")
                        for row in response["results"]
                    ],
                }
            per_query.append({"query": query_case["query"], "modes": query_results})

        query_count = len(dataset["queries"])
        for values in aggregate.values():
            values["recallAt5"] /= answerable_count
            values["precisionAt5"] /= query_count
            values["meanReciprocalRank"] /= query_count
        print(
            json.dumps(
                {
                    "modelId": settings.embedding_model_id,
                    "modelRevision": settings.embedding_model_revision,
                    "dimensions": settings.embedding_dimensions,
                    "corpusChunks": index_result["index"]["total_chunks"],
                    "indexWallSeconds": round(index_wall_seconds, 3),
                    "indexCpuSeconds": round(index_cpu_seconds, 3),
                    "peakPythonBytes": peak_python_bytes,
                    "metrics": aggregate,
                    "queries": per_query,
                    "limitations": [
                        "Small synthetic English corpus; results are not representative of learner documents.",
                        "Peak Python allocation excludes ONNX Runtime native memory.",
                        "Precision@5 counts missing slots as non-relevant; recall averages answerable queries only.",
                    ],
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
