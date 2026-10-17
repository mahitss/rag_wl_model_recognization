# Data strategy

## Sources and licensing

Start with developer-authored or public-domain/openly licensed educational examples, plus user-uploaded materials the user is entitled to process. User upload does not grant WonderLearn redistribution or training rights. Keep copyrighted textbooks local, do not add them to shared training/evaluation sets, and do not scrape websites. For every external sample record URL/author, license/version, access date, permitted uses, attribution, and redistribution status. Keep a `DATASET_CARD.md` with each dataset version.

## Annotation

- **Diagram:** image ID, source/license, diagram class, component class, box or polygon in normalized image coordinates, canonical concept ID, aliases, annotator, review status, occlusion/legibility flags.
- **Gestures:** consented video capture converted to landmarks where possible; store minimal derived sequences, gesture label, handedness, lighting/camera context, participant pseudonym, consent, and deletion schedule. Avoid face/audio capture and raw video retention by default.
- **RAG judgments:** question, expected supporting chunk IDs, acceptable answer points, abstention-needed flag, document version, annotator rationale. Use only rights-cleared material.
- **Tutor checks:** prompt, expected rubric items, misconception tags, reviewed answer examples; human review required before use.

Use two-pass annotation for evaluation-critical labels where feasible: independent label then adjudication. Measure agreement and record unresolved ambiguity rather than force a label.

## Versioning and split design

Version manifests and annotation files with Git for small metadata; use DVC or plain content-addressed manifests only if data volume justifies. Keep raw assets out of Git. Record checksums and storage location. Split by source document, diagram/template, and participant—not randomly by near-duplicate image/frame—to prevent leakage. Maintain a frozen test set; tune only on train/validation. Include out-of-scope examples for rejection evaluation.

## Storage and privacy

Local folders: `data/raw/` (ignored), `data/processed/` (ignored), `data/manifests/` (versioned metadata where rights allow), `artifacts/` (ignored), and evaluation reports with only permitted excerpts. Provide deletion paths for documents and derived indexes. Camera frames are ephemeral in browser; no raw frames to server unless an explicit future feature requires it. Student answers are session-only by default. If progress history is enabled, local-only, opt-in, exportable/deletable, with no unnecessary identifiers.

## Dataset growth

Collect only to answer an identified evaluation gap. Prioritize a small diverse set of licensed diagram families and deliberate negative/unsupported cases over large noisy internet datasets. Record class distribution, annotation coverage, exclusions, known bias, and quality issues. Never claim a dataset or model is representative without evidence.
