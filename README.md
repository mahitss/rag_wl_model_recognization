# WonderLearn

WonderLearn is a planned, local-first visual learning assistant. Its intended workflow connects camera-based study-material understanding with component selection, retrieval from a learner's own documents, and evidence-grounded explanations.

The repository now contains the architecture blueprint and M0 development foundation. The API and frontend are scaffolds only: document ingestion, retrieval, model inference, and learning features are not implemented, and no performance results are claimed.

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

M0: reproducible local development foundation. See [Windows setup and run instructions](docs/development/setup.md), the [roadmap](docs/architecture/implementation-roadmap.md), and [open decisions](docs/architecture/architecture-decisions.md). The recommended next milestone is M1: local PDF ingestion and evidence retrieval.
