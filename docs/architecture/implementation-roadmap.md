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

The original M2 milestone, curated diagram concept selection, is now implemented as the next slice after M2.1. The dense retrieval portion of M5 has been delivered early. M6 now has an optional local tutor implementation; comparative quality evaluation and human review of generated answers and citations remain outstanding. Milestone ordering and IDs below continue to describe the planned product sequence.

**M2 implementation update:** The first vertical slice is now implemented with an authored photosynthesis SVG, server-validated normalized rectangular regions, curated concept aliases, and evidence lookup through the existing retrieval service. Region selection uses pointer and keyboard-accessible buttons. Unsupported diagram/region IDs return safe errors, and concepts without matching local material return explicit insufficient evidence. This closes the original M2 selection milestone.

**M3 prototype update:** Browser camera preview is explicitly user-started and stays local. The diagram overlay has normalized regions and manual translation, scale, and rotation controls. Manual confirmation gates camera-region selection; users can mark alignment stale after movement or occlusion, which pauses region selection until reconfirmed. See `docs/development/camera-alignment.md` for assumptions and limitations.

**M4 tracking prototype update:** A local OpenCV.js ORB feature-matching and RANSAC homography proof of concept now estimates the authored planar photosynthesis diagram's overlay transform from sampled browser camera frames. It pauses region selection on tracking loss and attempts automatic reacquisition; manual alignment remains a fallback. This classical, single-reference prototype does not satisfy ADR-003's data gate for a learned/general detector and has not been validated on real camera footage. Gesture recognition remains outstanding.

**M6 tutor update:** The existing retrieval pipeline now feeds an optional local OpenAI-compatible chat-completions server bound to loopback. Generated citation labels are allow-listed against the retrieved evidence pack and converted to stored citation records; no evidence abstains without invoking the model. No weights are bundled or downloaded, and answer quality has not yet been evaluated or reviewed by humans. The tutor remains optional; the prior extractive answer endpoint is unchanged.

**Quiz/progress sequencing update (requested as M7; originally roadmap M8):** An evidence-grounded quiz and local progress slice is now implemented early without renumbering roadmap milestones. Questions are generated from retrieved material through the same optional local tutor, schema-validated and linked to stored chunk citations. Multiple-choice is scored by exact option ID; short answers use a deterministic token-overlap rubric with an explicit ambiguous outcome. SQLite retains attempt history and topic-level latest outcomes, and source deletion cascades dependent quiz citations/history. Model-generated quiz quality and the short-answer rubric still need human review; no claim of learning effectiveness is made. The original M7 vision dataset/model pilot remains in the roadmap.

**Knowledge graph sequencing update (requested as M9):** A local, evidence-backed candidate knowledge graph is implemented early as an optional capability. It extracts from stored chunks using the configured optional loopback model, stores stable canonical concepts and exact source provenance in SQLite, and labels relationships as candidates until a person explicitly verifies them. Aliases do not automatically merge concepts. Graph nodes can scope existing retrieval and tutor flows. This is a sequencing update, not completion of the roadmap's original M9 multimodal figure retrieval milestone; multimodal retrieval remains outstanding.

**M10 analytics sequencing update:** Local learning analytics summarize persisted quiz attempts, explicitly completed study sessions, and per-question review schedules. Topic mastery is a deterministic recency-weighted score over latest scored quiz outcomes (30-day half-life); ambiguous outcomes are excluded. A score below 0.60 is labeled weak only as an operational review prompt, not as a validated measure of mastery. Empty history remains empty, and source deletion removes dependent quiz attempts/reviews while independent local session records remain. M12 adds a small rule-based daily coach over this data; neither analytics nor the coach is a validated learning assessment or proof that learners mastered material. The original M9 multimodal retrieval milestone remains outstanding.

| Milestone | Depends on | Estimate | Definition of done |
|---|---|---:|---|
| M0: Environment and repository scaffold | none | 1–2 days | verified versions, project boundaries, license/data policy, run notes |
| M1: PDF → evidence retrieval vertical slice | M0 | 1–2 weeks | ingest, page provenance, FTS retrieval, citations, abstention, deletion, basic UI |
| M2: Curated diagram concept selection | M1 | 1 week | one licensed/authored diagram, configured regions, concept resolver, retrieval link, pointer fallback |
| M3: Camera alignment and interaction (manual prototype implemented) | M2 | 1–2 weeks | camera permission/lifecycle, normalized 2D overlays, movement/occlusion stale state, usability fallback |
| M4: Automatic diagram tracking prototype (implemented; bounded POC) | M3 | 1–2 weeks | local known-image features, planar alignment estimate, tracking-loss state, manual fallback, privacy behavior |
| M5: Gesture baseline | M3 | 2–4 weeks | rule baseline, collection consent process, evaluated landmarks/classifier comparison and false activation report |
| M6: Dense hybrid retrieval + local tutor experiment (dense retrieval delivered early; tutor prototype implemented) | M1 | 2–4 weeks | evaluate local tutor quality, human review of answers and citations, optional fallback |
| M7: Vision dataset/model pilot | M2 | 3–6 weeks | dataset card, group split, baseline, candidate model report, no promotion without criteria |
| M8: Teaching mode and progress | M1/M6 | 2–4 weeks | authored quiz rubric, response evaluation, opt-in local progress/deletion, learning task evaluation |
| M9: Multimodal retrieval (still outstanding; knowledge graph delivered early as an optional capability) | M7 | 4–8+ weeks | figure assets and captions provenance, evaluated visual retrieval, clear text/image capability boundary |
| M12: Personalized AI study coach (rule-based daily plan implemented) | M8/M10 | 1–2 weeks | use persisted progress for explainable priorities, source links, learner decisions, and evaluated usefulness |

**M10 delivery note:** The local dashboard now reports persisted quiz attempts, completed study sessions, mastery summaries, and due reviews. This is a minimal analytics/review surface rather than an adaptive planner; the deterministic rules still need learner-task evaluation.

**M11 image-learning update:** Learners can store local PNG/JPEG material, mark normalized image regions, optionally map them to curated concepts, retrieve indexed document passages, and ask the existing grounded tutor. Optional local Tesseract OCR reports unavailable/empty/failure states explicitly. This bounded upload-and-region workflow does not complete the original M9 goal of evaluated multimodal figure retrieval; no image embedding or image-to-image retrieval is introduced. Camera frames remain a separate local preview/tracking path and are not stored.

**M12 study-coach update:** The local coach creates at most three persisted recommendations per UTC day from actual quiz/review records, completed same-day sessions, and linked knowledge-graph relationships. A deterministic priority heuristic orders due reviews and weak topics; reasons name the underlying saved facts. Each recommendation links to current quiz citations and a quiz action, with a graph link when its topic maps unambiguously. Learners can accept, edit the title/duration, dismiss, and complete items. The optional local tutor can explain a recommendation only from its stored citation passages. Empty history stays empty; deleted source material removes dependent activity and the coach omits recommendations without live citations. This priority formula is an operational heuristic, not validated pedagogy or a learner diagnosis; usefulness evaluation remains outstanding.

**M13 release-integration note:** Commit `110ed63` adds a scripted API regression journey covering PDF upload, retrieval, curated diagram evidence, mocked tutor-backed quiz generation, answer submission, completed study session, coach recommendations, analytics, and source deletion. This demonstrates local API integration under deterministic fixtures; it does not constitute browser automation, real tutor quality evaluation, or a user study.

**M14 evaluation note:** A versioned synthetic retrieval benchmark now checks retrieval ranks, source provenance, and empty-evidence abstention, while deterministic regression tests cover tutor/quiz safety contracts and the M13 local API journey. Reports distinguish fixture results from real-world evaluation. No tutor answer-quality, quiz learning validity, camera robustness, or learning outcome claim is made. See `docs/architecture/evaluation-plan.md` for commands, report format, opt-in local model comparison, and metric conventions.

**M23 authentication and isolation update:** Local account registration, login, logout, and session retrieval use Argon2id password hashes, opaque digest-backed HttpOnly sessions, CSRF validation, bounded requests, and default-on rate limits. Protected routes pass the verified session owner into tenant-scoped storage. SQLite migrations preserve legacy rows as unowned and stop on conflicting ownership rather than exposing or deleting data. The auth gate and session-expiration handling are wired into the browser UI. See `docs/security.md` for the design, migration behavior, production requirements, and remaining deployment risks. This milestone is not authorization to deploy publicly.

**M25 backup and recovery update:** A local CLI creates atomic, checksummed archives from SQLite's online backup API, validates database and schema integrity, restores only to a new destination, and applies directory-confined retention. Archives include database-backed content and indexes but exclude active sessions, environment secrets, and regenerable model caches. Restore, privacy, retention, and manual live-database recovery steps are documented in `docs/deployment.md`.

**M32 feedback-analysis update:** A feedback analysis and product improvement loop is built on top of the M31 private-beta feedback system. Administrators can filter and sort feedback by category, status, and date range; transition feedback through an expanded review lifecycle (`new`, `reviewing`, `planned`, `in_progress`, `resolved`, `withdrawn`, `declined`); record resolution notes; and convert feedback into tracked improvement tasks. Improvement tasks carry a transparent priority score calculated from a documented formula that blends measured data (report count of linked feedback) with administrator estimates (severity, impact, reproducibility, workflow frequency, implementation effort). Every admin operation enforces server-side authorization; no external AI services are called. Tasks can be linked to supporting feedback, marked implemented with a regression test reference, and updated with priority recomputation. See `docs/development/feedback-analysis.md` for the prioritization formula, admin workflow, and verification results.

## Risks and sequencing

The vertical slice validates ingestion, provenance, retrieval, and actual user value before detector/model work. Gesture and general vision require data collection and careful licensing, so they follow a supported diagram flow. Local LLM feasibility is an experiment, not a prerequisite. Retraining automation comes only after stable data and evaluation pipelines exist.
