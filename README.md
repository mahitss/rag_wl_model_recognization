# WonderLearn

WonderLearn is a planned, local-first visual learning assistant. Its intended workflow connects camera-based study-material understanding with component selection, retrieval from a learner's own documents, and evidence-grounded explanations.

The repository contains the architecture blueprint, M0 local development foundation, and M1 local PDF ingestion with evidence retrieval. M1 indexes extracted PDF text locally with SQLite FTS5 and returns cited excerpts; it does not provide generated model inference, OCR, semantic search, or learning features, and no performance results are claimed.

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

M1: local PDF ingestion and evidence retrieval. See [Windows setup and run instructions](docs/development/setup.md), the [API contract proposal](docs/architecture/api-contracts.md), the [roadmap](docs/architecture/implementation-roadmap.md), and [open decisions](docs/architecture/architecture-decisions.md). Answers are extractive evidence bundles with explicit abstention, not LLM-generated explanations.
