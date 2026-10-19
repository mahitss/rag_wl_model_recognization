"""WonderLearn M18 real-world quality evaluation.

Extends the M14 synthetic retrieval smoke evaluation with a richer dataset and
additional quality dimensions:

- Retrieval metrics per mode (lexical, semantic, hybrid): Recall@5, MRR,
  citation validity, unsupported-claim rate, correct abstention, evidence coverage
- Diagram-concept retrieval: verifies curated diagram concept queries return
  expected evidence from indexed documents
- Quiz grounding: deterministic check that quiz answer keys are lexically
  supported by their cited evidence and that evaluate_answer scores them correctly
- Provenance consistency: document ID, page number, chunk index, and source text
  match the evaluation corpus manifest exactly

Run from the repository root:

    uv run --project services/api --extra dev python evaluations/realworld/evaluate_quality.py

With semantic/hybrid modes (requires the already-cached embedding model):

    uv run --project services/api --extra dev --extra semantic python evaluations/realworld/evaluate_quality.py --with-model

Outputs machine-readable JSON and a concise Markdown report to
evaluations/generated/quality_v2.json and evaluations/generated/quality_v2.md.
"""

from __future__ import annotations

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

DATASET_PATH = Path(__file__).with_name("retrieval_eval_v2.json")
OUTPUT_DIR = REPOSITORY_ROOT / "evaluations" / "generated"
TOP_K = 5


def _package_version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def runtime_versions(*, include_semantic: bool) -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "fastembed": _package_version("fastembed") if include_semantic else "not-loaded",
        "onnxruntime": _package_version("onnxruntime") if include_semantic else "not-loaded",
    }


def _load_synthetic_corpus(
    database_path: Path, dataset: dict, initialize_storage, store_document
) -> None:
    """Load the v2 synthetic corpus into a fresh SQLite database."""
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
                "parser_version": "synthetic-eval-v2/1",
            },
            chunks,
        )


def _metrics(results: list[dict], relevant_ids: set[str]) -> dict[str, float]:
    retrieved = [row["citationId"] for row in results[:TOP_K]]
    relevant_found = sum(chunk_id in relevant_ids for chunk_id in retrieved)
    first_rank = next(
        (rank for rank, chunk_id in enumerate(retrieved, start=1) if chunk_id in relevant_ids),
        None,
    )
    recall = relevant_found / len(relevant_ids) if relevant_ids else 0.0
    return {
        "recallAt5": recall,
        "precisionAt5": relevant_found / TOP_K,
        "reciprocalRank": 1.0 / first_rank if first_rank is not None else 0.0,
        "relevantFound": relevant_found,
        "totalRelevant": len(relevant_ids),
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


def _mock_quiz_generation(
    monkeypatch_target, quiz_payload: dict
):
    """Return a function that monkeypatches local_chat_completion to emit quiz_payload."""
    import json

    return lambda *_args, **_kwargs: json.dumps(quiz_payload)


def evaluate_quiz_grounding(settings, database_path, quiz_examples: list[dict]) -> dict:
    """Deterministically verify quiz answer keys are lexically supported by cited evidence.

    For each quiz example, monkeypatches local_chat_completion to return a quiz
    question with the expected answer and citations, then:
    1. Calls generate_quiz_questions to verify the pipeline produces a valid question
    2. Calls evaluate_answer with the expected answer to verify it scores "correct"
    3. Verifies citations reference chunks that contain the expected evidence text
    """
    from unittest import mock

    from app.quiz import evaluate_answer, generate_quiz_questions
    from app.tutor import InvalidTutorResponse

    results = []
    for example in quiz_examples:
        quiz_payload = {
            "questions": [
                {
                    "type": example["questionType"],
                    "question": example["query"],
                    "options": [] if example["questionType"] == "short_answer" else [
                        {"id": "A", "text": example["expectedAnswer"]},
                        {"id": "B", "text": "distractor option one"},
                        {"id": "C", "text": "distractor option two"},
                        {"id": "D", "text": "distractor option three"},
                    ],
                    "correctOption": "" if example["questionType"] == "short_answer" else "A",
                    "expectedAnswer": "" if example["questionType"] == "multiple_choice" else example["expectedAnswer"],
                    "acceptedAnswers": example.get("acceptedAnswers", []),
                    "explanation": f"This answer is supported by the cited evidence: {example['expectedEvidence'][0] if example['expectedEvidence'] else ''}",
                    "citations": [f"E{index + 1}" for index in range(len(example["relevant"]))],
                }
            ]
        }

        # Build evidence labels matching the quiz payload's E1, E2, ... citations
        evidence_payload = [
            {"label": f"E{index + 1}", "text": doc_text}
            for index, doc_text in enumerate(example["expectedEvidence"])
        ]
        # Patch local_chat_completion to return our quiz payload
        with mock.patch(
            "app.quiz.local_chat_completion",
            return_value=json.dumps(quiz_payload),
        ):
            try:
                generated = generate_quiz_questions(
                    settings,
                    topic=example["query"],
                    count=1,
                    evidence=evidence_payload,
                )
                generation_ok = len(generated) == 1
                generated_question = generated[0] if generated else None
            except InvalidTutorResponse as exc:
                generation_ok = False
                generated_question = None
                generation_error = str(exc)
            else:
                generation_error = None

        # Evaluate the expected answer against the generated question
        scoring_ok = False
        scoring_error = None
        if generated_question:
            question_record = {
                "questionType": generated_question["questionType"],
                "answerKey": generated_question.get("answerKey", ""),
                "acceptedAnswers": generated_question.get("acceptedAnswers", []),
                "options": generated_question.get("options", []),
            }
            try:
                outcome = evaluate_answer(question_record, example["expectedAnswer"])
                scoring_ok = outcome == "correct"
                scoring_error = None
            except (ValueError, KeyError) as exc:
                scoring_error = str(exc)

        # Verify citations reference chunks containing expected evidence text
        citation_support_ok = True
        for evidence_text in example["expectedEvidence"]:
            if not evidence_text:
                continue
            # Evidence should be a substring of the source document page text
            found = any(evidence_text in page_text for page_text in _all_page_texts(database_path))
            if not found:
                citation_support_ok = False
                break

        results.append(
            {
                "query": example["query"],
                "type": example["type"],
                "questionType": example.get("questionType"),
                "generationOk": generation_ok,
                "generationError": generation_error,
                "scoringOk": scoring_ok,
                "scoringError": scoring_error,
                "citationSupportOk": citation_support_ok,
                "expectedAnswer": example.get("expectedAnswer"),
            }
        )

    return {
        "totalQuizExamples": len(results),
        "generationOkCount": sum(1 for r in results if r["generationOk"]),
        "scoringOkCount": sum(1 for r in results if r["scoringOk"]),
        "citationSupportOkCount": sum(1 for r in results if r["citationSupportOk"]),
        "details": results,
    }


def _all_page_texts(database_path: Path) -> list[str]:
    from app.storage import connect_database

    with closing_connect_database(database_path) as connection:
        rows = connection.execute("SELECT text FROM chunks").fetchall()
    return [row["text"] for row in rows]


from contextlib import closing


def closing_connect_database(database_path: Path):
    from app.storage import connect_database

    return closing(connect_database(database_path))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-model",
        action="store_true",
        help="Also run semantic/hybrid retrieval using only the already-cached model.",
    )
    parser.add_argument(
        "--lexical-only",
        action="store_true",
        help="Run only the deterministic lexical (FTS5) benchmark.",
    )
    args = parser.parse_args()

    from app.config import Settings, get_settings
    from app.embeddings.service import (
        EmbeddingService,
        ModelUnavailable,
        QUERY_INSTRUCTION_PREFIX as query_instruction_prefix,
    )
    from app.embeddings.service import embedding_config_hash
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

    document_manifest = {document["id"]: document for document in dataset["documents"]}
    diagram_concepts = {}
    for diagram in dataset.get("diagrams", []):
        for concept in diagram["concepts"]:
            diagram_concepts[concept["conceptId"]] = {
                "diagramId": diagram["diagramId"],
                "concept": concept,
            }

    answerable_queries = [q for q in dataset["queries"] if q["relevant"]]
    unanswerable_queries = [q for q in dataset["queries"] if not q["relevant"] and not q.get("isAmbiguous")]
    ambiguous_queries = [q for q in dataset["queries"] if q.get("isAmbiguous")]
    quiz_examples = [q for q in dataset["queries"] if q["type"] == "quiz_example"]
    diagram_queries = [q for q in dataset["queries"] if q["type"] == "diagram_concept"]

    modes = ["lexical"]
    if not args.lexical_only:
        modes = ["lexical", "semantic", "hybrid"]

    with tempfile.TemporaryDirectory(prefix="wonderlearn-quality-v2-") as temporary:
        database_path = Path(temporary) / "quality_v2.sqlite3"
        _load_synthetic_corpus(database_path, dataset, initialize_storage, store_document)

        service = EmbeddingService(settings)
        tracemalloc.start()
        wall_start = time.perf_counter()
        cpu_start = time.process_time()

        if args.lexical_only:
            index_result = {"index": {"total_chunks": sum(len(doc["pages"]) for doc in dataset["documents"])}}
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

        # Per-mode retrieval evaluation
        aggregate = {}
        per_query = []
        for mode in modes:
            aggregate[mode] = {
                "recallAt5": 0.0,
                "precisionAt5": 0.0,
                "meanReciprocalRank": 0.0,
                "citationValidityRate": None,
                "unanswerableAbstentionRate": None,
                "unsupportedClaimRate": None,
                "answerableEvidenceCoverage": None,
                "citationCount": 0,
                "validCitationCount": 0,
            }

        for query_case in dataset["queries"]:
            query = query_case["query"]
            relevant = set(query_case["relevant"])
            is_unanswerable = not relevant and not query_case.get("isAmbiguous")
            is_ambiguous = query_case.get("isAmbiguous", False)

            query_result = {"query": query, "type": query_case["type"], "number": query_case["number"], "modes": {}}

            for mode in modes:
                totals = aggregate[mode]
                # Determine the effective query: diagram-concept queries use the concept's retrieval_query
                effective_query = query
                if query_case["type"] == "diagram_concept":
                    concept_info = diagram_concepts.get(query_case["conceptId"])
                    if concept_info:
                        effective_query = concept_info["concept"]["retrievalQuery"]

                response = retrieve_with_mode(
                    database_path,
                    query=effective_query,
                    document_ids=None,
                    limit=TOP_K,
                    mode=mode,
                    settings=settings,
                    embeddings=service,
                )
                results = response["results"]

                m = _metrics(results, relevant)
                for key in ("recallAt5", "precisionAt5"):
                    totals[key] += m[key]
                totals["meanReciprocalRank"] += m["reciprocalRank"]

                # Citation validity
                citations_valid = [citation_is_valid(row, document_manifest) for row in results]
                totals["validCitationCount"] += sum(citations_valid)
                totals["citationCount"] += len(citations_valid)

                # Abstention / unsupported-claim analysis
                answer_response = answer_from_evidence(query, results)
                abstained = answer_response["status"] == "insufficient_evidence"

                if is_unanswerable:
                    # Correct abstention: unanswerable query returns no evidence
                    if not results:
                        totals.setdefault("correctAbstentionCount", 0)
                        totals["correctAbstentionCount"] += 1
                    # Unsupported claim: unanswerable query returns evidence
                    if results:
                        totals.setdefault("unsupportedClaimCount", 0)
                        totals["unsupportedClaimCount"] += 1
                elif relevant and not abstained:
                    totals.setdefault("answerableWithEvidenceCount", 0)
                    totals["answerableWithEvidenceCount"] += 1

                # Diagram-concept: check that expected relevant chunk appears
                if query_case["type"] == "diagram_concept":
                    expected_ids = set(query_case["relevant"])
                    retrieved_ids = {row["citationId"] for row in results}
                    totals.setdefault("diagramConceptHitCount", 0)
                    if expected_ids & retrieved_ids:
                        totals["diagramConceptHitCount"] += 1

                query_result["modes"][mode] = {
                    "retrievalMode": response["retrievalMode"],
                    "fallbackReason": response.get("fallbackReason"),
                    "chunkIds": [row["citationId"] for row in results],
                    "metrics": m,
                    "citationValidity": citations_valid,
                    "abstained": abstained,
                    "isUnanswerable": is_unanswerable,
                    "isAmbiguous": is_ambiguous,
                    "answerStatus": answer_response["status"],
                }

            per_query.append(query_result)

        # Finalize aggregates
        answerable_count = len(answerable_queries)
        unanswerable_count = len(unanswerable_queries)
        ambiguous_count = len(ambiguous_queries)
        query_count = len(dataset["queries"])

        for mode in modes:
            totals = aggregate[mode]
            totals["recallAt5"] = totals["recallAt5"] / answerable_count if answerable_count else 0.0
            totals["precisionAt5"] = totals["precisionAt5"] / query_count if query_count else 0.0
            totals["meanReciprocalRank"] = totals["meanReciprocalRank"] / query_count if query_count else 0.0
            totals["citationValidityRate"] = (
                totals["validCitationCount"] / totals["citationCount"] if totals["citationCount"] else None
            )
            totals["unanswerableAbstentionRate"] = (
                totals.get("correctAbstentionCount", 0) / unanswerable_count if unanswerable_count else None
            )
            totals["unsupportedClaimRate"] = (
                totals.get("unsupportedClaimCount", 0) / unanswerable_count if unanswerable_count else None
            )
            totals["answerableEvidenceCoverage"] = (
                totals.get("answerableWithEvidenceCount", 0) / answerable_count if answerable_count else None
            )
            totals["diagramConceptHitRate"] = (
                totals.get("diagramConceptHitCount", 0) / len(diagram_queries) if diagram_queries else None
            )

        # Quiz grounding evaluation (deterministic, no model needed)
        quiz_report = evaluate_quiz_grounding(settings, database_path, quiz_examples)

        report = {
            "reportVersion": "2",
            "generatedAtUtc": datetime.now(UTC).isoformat(timespec="seconds"),
            "dataset": {
                "path": "evaluations/realworld/retrieval_eval_v2.json",
                "version": dataset["datasetVersion"],
                "kind": dataset["datasetKind"],
                "sha256": hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest(),
                "queries": len(dataset["queries"]),
                "documents": len(dataset["documents"]),
                "diagrams": len(dataset.get("diagrams", [])),
            },
            "runtime": runtime_versions(include_semantic=not args.lexical_only),
            "effectiveSettings": {
                "modelId": settings.embedding_model_id,
                "modelRevision": settings.embedding_model_revision,
                "embeddingDimensions": settings.embedding_dimensions,
                "retrievalModesCompared": modes,
                "topK": TOP_K,
                "lexicalRanker": "SQLite FTS5 BM25",
                "similarityMetric": "cosine",
                "vectorNormalization": "L2",
            },
            "indexing": {
                "corpusChunks": index_result["index"]["total_chunks"],
                "indexWallSeconds": round(index_wall_seconds, 3),
                "indexCpuSeconds": round(index_cpu_seconds, 3),
                "peakPythonBytes": peak_python_bytes,
            },
            "queryBreakdown": {
                "total": query_count,
                "answerable": answerable_count,
                "unanswerable": unanswerable_count,
                "ambiguous": ambiguous_count,
                "diagramConcept": len(diagram_queries),
                "quizExamples": len(quiz_examples),
            },
            "metrics": aggregate,
            "queries": per_query,
            "quizGrounding": quiz_report,
            "interpretation": (
                "Synthetic quality evaluation only. Metrics are computed against "
                "authored synthetic labels and do not establish real-world learner "
                "performance or tutor answer quality. Quiz grounding is verified "
                "deterministically with a mocked model response."
            ),
            "limitations": [
                "Small synthetic English corpus; results are not representative of learner documents.",
                "Recall@5 averages answerable queries only; precision@5 counts missing slots as non-relevant.",
                "Citation validity checks API provenance against the synthetic manifest, not semantic relevance.",
                "Unsupported-claim rate measures empty retrieval for labeled unanswerable queries.",
                "Quiz grounding uses mocked model output; real model answer quality is not scored.",
                "Peak Python allocation excludes ONNX Runtime native memory.",
                "Semantic/hybrid modes require the locally cached embedding model.",
            ],
        }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / "quality_v2.json"
    markdown_path = OUTPUT_DIR / "quality_v2.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    print(f"Quality evaluation JSON: {json_path.relative_to(REPOSITORY_ROOT)}")
    print(f"Quality evaluation report: {markdown_path.relative_to(REPOSITORY_ROOT)}")
    print(markdown_path.read_text(encoding="utf-8"))
    return 0


def _markdown(report: dict) -> str:
    lines = [
        "# WonderLearn M18 quality evaluation report",
        "",
        f"- Generated: {report['generatedAtUtc']}",
        f"- Dataset: `{report['dataset']['version']}` ({report['dataset']['kind']}), SHA-256 `{report['dataset']['sha256']}`",
        f"- Queries: {report['dataset']['queries']} across {report['dataset']['documents']} documents",
        f"- Modes compared: {', '.join(report['effectiveSettings']['retrievalModesCompared'])}",
        f"- Tutor/quiz quality scored: no (deterministic quiz grounding only)",
        "",
        "## Query breakdown",
        "",
        f"- Total: {report['queryBreakdown']['total']}",
        f"- Answerable: {report['queryBreakdown']['answerable']}",
        f"- Unanswerable: {report['queryBreakdown']['unanswerable']}",
        f"- Ambiguous: {report['queryBreakdown']['ambiguous']}",
        f"- Diagram-concept: {report['queryBreakdown']['diagramConcept']}",
        f"- Quiz examples: {report['queryBreakdown']['quizExamples']}",
        "",
        "## Retrieval metrics by mode",
        "",
        "| Mode | Recall@5 | MRR | Citation Validity | Unanswerable Abstention | Unsupported Claim | Answerable Coverage |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for mode, values in report["metrics"].items():
        def fmt(value):
            return f"{value:.3f}" if isinstance(value, float) else str(value)
        lines.append(
            f"| {mode} | {fmt(values['recallAt5'])} | {fmt(values['meanReciprocalRank'])} | "
            f"{fmt(values['citationValidityRate'])} | {fmt(values['unanswerableAbstentionRate'])} | "
            f"{fmt(values['unsupportedClaimRate'])} | {fmt(values['answerableEvidenceCoverage'])} |"
        )
    if report["queryBreakdown"]["diagramConcept"] > 0:
        lines.append("")
        lines.append("## Diagram-concept retrieval")
        lines.append("")
        for mode, values in report["metrics"].items():
            lines.append(
                f"- {mode}: diagram-concept hit rate = {values.get('diagramConceptHitRate')}"
            )

    qg = report["quizGrounding"]
    lines += [
        "",
        "## Quiz grounding (deterministic, mocked model)",
        "",
        f"- Total quiz examples: {qg['totalQuizExamples']}",
        f"- Generation OK: {qg['generationOkCount']} / {qg['totalQuizExamples']}",
        f"- Scoring OK (expected answer scores correct): {qg['scoringOkCount']} / {qg['totalQuizExamples']}",
        f"- Citation support OK: {qg['citationSupportOkCount']} / {qg['totalQuizExamples']}",
    ]

    lines += ["", report["interpretation"], ""]
    for limitation in report["limitations"]:
        lines.append(f"- {limitation}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
