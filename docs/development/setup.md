# Local setup (Windows PowerShell)

M1 adds local PDF ingestion, page-aware chunking, SQLite FTS5 retrieval, and an evidence-only answer endpoint to the M0 API. It does **not** implement OCR, semantic search, generated model inference, a tutor, or camera processing. Nothing downloads a dataset/model or calls a paid service.

## Verified toolchain and pinned packages

The host reports Python 3.13.5, Node.js 24.11.0, npm 11.6.0, Git 2.52.0, and uv 0.12.15. `uv` selected Python 3.12.13 for the locked project environment. The backend declares Python `>=3.11,<3.14`, FastAPI 0.143.3, pydantic-settings 2.16.0, Uvicorn 0.54.0, pypdf 6.21.1 (BSD-3-Clause), and python-multipart 0.0.32. Development tools are pytest 9.1.1, HTTPX 0.28.1, and Ruff 0.17.1. Frontend dependencies are pinned exactly in `apps/web/package.json` and `package-lock.json` (React 19.3.1, Vite 8.3.4, TypeScript 7.0.2). `uv.lock` and `package-lock.json` capture resolved dependency trees.

Vite 8 requires a supported modern Node runtime; use an even-numbered Node LTS release compatible with Vite 8. This workspace was built with Node 24. Official release references: [FastAPI release notes](https://fastapi.tiangolo.com/release-notes/), [React versions](https://react.dev/versions), and [Vite supported versions](https://vite.dev/releases/).

## One-time setup

Run these commands from the repository root:

```powershell
Copy-Item .env.example .env
uv sync --project services/api --extra dev
npm ci --prefix apps/web
```

The `.env` file contains local path/origin configuration only. Do not put secrets in it or commit it.

## Run locally

Open two PowerShell terminals at the repository root.

Terminal 1, API:

```powershell
uv run --project services/api uvicorn app.main:app --app-dir services/api --host 127.0.0.1 --port 8000
```

Terminal 2, frontend:

```powershell
npm run dev --prefix apps/web
```

Open `http://127.0.0.1:5173`. API health is `http://127.0.0.1:8000/api/v1/health`; readiness is `http://127.0.0.1:8000/api/v1/ready`; interactive OpenAPI docs are at `http://127.0.0.1:8000/docs`.

Health reports process liveness and explicitly marks planned capabilities false. Readiness opens the configured SQLite database, runs `PRAGMA quick_check`, and verifies FTS5 can create a temporary virtual table. The default database path is `data/local/wonderlearn.sqlite3`; it is local and ignored by Git. Readiness returning HTTP 503 means a real storage prerequisite failed.

## Checks

From the repository root:

```powershell
uv run --project services/api --extra dev pytest
uv run --project services/api --extra dev ruff check services/api
npm run typecheck --prefix apps/web
npm run build --prefix apps/web
```

## Configuration

Settings use the `WL_` prefix and load `.env` from the current working directory. Supported settings:

- `WL_DATABASE_PATH`: SQLite file path. Default: `data/local/wonderlearn.sqlite3`.
- `WL_CORS_ORIGINS`: comma-separated local HTTP origins. Default allows Vite at localhost and 127.0.0.1 port 5173. Non-local origins are rejected.
- `WL_MAX_UPLOAD_BYTES`: maximum PDF upload size. Default: 10485760 (10 MiB).
- `WL_MAX_PDF_PAGES`: maximum page count. Default: 250.
- `WL_MAX_EXTRACTED_CHARS`: maximum extracted text characters per PDF. Default: 5000000.
- `WL_CHUNK_SIZE_CHARS`: maximum chunk length. Default: 2000 characters.
- `WL_CHUNK_OVERLAP_CHARS`: overlap between chunks on one page. Default: 200 characters.
- `WL_MAX_RETRIEVAL_RESULTS`: maximum search results accepted per request. Default: 10.

## M1 local PDF workflow

Start the API and use another PowerShell window. Replace `C:\path\to\lesson.pdf` with a local PDF path:

```powershell
$upload = curl.exe -sS -X POST http://127.0.0.1:8000/api/v1/documents -F 'file=@C:\path\to\lesson.pdf;type=application/pdf' | ConvertFrom-Json
$documentId = $upload.documentId
$upload | ConvertTo-Json -Depth 5
Invoke-RestMethod http://127.0.0.1:8000/api/v1/documents | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/retrieve -ContentType 'application/json' -Body '{"query":"evaporation","limit":5}' | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/answers -ContentType 'application/json' -Body '{"question":"How does evaporation work?","limit":5}' | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Delete -Uri "http://127.0.0.1:8000/api/v1/documents/$documentId"
```

`POST /api/v1/documents` extracts text with pypdf, chunks each page deterministically, stores document metadata and text in SQLite, and inserts FTS5 index rows in one transaction. Original PDFs are not retained. Duplicate file bytes return HTTP 409 with the existing document ID. Unsupported, malformed, encrypted, empty, or textless/scanned PDFs are rejected; OCR is not available. Limits are configurable above.

`POST /api/v1/retrieve` uses SQLite FTS5 lexical OR matching over up to 32 normalized query tokens and ranks matches with BM25. Results include the exact stored chunk and page provenance. It is lexical retrieval, not semantic search. `POST /api/v1/answers` packages the retrieved excerpts verbatim with their citations; when no chunks match, it returns `insufficient_evidence` and a null answer. It does not synthesize an explanation or call a model.

## Project locations

- `apps/web`: minimal React/TypeScript shell.
- `services/api`: FastAPI modular-monolith boundary and system routes.
- `ml/notebooks`: future training experiments; no training code in M0.
- `data/manifests`: dataset provenance metadata only.
- `evaluations`: future evaluation material; no model results claimed.
- `scripts`: local maintenance and setup helpers.
- `tests`: reserved for cross-component checks; API tests currently live beside the service in `services/api/tests`.

The service's future retrieval module belongs under `services/api/app/retrieval`, matching the architecture tree. No separate retrieval service or fake RAG package is created.
