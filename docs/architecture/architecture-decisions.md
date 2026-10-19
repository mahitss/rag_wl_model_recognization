# Architecture decisions and open questions

## Accepted provisional decisions

### ADR-001: Local-first modular monolith

**Decision:** React/TypeScript browser client plus FastAPI modular monolith, SQLite storage, no paid/cloud dependency.

**Alternatives:** hosted backend, microservices, managed vector database.

**Reason:** strict ₹0 budget, single-developer learning context, local privacy, low operational burden. Revisit if multi-user collaboration or deployment needs become concrete.

### ADR-002: Evidence retrieval before generative tutor

**Decision:** build PDF provenance and FTS5 retrieval first; use evidence cards/templates and explicit abstention. Add local LLM only as a measured optional adapter.

**Alternatives:** hosted LLM/API, immediate local generative model, fine-tuned tutor.

**Reason:** avoids paid dependency and hardware assumption, makes citation behavior verifiable, validates retrieval value independently.

**Implementation update (M6):** An optional loopback-only local chat-completion adapter now generates explanations after retrieval. Retrieved evidence remains the sole supplied source, model citation labels are allow-listed against the retrieved set, and empty evidence produces explicit abstention before model invocation. This is an implementation of the optional adapter, not a change to the evidence-first decision. The adapter is not required for API operation; human review of answer quality and citations remains outstanding, so the tutor is an experiment rather than a validated learning capability.

### ADR-006: Local evidence-backed quiz and progress records

**Decision:** generate quiz items only from retrieved local passages through the existing optional loopback tutor; validate schemas and retrieved citation IDs; score multiple-choice exactly and short answers with a conservative deterministic lexical rubric that preserves ambiguous outcomes. Store quiz questions, source chunk references, attempts, and derived topic progress in SQLite on the learner's device.

**Alternatives:** hosted quiz generation, model-judged correctness, server-side learner accounts, ungrounded authored templates.

**Reason:** preserve the local-first and evidence-first boundary, avoid paid services and model training, and avoid claiming certainty for semantically ambiguous short answers. Source document deletion cascades dependent quiz and attempt data. Human review of generated educational quality and the short-answer rubric remains necessary.

**Sequencing note:** The request labels this work M7; the existing roadmap assigns the quiz/progress product work to M8 after the vision dataset pilot. This implementation delivers that product slice early and leaves roadmap numbering unchanged.

### ADR-003: Curated diagram component selection first

**Decision:** begin with configured regions and pointer selection on one supported diagram family; advance to detection only after licensed annotated data exists.

**Alternatives:** generic detector, segmentation, open-world classification.

**Reason:** localization and concept mapping are the core useful task; constrained labels make training and rejection evaluation tractable.

**Implementation note (M4):** A classical ORB feature-matching/homography proof of concept now tracks only the existing authored planar photosynthesis diagram. It uses no learned detector and does not satisfy the annotated-data, held-out evaluation, or promotion gates above. Broader detector work remains deferred until those gates can be met.

### ADR-004: Browser hand landmarks, learned gesture later

**Decision:** MediaPipe landmark extraction with rules baseline; custom classifier only after a consented, diverse dataset and evaluation plan exist.

**Alternatives:** train end-to-end video model; use generic gesture model as product capability.

**Reason:** landmarks reduce data requirements and are inspectable. Model/checkpoint licenses still need review.

### ADR-005: FTS5 first, dense retrieval optional

**Decision:** SQLite FTS5 lexical baseline; add local embeddings and FAISS only if labeled retrieval evaluation demonstrates need and Windows packaging is verified.

**Alternatives:** vector database from day one, cloud embeddings.

**Reason:** smallest reliable local stack; FTS5 provides keyword search and BM25 ranking.

**Implementation update (M2.1):** Semantic retrieval was delivered early in commit `245737d` as an optional local capability while retaining FTS5. The small synthetic comparison measured identical lexical, semantic, and hybrid metrics, so it did not demonstrate the need specified by this ADR. The delivery records a sequencing exception; it does not satisfy or remove the evaluation gate for making dense retrieval the preferred or mandatory path. The existing similarity cutoff is operational configuration, not calibrated confidence.

## Open decisions for founder

1. Is the intended product ultimately fully open source, or should the architecture preserve the possibility of proprietary distribution? This affects AGPL choices and model licensing.
2. Which first learner age range, language(s), and subject/diagram family should anchor the dataset and UX? Use one narrowly scoped family for the first slice.
3. Should user documents remain strictly local and unbacked-up, or is an explicit user-managed backup/export workflow required?
4. Is an internet connection acceptable for one-time user-initiated model downloads, while inference and documents remain local?
5. Should educator-provided materials be indexed only for personal study, or may later versions use them for training/evaluation? Default recommendation: never without separate documented permission.

## Revisit triggers

Revisit topology when simultaneous multi-user use is required; detector choice after annotation and benchmarks; embedding choice after retrieval judgments; local LLM after CPU latency and tutor-quality review; scanned-PDF OCR after representative PDF fixtures (M11 currently adds optional local OCR for uploaded raster images only); 3D only with a clear depth/camera calibration requirement and feasible hardware. Recheck all current dependency/model licenses and tool limits at implementation time.
