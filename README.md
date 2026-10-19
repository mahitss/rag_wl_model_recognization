# WonderLearn

WonderLearn is a planned, local-first visual learning assistant. Its intended workflow connects camera-based study-material understanding with component selection, retrieval from a learner's own documents, and evidence-grounded explanations.

The repository contains the local development foundation, PDF ingestion with SQLite FTS5, optional semantic retrieval, curated photosynthesis diagram selection, browser-side camera alignment/tracking, an optional evidence-grounded tutor, evidence-backed quizzes and local analytics, an optional SQLite knowledge graph, local educational image upload with region selection and optional Tesseract OCR, and a deterministic daily study coach. Image bytes and derived regions remain in the local SQLite database; camera frames are never persisted. Diagram concepts, graph relationships, tutor citations, quiz/image-region citations, and study-plan passages resolve through server-side evidence provenance. Automatic tracking is limited to the known authored diagram and has not been validated on real camera footage. No tutor model is bundled or downloaded. The study coach's priority formula is an operational heuristic and has not been evaluated for learning outcomes. The small synthetic retrieval evaluation is not a product-quality claim; quiz quality, graph extraction, and short-answer scoring need human review.

## Architecture

- [Product requirements](docs/architecture/product-requirements.md)
- [System architecture](docs/architecture/system-architecture.md)
- [Model strategy](docs/architecture/model-strategy.md)
- [RAG architecture](docs/architecture/rag-architecture.md)
- [Data strategy](docs/architecture/data-strategy.md)
- [API contracts](docs/architecture/api-contracts.md)
- [Evaluation plan](docs/architecture/evaluation-plan.md)
- [Implementation roadmap](docs/architecture/implementation-roadmap.md)
- [Risk register](docs/architecture/risk-register.md)
- [Architecture decisions](docs/architecture/architecture-decisions.md)
- [Proposed repository tree](docs/architecture/repository-tree.md)

## Current phase

M6 adds an optional local generated explanation path alongside the unchanged extractive `/answers` endpoint. The requested M7 quiz/progress slice (originally roadmap M8) adds evidence-backed quiz generation, local attempt history, and topic-level review. The requested M9 knowledge graph is delivered as an optional early capability. M10 adds local analytics over persisted quiz attempts, explicitly completed study sessions, and review reminders; its mastery score is a deterministic reporting rule, not a validated learning measure. M11 adds local image upload and region OCR. M12 adds a source-linked rule-based daily study coach with optional local tutor explanations. The roadmap's original M9 evaluated multimodal retrieval remains outstanding. See [Windows setup and run instructions](docs/development/setup.md), including optional tutor, quiz, graph, and OCR setup, the [camera alignment and tracking guide](docs/development/camera-alignment.md), the [semantic retrieval guide and evaluation](docs/development/semantic-retrieval.md), the [API contracts](docs/architecture/api-contracts.md), the [roadmap](docs/architecture/implementation-roadmap.md), and [open decisions](docs/architecture/architecture-decisions.md). Tutor/quiz quality, graph extraction, short-answer scoring, and study-coach usefulness still require human review.
