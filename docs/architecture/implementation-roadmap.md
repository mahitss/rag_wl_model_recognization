# Implementation roadmap

Effort estimates assume one developer learning while building, working part-time (roughly 10–15 hours/week), a verified 16 GB RAM Windows laptop, no application code currently present, and use of free tools with no guaranteed hosted GPU. Calendar estimates are ranges, not commitments.

## First week plan

1. **Day 1:** verify Python/Node/Git/browser and machine; make repository skeleton, decision log, license policy, and sample rights-cleared text PDF.
2. **Day 2:** define API schemas and SQLite tables; implement text extraction with per-page provenance and safe upload limits.
3. **Day 3:** implement chunking and FTS5 index; add deletion and idempotent re-ingestion.
4. **Day 4:** implement `/retrieve`, citations and a small human-labeled question set; inspect errors manually.
5. **Day 5:** thin UI for document upload, question, evidence passages, page citation, and abstention. Keep camera/gesture off this first slice.
6. **Day 6:** integrate a single authored diagram's configured component regions with concept IDs and retrieval; add accessible click fallback.
7. **Day 7:** document setup, manually run acceptance scenarios, measure latency and retrieval baseline, review privacy/license issues, record open questions.

This is a first learning slice, not a production-ready product.

## Ordered milestones

### Delivery status and sequencing update

Semantic retrieval was delivered early as an optional capability in commit `245737d`; it does not replace the FTS5 baseline. The synthetic evaluation currently shows no measured semantic or hybrid improvement over lexical retrieval. The similarity cutoff is an operational configuration value, not a calibrated confidence threshold. This early delivery does not mean the original retrieval evaluation gate was satisfied.

The original M2 milestone, curated diagram concept selection, is now implemented as the next slice after M2.1. The dense retrieval portion of M5 has been delivered early; the local tutor experiment and human review of answers and citations remain outstanding. Milestone ordering and IDs below continue to describe the planned product sequence.

**M2 implementation update:** The first vertical slice is now implemented with an authored photosynthesis SVG, server-validated normalized rectangular regions, curated concept aliases, and evidence lookup through the existing retrieval service. Region selection uses pointer and keyboard-accessible buttons. Unsupported diagram/region IDs return safe errors, and concepts without matching local material return explicit insufficient evidence. This closes the original M2 selection milestone; camera alignment and recognition remain M3 work.

| Milestone | Depends on | Estimate | Definition of done |
|---|---|---:|---|
| M0: Environment and repository scaffold | none | 1–2 days | verified versions, project boundaries, license/data policy, run notes |
| M1: PDF → evidence retrieval vertical slice | M0 | 1–2 weeks | ingest, page provenance, FTS retrieval, citations, abstention, deletion, basic UI |
| M2: Curated diagram concept selection | M1 | 1 week | one licensed/authored diagram, configured regions, concept resolver, retrieval link, pointer fallback |
| M3: Camera alignment and interaction | M2 | 1–2 weeks | camera permission/lifecycle, normalized 2D overlays, movement/occlusion stale state, usability fallback |
| M4: Gesture baseline | M3 | 2–4 weeks | rule baseline, collection consent process, evaluated landmarks/classifier comparison and false activation report |
| M5: Dense hybrid retrieval + local tutor experiment (dense retrieval delivered early) | M1 | 2–4 weeks | local tutor experiment, human review of answers and citations, optional fallback |
| M6: Vision dataset/model pilot | M2 | 3–6 weeks | dataset card, group split, baseline, candidate model report, no promotion without criteria |
| M7: Teaching mode and progress | M1/M5 | 2–4 weeks | authored quiz rubric, response evaluation, opt-in local progress/deletion, learning task evaluation |
| M8: Multimodal retrieval | M6 | 4–8+ weeks | figure assets and captions provenance, evaluated visual retrieval, clear text/image capability boundary |

## Risks and sequencing

The vertical slice validates ingestion, provenance, retrieval, and actual user value before detector/model work. Gesture and general vision require data collection and careful licensing, so they follow a supported diagram flow. Local LLM feasibility is an experiment, not a prerequisite. Retraining automation comes only after stable data and evaluation pipelines exist.
