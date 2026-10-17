# Risk register

| Risk | Likelihood / impact | Mitigation | Fallback / trigger to revisit |
|---|---|---|---|
| Generic diagram recognition needs more labeled data than expected | High / high | start with one curated diagram family and explicit regions; collect annotations before modeling | support manual region map; expand only after held-out results |
| Monocular pointing drifts with motion, crop, or occlusion | High / medium | 2D normalized coordinates, tracking confidence, stale overlay state, temporal smoothing | click/touch/accessibility list; never claim 3D accuracy |
| False gesture activations disrupt learning | Medium / high | rules baseline, neutral reset, thresholds, cooldown, confirmation, false events/min benchmark | disable gestures per user/session |
| Local LLM is too slow or unreliable on 16 GB CPU | High / medium | template path, compact quantized candidate, short context, benchmark first | evidence cards and answer templates |
| Free notebook GPU is unavailable or interrupted | Medium / medium | small resumable runs, checkpoints, CPU baselines, no dependency on cloud storage | delay training; keep retrieval/demo scoped |
| Model/tool license conflicts with future distribution | Medium / high | artifact-level license inventory; avoid AGPL components by default absent explicit open-source commitment | swap candidate; legal review before distribution |
| User-uploaded textbook rights unclear | High / high | user-responsibility notice, local processing, no redistribution/training by default | support openly licensed demo corpus |
| PDF parser exploit/resource exhaustion | Medium / high | size/page limits, parser isolation, timeouts, no active content, dependency updates | reject problematic file; process page-limited copies |
| Prompt injection in documents influences tutor | Medium / high | retrieved text isolated as untrusted data, no tool privileges, citations validated in app | disable generation; show raw evidence |
| Citation points to wrong page or fabricated source | Medium / high | provenance generated from extraction metadata, answer cites evidence IDs only, citation benchmark | evidence-only mode until fixed |
| OCR corrupts formulas/tables/labels | High / medium | defer OCR, mark extraction gaps, evaluate OCR separately | request text PDF or manual query |
| Privacy leakage via frames/answers/logs | Low-medium / high | browser-only frames, minimal logging, local opt-in sessions, deletion | disable persistence and upload path |
| Storage growth from documents/models/artifacts | Medium / medium | quotas/status, model manifest, ignore large files, user deletion and cleanup | remove optional embeddings or models |
| Learning analytics overstate educational benefit | Medium / high | evaluate learning tasks, pre/post or delayed recall, report uncertainty | make no learning efficacy claim |

Likelihood labels are qualitative planning judgments, not measured probabilities.
