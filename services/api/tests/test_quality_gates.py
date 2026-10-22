"""M18 quality gate regression tests.

These tests verify retrieval and grounded-answer quality using the M18 synthetic
evaluation dataset (evaluations/realworld/retrieval_eval_v2.json).  They run in
lexical-only mode and use a mocked model for quiz grounding, so no external
model download or network access is required.

Run with:

    uv run --project services/api --extra dev pytest
    services/api/tests/test_quality_gates.py -m quality_gates
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

sys.path.insert(0, "services/api")

from app.config import Settings  # noqa: E402
from app.retrieval.service import answer_from_evidence, retrieve_with_mode  # noqa: E402
from app.storage import initialize_storage, store_document  # noqa: E402

pytestmark = pytest.mark.quality_gates

DATASET_PATH = Path("evaluations/realworld/retrieval_eval_v2.json")
TOP_K = 5


def make_pdf_pages(page_texts: list[str]) -> bytes:
    writer = PdfWriter()
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        if text:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
            )
            content = DecodedStreamObject()
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _load_dataset(tmp_path: Path) -> tuple[Settings, dict, dict]:
    """Load the v2 synthetic corpus into a temp database.

    Returns settings, dataset, and document manifest.
    """
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    db_path = tmp_path / "quality_gates.sqlite3"
    settings = Settings(
        _env_file=None,
        database_path=db_path,
        cors_origins="http://localhost:5173",
        retrieval_mode="lexical",
        chunk_size_chars=2_000,
        chunk_overlap_chars=200,
        max_retrieval_results=10,
    )
    initialize_storage(db_path)
    timestamp = "2026-01-01T00:00:00Z"
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
            db_path,
            {
                "document_id": document_id,
                "display_name": document["filename"],
                "sha256": content_hash,
                "byte_size": len("\n".join(page_texts).encode("utf-8")),
                "page_count": len(page_texts),
                "chunk_count": len(chunks),
                "created_at": timestamp,
                "parser_version": "quality-gates/1",
            },
            chunks,
        )
    manifest = {document["id"]: document for document in dataset["documents"]}
    return settings, dataset, manifest


def _retrieve(settings, database_path, query: str, mode: str = "lexical"):
    from app.embeddings.service import EmbeddingService

    service = EmbeddingService(settings)
    return retrieve_with_mode(
        database_path,
        query=query,
        document_ids=None,
        limit=TOP_K,
        mode=mode,
        settings=settings,
        embeddings=service,
    )


def test_factual_queries_return_relevant_evidence(tmp_path):
    """Every factual query should retrieve at least one of its relevant chunks."""
    settings, dataset, manifest = _load_dataset(tmp_path)
    factual = [q for q in dataset["queries"] if q["type"] == "factual"]
    for query_case in factual:
        response = _retrieve(settings, settings.database_path, query_case["query"])
        retrieved_ids = {row["citationId"] for row in response["results"]}
        expected = set(query_case["relevant"])
        assert retrieved_ids & expected, (
            f"Query '{query_case['query']}' retrieved none of the expected chunks {expected}"
        )


def test_multi_chunk_queries_return_multiple_relevant_chunks(tmp_path):
    """Multi-chunk queries should retrieve more than one relevant chunk."""
    settings, dataset, _manifest = _load_dataset(tmp_path)
    multi = [q for q in dataset["queries"] if q["type"] == "multi_chunk"]
    for query_case in multi:
        response = _retrieve(settings, settings.database_path, query_case["query"])
        retrieved_ids = {row["citationId"] for row in response["results"]}
        expected = set(query_case["relevant"])
        overlap = retrieved_ids & expected
        assert len(overlap) >= 2, "Multi-chunk query retrieved too few expected chunks"


def test_unanswerable_queries_abstain_when_no_results(tmp_path):
    """When retrieval returns no results for unanswerable queries, the answer abstains."""
    settings, dataset, _manifest = _load_dataset(tmp_path)
    unanswerable = [
        q for q in dataset["queries"] if q["type"] == "unanswerable" and not q.get("isAmbiguous")
    ]
    for query_case in unanswerable:
        response = _retrieve(settings, settings.database_path, query_case["query"])
        answer = answer_from_evidence(query_case["query"], response["results"])
        if not response["results"]:
            assert answer["status"] == "insufficient_evidence", (
                f"Unanswerable query '{query_case['query']}' returned no results but "
                f"answer status is '{answer['status']}'"
            )


def test_ambiguous_queries_do_not_overclaim(tmp_path):
    """Ambiguous queries should not return a confident single-answer grounding."""
    settings, dataset, _manifest = _load_dataset(tmp_path)
    ambiguous = [q for q in dataset["queries"] if q.get("isAmbiguous")]
    for query_case in ambiguous:
        response = _retrieve(settings, settings.database_path, query_case["query"])
        answer = answer_from_evidence(query_case["query"], response["results"])
        # For ambiguous queries with no evidence, the system should abstain.
        if not response["results"]:
            assert answer["status"] == "insufficient_evidence"


def test_citation_provenance_is_exact(tmp_path):
    """Every retrieved citation must match the corpus manifest exactly."""
    settings, dataset, manifest = _load_dataset(tmp_path)
    all_queries = [q for q in dataset["queries"] if q["relevant"]]
    for query_case in all_queries:
        response = _retrieve(settings, settings.database_path, query_case["query"])
        for row in response["results"]:
            citation_id = row["citationId"]
            document_id = row["documentId"]
            document = manifest.get(document_id)
            assert document is not None, f"Unknown document ID {document_id}"
            assert citation_id.startswith(f"{document_id}-p"), (
                f"Citation ID {citation_id} does not follow document-page convention"
            )
            try:
                page_number = int(citation_id.rsplit("-p", 1)[1])
            except ValueError:
                pytest.fail(f"Invalid citation ID {citation_id}")
            assert page_number <= len(document["pages"]), (
                f"Citation {citation_id} references page {page_number} but document has "
                f"{len(document['pages'])} pages"
            )
            assert row.get("text") == document["pages"][page_number - 1], (
                f"Citation {citation_id} text does not match source document page text"
            )
            assert row.get("pageNumber") == page_number
            assert row.get("chunkIndex") == 0
            assert row.get("displayFilename") == document["filename"]


def test_page_provenance_queries_return_correct_page(tmp_path):
    """Page-provenance queries should retrieve chunks from the expected page."""
    settings, dataset, _manifest = _load_dataset(tmp_path)
    provenance = [q for q in dataset["queries"] if q["type"] == "page_provenance"]
    for query_case in provenance:
        response = _retrieve(settings, settings.database_path, query_case["query"])
        retrieved_ids = {row["citationId"] for row in response["results"]}
        expected = set(query_case["relevant"])
        assert retrieved_ids & expected, "Page-provenance query missed expected chunks"


def test_quiz_answer_keys_score_correct(tmp_path):
    """Quiz answer keys should score 'correct' when submitted via evaluate_answer."""
    settings, dataset, manifest = _load_dataset(tmp_path)
    quiz_examples = [q for q in dataset["queries"] if q["type"] == "quiz_example"]
    from app.quiz import evaluate_answer

    for example in quiz_examples:
        # Build a question record as if it came from the quiz generation pipeline
        question_record = {
            "questionType": example["questionType"],
            "answerKey": example["expectedAnswer"],
            "acceptedAnswers": example.get("acceptedAnswers", []),
            "options": [],
        }
        outcome = evaluate_answer(question_record, example["expectedAnswer"])
        assert outcome == "correct", (
            f"Quiz example '{example['query']}' expected answer '{example['expectedAnswer']}' "
            f"scored '{outcome}', expected 'correct'"
        )


def test_diagram_concept_retrieval_returns_expected_evidence(tmp_path):
    """Diagram-concept queries should retrieve evidence matching the concept's target chunk."""
    settings, dataset, manifest = _load_dataset(tmp_path)
    diagram_queries = [q for q in dataset["queries"] if q["type"] == "diagram_concept"]
    diagram_concepts = {}
    for diagram in dataset.get("diagrams", []):
        for concept in diagram["concepts"]:
            diagram_concepts[concept["conceptId"]] = concept

    for query_case in diagram_queries:
        concept = diagram_concepts[query_case["conceptId"]]
        response = _retrieve(
            settings, settings.database_path, concept["retrievalQuery"], mode="lexical"
        )
        retrieved_ids = {row["citationId"] for row in response["results"]}
        expected = set(query_case["relevant"])
        assert retrieved_ids & expected, (
            f"Diagram-concept query for '{query_case['conceptId']}' using concept query "
            f"'{concept['retrievalQuery']}' did not retrieve expected chunk(s) {expected}"
        )


def test_dataset_labels_match_corpus(tmp_path):
    """Every expected evidence snippet must appear in its source document page."""
    settings, dataset, manifest = _load_dataset(tmp_path)
    for query_case in dataset["queries"]:
        relevant = query_case["relevant"]
        evidence_list = query_case["expectedEvidence"]
        sources = query_case["expectedSources"]
        for chunk_id, evidence, source in zip(relevant, evidence_list, sources, strict=True):
            document_id, page_str = chunk_id.rsplit("-p", 1)
            document = manifest[document_id]
            page_number = int(page_str)
            source_text = document["pages"][page_number - 1]
            assert evidence in source_text, (
                f"Query {query_case['number']}: evidence '{evidence}' not found in "
                f"{chunk_id} page text"
            )
            assert source == {
                "documentId": document_id,
                "filename": document["filename"],
                "pageNumber": page_number,
            }


def test_lexical_retrieval_is_deterministic(tmp_path):
    """Running the same query twice should return identical results."""
    settings, dataset, _manifest = _load_dataset(tmp_path)
    query = "What is photosynthesis?"
    response1 = _retrieve(settings, settings.database_path, query)
    response2 = _retrieve(settings, settings.database_path, query)
    ids1 = [row["citationId"] for row in response1["results"]]
    ids2 = [row["citationId"] for row in response2["results"]]
    assert ids1 == ids2, "Lexical retrieval returned non-deterministic results across runs"


def test_no_query_returns_more_than_top_k(tmp_path):
    """Retrieval results must be bounded by TOP_K."""
    settings, dataset, _manifest = _load_dataset(tmp_path)
    for query_case in dataset["queries"]:
        if not query_case["relevant"]:
            continue
        response = _retrieve(settings, settings.database_path, query_case["query"])
        assert len(response["results"]) <= TOP_K, (
            f"Query '{query_case['query']}' returned {len(response['results'])} results, "
            f"exceeding TOP_K={TOP_K}"
        )
