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
