# WonderLearn

WonderLearn is a planned, local-first visual learning assistant. Its intended workflow connects camera-based study-material understanding with component selection, retrieval from a learner's own documents, and evidence-grounded explanations.

The repository contains the local development foundation, PDF ingestion with SQLite FTS5, optional semantic retrieval, curated photosynthesis diagram selection, browser-side camera alignment/tracking, and an optional evidence-grounded tutor connected to a loopback model server. Diagram concepts and tutor citations resolve through stored evidence provenance. Automatic tracking is limited to the known authored diagram and has not been validated on real camera footage. No tutor model is bundled or downloaded, and OCR is not implemented. The small synthetic retrieval evaluation is not a product-quality claim.

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

M6 adds an optional local generated explanation path alongside the unchanged extractive `/answers` endpoint. See [Windows setup and run instructions](docs/development/setup.md), including optional tutor setup, the [camera alignment and tracking guide](docs/development/camera-alignment.md), the [semantic retrieval guide and evaluation](docs/development/semantic-retrieval.md), the [API contracts](docs/architecture/api-contracts.md), the [roadmap](docs/architecture/implementation-roadmap.md), and [open decisions](docs/architecture/architecture-decisions.md). Generated answer quality still requires human review.
