# WonderLearn

WonderLearn is a planned, local-first visual learning assistant. Its intended workflow connects camera-based study-material understanding with component selection, retrieval from a learner's own documents, and evidence-grounded explanations.

The repository contains the architecture blueprint, M0 local development foundation, M1 PDF ingestion with SQLite FTS5, and M2 optional local semantic retrieval. M2 adds CPU sentence embeddings and hybrid retrieval while retaining lexical-only operation; there is still no generated model inference, OCR, or learning feature, and the small synthetic evaluation is not a product-quality claim.

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

M2: optional local semantic retrieval and embeddings. See [Windows setup and run instructions](docs/development/setup.md), the [semantic retrieval guide and evaluation](docs/development/semantic-retrieval.md), the [API contract proposal](docs/architecture/api-contracts.md), the [roadmap](docs/architecture/implementation-roadmap.md), and [open decisions](docs/architecture/architecture-decisions.md). Answers remain extractive evidence bundles with explicit abstention, not LLM-generated explanations.
