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

### ADR-003: Curated diagram component selection first

**Decision:** begin with configured regions and pointer selection on one supported diagram family; advance to detection only after licensed annotated data exists.

**Alternatives:** generic detector, segmentation, open-world classification.

**Reason:** localization and concept mapping are the core useful task; constrained labels make training and rejection evaluation tractable.

### ADR-004: Browser hand landmarks, learned gesture later

**Decision:** MediaPipe landmark extraction with rules baseline; custom classifier only after a consented, diverse dataset and evaluation plan exist.

**Alternatives:** train end-to-end video model; use generic gesture model as product capability.

**Reason:** landmarks reduce data requirements and are inspectable. Model/checkpoint licenses still need review.

### ADR-005: FTS5 first, dense retrieval optional

**Decision:** SQLite FTS5 lexical baseline; add local embeddings and FAISS only if labeled retrieval evaluation demonstrates need and Windows packaging is verified.

**Alternatives:** vector database from day one, cloud embeddings.

**Reason:** smallest reliable local stack; FTS5 provides keyword search and BM25 ranking.

## Open decisions for founder

1. Is the intended product ultimately fully open source, or should the architecture preserve the possibility of proprietary distribution? This affects AGPL choices and model licensing.
2. Which first learner age range, language(s), and subject/diagram family should anchor the dataset and UX? Use one narrowly scoped family for the first slice.
3. Should user documents remain strictly local and unbacked-up, or is an explicit user-managed backup/export workflow required?
4. Is an internet connection acceptable for one-time user-initiated model downloads, while inference and documents remain local?
5. Should educator-provided materials be indexed only for personal study, or may later versions use them for training/evaluation? Default recommendation: never without separate documented permission.

## Revisit triggers

Revisit topology when simultaneous multi-user use is required; detector choice after annotation and benchmarks; embedding choice after retrieval judgments; local LLM after CPU latency and tutor-quality review; OCR after representative scanned PDFs; 3D only with a clear depth/camera calibration requirement and feasible hardware. Recheck all current dependency/model licenses and tool limits at implementation time.
