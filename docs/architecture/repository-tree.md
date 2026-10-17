# Proposed repository tree

```text
wonderlearn/
├── README.md
├── docs/
│   ├── architecture/          # requirements, architecture, decisions, plans
│   ├── data/                  # dataset cards, annotation guide, privacy notes
│   └── development/           # setup and contributor learning notes
├── apps/
│   └── web/                   # React + TypeScript UI, camera and 2D overlays
├── services/
│   └── api/                   # FastAPI modular monolith
│       ├── app/api/v1/        # routes and boundary schemas
│       ├── app/ingestion/     # file validation, extraction, chunking
│       ├── app/retrieval/     # FTS, optional vectors, fusion
│       ├── app/tutor/         # evidence policy and optional generator
│       ├── app/vision/        # detector interfaces and inference adapters
│       └── app/storage/       # SQLite schema/repositories/migrations
├── packages/
│   └── contracts/             # shared API types/schemas if justified
├── ml/
│   ├── gesture/               # collection, preprocessing, train, evaluate
│   ├── vision/                # annotation format, train, evaluate
│   ├── retrieval/             # retrieval judgments and experiments
│   └── notebooks/             # resumable, reproducible notebook experiments
├── data/
│   ├── manifests/              # rights-cleared dataset manifests only
│   ├── raw/                    # ignored user/source data
│   └── processed/              # ignored derived data
├── artifacts/                 # ignored model checkpoints and indexes
├── evaluations/               # reports, configs, approved test outputs
└── scripts/                   # data validation, export, cleanup utilities
```

Do not create this application tree until implementation begins. Ignore raw/processed datasets, uploaded documents, local databases, secrets, model weights and generated artifacts. Keep a small, rights-cleared fixture corpus only if redistribution terms allow it.
