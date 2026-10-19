# Evaluation plan

All baselines and thresholds below are proposed procedures, not results. Establish numeric targets after collecting a representative pilot set and measuring a simple baseline; do not invent performance figures.

## Dataset construction and protocol

Create versioned test suites from rights-cleared/authored diagrams, licensed educational passages, synthetic questions reviewed by a human, consented gesture sequences, and explicit unsupported examples. Split by document/template/person as relevant. Keep test records frozen; report dataset size and confidence intervals where sample size permits. Run each benchmark on the same verified machine/configuration and record model hashes, versions, warm/cold latency, and environment.

## Baselines and metrics

| Area | Baseline | Metrics |
|---|---|---|
| Vision | Manual/configured component regions; then smallest candidate detector | precision, recall, mAP@[.5:.95] when box task fits, IoU, per-class AP, unsupported rejection, latency |
| Gesture | Rule-based pinch/point and no-gesture baseline | macro-F1, per-class precision/recall, confusion matrix, false activations/minute, event latency |
| Selection | Pointer region selection then gesture projection | correct component rate, time-to-selection, failure under movement/occlusion, abstention rate |
| Retrieval | SQLite FTS5 BM25 | Recall@K, Precision@K, MRR, NDCG@K, citation page/section correctness |
| Tutor | Evidence snippets/template | evidence faithfulness, answer relevance, citation validity, human correctness, abstention quality, rubric scoring agreement |
| Product | End-to-end scripted learner tasks | completion rate, task time, errors, user-rated usefulness; learning gain via pre/post task if feasible |

Use human-reviewed answer keys and relevance judgments. For learning benefit, use an appropriate short pre/post or delayed recall task with consent; engagement alone is not learning evidence.

## Acceptance thresholds

Thresholds must be declared before evaluating a candidate on the frozen test set. For MVP, define per-task minimum acceptable values after pilot baseline and review; include safety-critical zero-tolerance checks such as invalid citation IDs and silent failure to abstain on known no-evidence cases. Log the rationale and stakeholder approval in the candidate report. Do not tune the frozen test set.

## Error analysis and promotion

Review false positives/negatives by class, source, lighting, camera pose, OCR/extraction quality, and demographic conditions where consented and ethically appropriate. Classify root cause: data coverage, label ambiguity, preprocessing, model, threshold, retrieval, or UX. Compare candidate against current baseline on frozen validation/test suites. Promote only if quality criteria pass and no material regression is found; save previous artifact and rollback path. No automated production promotion in MVP.

## M14 evaluation implementation

The versioned starter set is `evaluations/semantic/retrieval_eval.json` (dataset version 1.0.0). It is a small synthetic corpus and authored query set, not human-reviewed learner data. Expected chunk IDs, evidence substrings, document IDs, filenames, and pages are checked against the fixture before scoring. Its SHA-256 and effective retrieval settings are included in each machine report. This set is a smoke/regression benchmark, not a release-quality sample.

Run the deterministic, offline-default suite from the repository root:

```powershell
uv run --locked --project services/api --extra dev --extra semantic python evaluations/run_suite.py
```

The command runs the backend regression tests and lexical FTS5 benchmark, then writes `evaluations/generated/latest.json` and `evaluations/generated/latest.md`. The generated directory is ignored by Git. The backend suite covers tutor citation allow-listing, abstention, malformed responses, provider unavailability, quiz schema/support/citations and answer scoring, plus the M13 scripted learning journey and source deletion. The journey stubs a local model response; it tests plumbing and validation, not generated answer quality. No model is called by the offline-default command.

To compare semantic and hybrid retrieval using only the already cached embedding model, explicitly opt in:

```powershell
uv run --locked --project services/api --extra dev --extra semantic python evaluations/run_suite.py --with-model
```

The service uses `local_files_only`; this command does not fetch model weights. An uncached model is reported as a failed evaluation. The separately optional integration test can be enabled with `--include-model-tests`; it also requires the model to be cached. Tutor generation quality and quiz educational validity remain unscored pending a rights-cleared, human-reviewed answer set and rubric. Camera alignment/diagram understanding is not evaluated by this synthetic text retrieval dataset. Physical-camera tests remain manual and are not represented as automated evaluation.

The report records exact metric conventions. Recall@5 averages answerable questions; precision@5 divides by five for every query; MRR averages reciprocal first relevant rank over all queries. Citation validity checks that each returned citation matches its fixture chunk's ID, filename, page, index, and text; this verifies provenance wiring but not whether the passage semantically supports a generated claim. Unanswerable abstention recall measures empty-evidence responses on labeled unsupported queries, while answerable evidence coverage records how often any passage is returned for answerable queries. Neither is tutor faithfulness. No arbitrary pass threshold is applied and identical/weak results do not establish a winning retrieval mode. Real-world performance requires a larger, independently reviewed dataset and human citation/answer assessment.
