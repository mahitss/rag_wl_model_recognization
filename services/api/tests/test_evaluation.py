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
