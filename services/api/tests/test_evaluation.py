import json
import sys
from pathlib import Path


def test_evaluation_settings_report_effective_ranking_configuration(tmp_path):
    repository_root = Path(__file__).resolve().parents[3]
    sys.path[:0] = [
        str(repository_root),
        str(repository_root / "services" / "api"),
    ]
    from evaluations.semantic.evaluate_retrieval import evaluation_settings

    from app.config import Settings
    from app.embeddings.service import QUERY_INSTRUCTION_PREFIX
    from app.ingestion.pdf import QUERY_STOP_WORDS

    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "evaluation.sqlite3",
        retrieval_mode="hybrid",
        embedding_model_id="example/model",
        embedding_model_revision="revision-123",
        embedding_dimensions=4,
        embedding_batch_size=7,
        lexical_candidate_limit=13,
        semantic_candidate_limit=17,
        max_retrieval_results=9,
        rrf_k=23,
        semantic_min_similarity=0.72,
        chunk_size_chars=1200,
        chunk_overlap_chars=100,
    )

    report = evaluation_settings(
        settings,
        query_instruction_prefix=QUERY_INSTRUCTION_PREFIX,
        lexical_query_stop_words=QUERY_STOP_WORDS,
    )

    assert report == {
        "model_id": "example/model",
        "model_revision": "revision-123",
        "embedding_dimensions": 4,
        "embedding_batch_size": 7,
        "query_instruction_prefix": QUERY_INSTRUCTION_PREFIX,
        "vector_normalization": "L2",
        "similarity_metric": "cosine",
        "default_retrieval_mode": "hybrid",
        "retrieval_modes_compared": ["lexical", "semantic", "hybrid"],
        "lexical_candidate_limit": 13,
        "semantic_candidate_limit": 17,
        "evaluation_result_limit": 5,
        "configured_max_retrieval_results": 9,
        "rrf_k": 23,
        "semantic_min_similarity": 0.72,
        "chunk_size_chars": 1200,
        "chunk_overlap_chars": 100,
        "lexical_ranker": "SQLite FTS5 BM25",
        "fts5_tokenizer": "unicode61",
        "lexical_query_term_limit": 32,
        "lexical_query_stop_words": sorted(QUERY_STOP_WORDS),
    }


def _evaluation_module():
    repository_root = Path(__file__).resolve().parents[3]
    sys.path[:0] = [str(repository_root), str(repository_root / "services" / "api")]
    from evaluations.semantic import evaluate_retrieval

    return evaluate_retrieval


def test_synthetic_relevance_labels_point_to_real_evidence_and_provenance():
    evaluator = _evaluation_module()
    dataset_path = (
        Path(__file__).resolve().parents[3] / "evaluations" / "semantic" / "retrieval_eval.json"
    )
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    assert evaluator.validate_dataset(dataset) == []


def test_recall_precision_and_mrr_follow_documented_top_k_conventions():
    evaluator = _evaluation_module()
    results = [
        {"citationId": "unrelated"},
        {"citationId": "expected"},
    ]
    assert evaluator._metrics(results, {"expected"}) == {
        "recallAt5": 1.0,
        "precisionAt5": 0.2,
        "reciprocalRank": 0.5,
    }


def test_citation_provenance_rejects_invented_or_mismatched_fields():
    evaluator = _evaluation_module()
    document = {
        "doc1": {
            "filename": "lesson.txt",
            "pages": ["Exact supporting passage."],
        }
    }
    citation = {
        "citationId": "doc1-p1",
        "documentId": "doc1",
        "displayFilename": "lesson.txt",
        "pageNumber": 1,
        "chunkIndex": 0,
        "text": "Exact supporting passage.",
    }
    assert evaluator.citation_is_valid(citation, document)
    assert not evaluator.citation_is_valid({**citation, "pageNumber": 2}, document)
    assert not evaluator.citation_is_valid({**citation, "text": "fabricated"}, document)
