# WonderLearn

WonderLearn is a planned, local-first visual learning assistant. Its intended workflow connects camera-based study-material understanding with component selection, retrieval from a learner's own documents, and evidence-grounded explanations.

The repository contains the local development foundation, PDF ingestion with SQLite FTS5, optional semantic retrieval, curated photosynthesis diagram selection, browser-side camera alignment/tracking, an optional evidence-grounded tutor, evidence-backed quizzes with local learning progress, and an optional SQLite knowledge graph with source-linked candidate relationships. Diagram concepts, graph relationships, tutor citations, and quiz citations resolve through stored evidence provenance. Automatic tracking is limited to the known authored diagram and has not been validated on real camera footage. No tutor model is bundled or downloaded, and OCR is not implemented. The small synthetic retrieval evaluation is not a product-quality claim; quiz quality, graph extraction, and short-answer scoring need human review.

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

M6 adds an optional local generated explanation path alongside the unchanged extractive `/answers` endpoint. The requested M7 quiz/progress slice (originally roadmap M8) adds evidence-backed quiz generation, local attempt history, and topic-level review. The requested M9 knowledge graph is delivered as an optional early capability; the roadmap's original M9 multimodal retrieval and a separate study-planner experience remain outstanding. See [Windows setup and run instructions](docs/development/setup.md), including optional tutor, quiz, and graph setup, the [camera alignment and tracking guide](docs/development/camera-alignment.md), the [semantic retrieval guide and evaluation](docs/development/semantic-retrieval.md), the [API contracts](docs/architecture/api-contracts.md), the [roadmap](docs/architecture/implementation-roadmap.md), and [open decisions](docs/architecture/architecture-decisions.md). Tutor/quiz quality, graph extraction, and short-answer scoring still require human review.
