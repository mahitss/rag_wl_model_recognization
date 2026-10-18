# WonderLearn

WonderLearn is a planned, local-first visual learning assistant. Its intended workflow connects camera-based study-material understanding with component selection, retrieval from a learner's own documents, and evidence-grounded explanations.

The repository contains the architecture blueprint, M0 local development foundation, M1 PDF ingestion with SQLite FTS5, optional M2 semantic retrieval, the curated M2 photosynthesis diagram selection slice, an M3 local camera preview with manual alignment, and an M4 browser-side planar diagram-tracking proof of concept. Diagram regions resolve through server-owned concepts and the existing evidence retrieval service. Automatic tracking is limited to the known authored diagram and has not been validated on real camera footage; generated model inference and OCR are not implemented. The small synthetic retrieval evaluation is not a product-quality claim.

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

M3 camera preview and manual diagram alignment prototype. M4 adds optional local ORB feature matching for the authored photosynthesis diagram. M2 includes optional local semantic retrieval and accessible diagram-region selection with cited evidence. See [Windows setup and run instructions](docs/development/setup.md), the [camera alignment and tracking guide](docs/development/camera-alignment.md), the [semantic retrieval guide and evaluation](docs/development/semantic-retrieval.md), the [API contracts](docs/architecture/api-contracts.md), the [roadmap](docs/architecture/implementation-roadmap.md), and [open decisions](docs/architecture/architecture-decisions.md). Answers remain evidence bundles with explicit abstention, not LLM-generated explanations.
