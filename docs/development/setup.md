# Local setup (Windows PowerShell)

The local application includes PDF ingestion and FTS5 retrieval, optional semantic retrieval, curated photosynthesis diagram selection, local camera tracking, and an optional evidence-grounded tutor. The tutor is disabled unless a loopback model server is configured. Nothing downloads a dataset/model automatically or calls a paid service.

## Verified toolchain and pinned packages

The host reports Python 3.13.5, Node.js 24.11.0, npm 11.6.0, Git 2.52.0, and uv 0.12.15. `uv` selected Python 3.12.13 for the locked project environment. The backend declares Python `>=3.11,<3.14`, FastAPI 0.143.3, pydantic-settings 2.16.0, Uvicorn 0.54.0, pypdf 6.21.1 (BSD-3-Clause), and python-multipart 0.0.32. The optional semantic extra pins FastEmbed 0.9.0 (Apache-2.0); it uses CPU ONNX Runtime and does not install PyTorch. Development tools are pytest 9.1.1, HTTPX 0.28.1, and Ruff 0.17.1. Frontend dependencies are pinned exactly in `apps/web/package.json` and `package-lock.json` (React 19.3.1, Vite 8.3.4, TypeScript 7.0.2). `uv.lock` and `package-lock.json` capture resolved dependency trees.

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

## Curated diagram workflow

Open the frontend and choose a labeled region in the authored photosynthesis diagram. The outlined region buttons work with a pointer and keyboard; the region list below the image is an additional accessible control. Selection resolves a server-owned concept and searches the indexed local PDFs through the same retrieval service used by `/retrieve`. Results show the stored filename, page, exact passage, and citation/chunk ID. With no matching evidence, the UI says so and shows no citation. Upload permitted PDFs through the existing `/api/v1/documents` endpoint before selecting concepts. The only current diagram is `photosynthesis-cycle`; arbitrary client queries, diagram IDs, document filters, and fabricated source metadata are not accepted by this flow.

Diagram geometry uses normalized image-relative rectangles: each `{x, y, width, height}` value is between 0 and 1, and the frontend positions buttons as percentages over the same aspect-ratio-preserving image stage.

## Checks

From the repository root:

```powershell
uv run --project services/api --extra dev pytest
uv run --project services/api --extra dev ruff check services/api
npm test --prefix apps/web
npm run typecheck --prefix apps/web
npm run build --prefix apps/web
```

The M3 camera preview and manual overlay prototype uses no service or model. See the [camera alignment guide](camera-alignment.md) for its privacy behavior, controls, and alignment limitations.

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
- `WL_RETRIEVAL_MODE`: default API retrieval mode: `lexical`, `semantic`, or `hybrid`. Default: `lexical`.
- `WL_EMBEDDING_MODEL_ID` and `WL_EMBEDDING_MODEL_REVISION`: pinned model and upstream revision. See the [semantic retrieval guide](semantic-retrieval.md).
- `WL_EMBEDDING_DIMENSIONS`: expected vector size. Default: 384.
- `WL_EMBEDDING_BATCH_SIZE`: chunks per encode batch. Default: 32.
- `WL_EMBEDDING_CACHE_PATH`: local model cache; default `artifacts/embedding-models` is ignored by Git.
- `WL_LEXICAL_CANDIDATE_LIMIT`, `WL_SEMANTIC_CANDIDATE_LIMIT`: per-retriever candidate limits. Default: 50 each.
- `WL_RRF_K`: reciprocal-rank-fusion constant. Default: 60.
- `WL_SEMANTIC_MIN_SIMILARITY`: cosine cutoff used to abstain from weak semantic matches. Default: 0.60; this initial cutoff is based on a small synthetic set and is not a factual-confidence score.

Lexical retrieval works with the default setup. To install optional semantic runtime support and explicitly cache its model, follow the [semantic retrieval guide](semantic-retrieval.md). No model is downloaded by normal setup or API startup.

## Optional local AI tutor

The tutor is disabled by default and adds no Python or Node dependency. It connects only to `localhost`, `127.0.0.1`, or `::1` over HTTP using the OpenAI-compatible chat-completions API. The API sends the question and retrieved local passages to that loopback endpoint; camera frames are not involved. The model is instructed to answer only from evidence and use inline `[E1]` citation labels. The API maps labels to retrieved passage records and rejects unknown or malformed citations. Existing `/api/v1/answers` remains extractive.

To enable it, install/run a free local inference server such as `llama.cpp`'s `llama-server` and choose a GGUF instruct model whose license you have reviewed. Model downloads are manual and user initiated. Example PowerShell command (adjust the local model path and alias):

```powershell
llama-server -m C:\models\your-instruct-model.gguf --host 127.0.0.1 --port 8080 --alias local-model
```

Set these values in `.env`, then restart the API:

```dotenv
WL_TUTOR_BASE_URL=http://127.0.0.1:8080/v1
WL_TUTOR_MODEL_ID=local-model
WL_TUTOR_TIMEOUT_SECONDS=90
WL_TUTOR_MAX_TOKENS=700
```

The API validates that the URL is loopback-only; it does not send study material to remote hosts. `GET /api/v1/tutor/status` reports configuration without exposing paths or secrets. With a small 1Bâ€“3B quantized GGUF, plan for roughly 1â€“3 GB of model storage and several additional GB of RAM depending on context and runtime; speed and answer quality depend heavily on hardware and model. No model is bundled, downloaded, or trained by this project. Verify upstream model licenses before use. With no server configured or reachable, the UI reports the tutor as unavailable while retrieval and citation browsing remain usable.

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

`POST /api/v1/retrieve` defaults to SQLite FTS5 lexical OR matching over up to 32 query tokens after common English stop words are removed; FTS5 BM25 ranks matches. Optional semantic or hybrid modes are described in the [semantic retrieval guide](semantic-retrieval.md). Results include the exact stored chunk and page provenance. `POST /api/v1/answers` packages retrieved excerpts verbatim with their citations; semantic results below the configured cutoff abstain, and lexical retrieval abstains when it finds no matches. It does not synthesize an explanation or call a model.

## Project locations

- `apps/web`: React/TypeScript diagram selection and evidence interface; the authored SVG is under `public/diagrams`.
- `services/api`: FastAPI modular-monolith boundary and system routes.
- `ml/notebooks`: future training experiments; no training code in M0.
- `data/manifests`: dataset provenance metadata only.
- `evaluations`: future evaluation material; no model results claimed.
- `scripts`: local maintenance and setup helpers.
- `tests`: reserved for cross-component checks; API tests currently live beside the service in `services/api/tests`.

The service's future retrieval module belongs under `services/api/app/retrieval`, matching the architecture tree. No separate retrieval service or fake RAG package is created.
