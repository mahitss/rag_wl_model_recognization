# WonderLearn M15 — Production Deployment & Operations Readiness

This document describes how to deploy and operate WonderLearn in production
using free-tier and open-source tooling.  There is **no automatic deployment**
— CI runs tests and linting only.  Deploy manually after reviewing this
document and the configuration.

## Architecture summary (unchanged)

- **Backend:** FastAPI + uvicorn, single-process, SQLite database file.
- **Frontend:** React + Vite, static build, served from the same origin as the
  API or behind a reverse proxy.
- **Database:** One SQLite file (`WL_DATABASE_PATH`). No external database or
  managed/cloud backup service; the local SQLite backup CLI is documented below.
- **Models:** Optional FastEmbed semantic model cached at
  `WL_EMBEDDING_CACHE_PATH` (~67 MB).  Optional tutor connects to a loopback
  OpenAI-compatible server (`WL_TUTOR_BASE_URL`); no model is bundled or
  downloaded automatically.
- **OCR:** Optional Tesseract executable on `PATH`; not installed by the image
  by default (the Dockerfile includes `tesseract-ocr`).
- **Camera:** Browser-side only; frames are never persisted or sent to the API.

---

## 1. Local setup commands

Run from the repository root:

```powershell
# 1. Copy environment templates (do not commit .env)
Copy-Item .env.example .env
Copy-Item apps/web/.env.example apps/web/.env.local

# 2. Install backend dependencies
uv sync --project services/api --extra dev --extra ocr

# 3. Install frontend dependencies
npm ci --prefix apps/web

# 4. Optionally download the semantic embedding model
uv run --project services/api --extra semantic python app/embeddings/download_model.py

# 5. Start the API (terminal 1)
uv run --project services/api uvicorn app.main:app --app-dir services/api --host 127.0.0.1 --port 8000

# 6. Start the frontend (terminal 2)
npm run dev --prefix apps/web
```

Open `http://127.0.0.1:5173`.  Health: `http://127.0.0.1:8000/api/v1/health`.
Readiness: `http://127.0.0.1:8000/api/v1/ready`.

---

## 2. Environment-variable reference

All backend variables use the `WL_` prefix.  Copy `.env.example` to `.env`
and override as needed.  `.env` is gitignored — never commit it.

| Variable | Default | Description |
|---|---|---|
| `WL_ENVIRONMENT` | `development` | `development` or `production`. Affects CORS rules and startup warnings. |
| `WL_RATE_LIMIT_ENABLED` | `true` | Must remain enabled in production. Limits are per process and reset on restart. |
| `WL_RATE_LIMIT_REQUESTS_PER_MINUTE` | `60` | General per-IP/path request budget. |
| `WL_AUTH_RATE_LIMIT_REQUESTS_PER_MINUTE` | `10` | Per-IP/path budget for authentication endpoints. |
| `WL_EXPENSIVE_RATE_LIMIT_REQUESTS_PER_MINUTE` | `10` | Per-IP/path budget for upload and expensive processing endpoints. |
| `WL_MAX_REQUEST_BODY_BYTES` | `12582912` | Maximum streamed request size (12 MiB); production requires a finite value. |
| `WL_SESSION_TTL_SECONDS` | `2592000` | Session lifetime (30 days). |
| `WL_LOG_LEVEL` | `INFO` | Structured log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `WL_API_HOST` | `127.0.0.1` | Host uvicorn binds to. Use `0.0.0.0` only behind a trusted reverse proxy. |
| `WL_API_PORT` | `8000` | Port uvicorn listens on. |
| `WL_REQUEST_TIMEOUT_SECONDS` | `30` | Request timeout hint (documented; enforced by reverse proxy). |
| `WL_DATABASE_PATH` | `data/local/wonderlearn.sqlite3` | SQLite file path. Parent directory is created at startup. |
| `WL_BACKUP_DIRECTORY` | `backups` | Local directory for private `.wlbackup` archives. |
| `WL_BACKUP_RETENTION_COUNT` | `30` | Number of newest generated archives to retain after backup creation. |
| `WL_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated allowed CORS origins. Wildcard `*` rejected. HTTP only for loopback; HTTPS required for remote origins in production. |
| `WL_MAX_UPLOAD_BYTES` | `10485760` | Max PDF upload size (bytes). |
| `WL_MAX_IMAGE_UPLOAD_BYTES` | `10485760` | Max image upload size (bytes). |
| `WL_MAX_IMAGE_DIMENSION` | `4096` | Max width/height after EXIF orientation. |
| `WL_MAX_IMAGE_PIXELS` | `16000000` | Max decoded image pixels. |
| `WL_MAX_PDF_PAGES` | `250` | Max PDF pages. |
| `WL_MAX_EXTRACTED_CHARS` | `5000000` | Max extracted characters per PDF. |
| `WL_CHUNK_SIZE_CHARS` | `2000` | Max chunk length. |
| `WL_CHUNK_OVERLAP_CHARS` | `200` | Overlap between chunks (< chunk size). |
| `WL_MAX_RETRIEVAL_RESULTS` | `10` | Max retrieval results per request. |
| `WL_RETRIEVAL_MODE` | `lexical` | `lexical`, `semantic`, or `hybrid`. |
| `WL_EMBEDDING_MODEL_ID` | `BAAI/bge-small-en-v1.5` | FastEmbed model identifier. |
| `WL_EMBEDDING_MODEL_REVISION` | `c32e6154...` | Pinned model revision. |
| `WL_EMBEDDING_DIMENSIONS` | `384` | Expected vector size. |
| `WL_EMBEDDING_BATCH_SIZE` | `32` | Chunks per encode batch. |
| `WL_EMBEDDING_CACHE_PATH` | `artifacts/embedding-models` | Local model cache directory. |
| `WL_LEXICAL_CANDIDATE_LIMIT` | `50` | FTS5 candidate limit. |
| `WL_SEMANTIC_CANDIDATE_LIMIT` | `50` | Semantic candidate limit. |
| `WL_RRF_K` | `60` | Reciprocal-rank-fusion constant. |
| `WL_SEMANTIC_MIN_SIMILARITY` | `0.60` | Semantic abstention cutoff. |
| `WL_TUTOR_BASE_URL` | *(empty)* | Optional loopback-only tutor server URL. Disabled if empty. |
| `WL_TUTOR_MODEL_ID` | `local-model` | Tutor model alias. |
| `WL_TUTOR_TIMEOUT_SECONDS` | `90` | Tutor request timeout (1–600). |
| `WL_TUTOR_MAX_TOKENS` | `700` | Tutor max tokens (1–4096). |

Frontend variables (prefix `VITE_`, copied to `apps/web/.env.local`):

| Variable | Default | Description |
|---|---|---|
| `VITE_API_URL` | *(empty)* | Backend origin if frontend and API are not same-origin. When unset, the Vite dev proxy routes `/api` to `http://127.0.0.1:8000`. |

**Secrets:** No API keys, tokens, or credentials are required by WonderLearn.
The tutor URL is loopback-only and validated at startup.  Secrets are redacted
in structured logs; no secret should ever be placed in `.env`, frontend
bundles, or log output.

---

## 3. Production deployment instructions

### Option A: Docker (recommended)

Build and run the API container:

```bash
docker build -t wonderlearn-api .
docker run -d \
  --name wonderlearn-api \
  --env-file .env \
  -p 8000:8000 \
  -v $(pwd)/data/local:/wonderlearn/data/local \
  -v $(pwd)/artifacts/embedding-models:/wonderlearn/artifacts/embedding-models \
  wonderlearn-api
```

The image includes `tesseract-ocr` for optional OCR.  The container runs as
user `wonderlearn` (non-root) and exposes port 8000.  A `/api/v1/ready`
healthcheck is configured.

### Option B: Bare metal / VM

```powershell
# From the repository root
Copy-Item .env.example .env
# Edit .env for production: set WL_ENVIRONMENT=production, WL_API_HOST=0.0.0.0,
# WL_CORS_ORIGINS to your frontend HTTPS origin(s), WL_LOG_LEVEL=INFO

uv sync --project services/api --extra ocr
uv run --project services/api uvicorn app.main:app --app-dir services/api --host 0.0.0.0 --port 8000
```

**Bind to `0.0.0.0` only behind a trusted reverse proxy** (nginx, Caddy,
Cloudflare Tunnel).  The reverse proxy should:
- Terminate TLS.
- Forward `X-Forwarded-Proto` and `X-Forwarded-For`.
- Enforce request timeouts (recommend 30s).
- Serve the frontend static build (`apps/web/dist/`) from the same origin.

### Frontend in production

Build once, serve static:

```powershell
npm run build --prefix apps/web
# Output: apps/web/dist/
# Serve `apps/web/dist/` behind your reverse proxy at the same origin as the API.
```

If frontend and API are on different origins, set `VITE_API_URL` to the public
API origin and ensure `WL_CORS_ORIGINS` includes the frontend origin (HTTPS).

---

## 4. Database persistence, backups, and recovery

WonderLearn persists application records, extracted document text and chunks,
FTS5 data, embedding vectors, learning-image bytes, and related records in the
SQLite database at `WL_DATABASE_PATH`. The optional FastEmbed model cache at
`WL_EMBEDDING_CACHE_PATH` is reproducible from its pinned model configuration
and is not required to restore saved vectors or lexical retrieval.

Create a timestamped, checksummed archive while the API is running:

```powershell
uv run --project services/api python -m app.backup create
```

The command uses SQLite's online backup API, so its snapshot is consistent
with concurrent SQLite activity (unlike copying the database file while WAL
writes are active). It writes one `.wlbackup` archive under
`WL_BACKUP_DIRECTORY` and applies `WL_BACKUP_RETENTION_COUNT`. To override
settings for a one-off backup:

```powershell
uv run --project services/api python -m app.backup create `
  --database data/local/wonderlearn.sqlite3 `
  --backup-dir D:\WonderLearnBackups `
  --retention-count 14
```

On POSIX systems, the tool restricts the backup directory to mode `0700` and
archives to `0600`. On Windows, it resets the target directory/archive ACL and
grants access only to the current account SID; verify the result with
`icacls D:\WonderLearnBackups`. Run the tool under the same account that will
need to read or restore the archives. The tool does not upload backups to an
external service.

Validate an archive before relying on it:

```powershell
uv run --project services/api python -m app.backup validate `
  backups\wonderlearn-YYYYMMDDTHHMMSSZ-<uuid>.wlbackup
```

Validation checks the ZIP CRCs and exact member list, manifest, database size
and SHA-256, SQLite `quick_check`, foreign keys, and schema compatibility.
Older schema versions are accepted for the application's existing migrations;
newer/unknown versions are rejected. Active session rows are omitted from
backup snapshots, so all browser sessions must log in again after recovery.
Password hashes (not plaintext passwords), usernames, and account email values
remain in the database because they are required to preserve account ownership
and login.

### Restore and recovery

Always restore to a **new, separate path** first. Restore refuses to overwrite
any existing file, including the configured live database. A dry run validates
the archive and destination without writing a database:

```powershell
$backup = "backups\wonderlearn-YYYYMMDDTHHMMSSZ-<uuid>.wlbackup"
uv run --project services/api python -m app.backup restore $backup `
  --database data\recovery\wonderlearn.sqlite3 --dry-run
uv run --project services/api python -m app.backup restore $backup `
  --database data\recovery\wonderlearn.sqlite3
```

Then check the restored database and ownership in an isolated environment:

```powershell
uv run --project services/api python -m app.backup validate $backup
uv run --project services/api python -c "import sqlite3; c=sqlite3.connect(r'data\recovery\wonderlearn.sqlite3'); print(c.execute('PRAGMA quick_check').fetchone()[0]); print(c.execute('PRAGMA foreign_key_check').fetchall()); c.close()"
```

For recovery of the configured database, stop the API and frontend, preserve
the current database and any `-wal`/`-shm` sidecars as a separate rollback
copy, and restore the chosen archive to a new path. After validation, manually
configure `WL_DATABASE_PATH` to that restored path and start the API. Do not
replace a live database or copy a database over active SQLite sidecars. Keep
the previous database untouched until readiness and representative
user-scoped reads have been checked; roll back by stopping the API and
re-pointing `WL_DATABASE_PATH` to the preserved database. A restore rolls
application data back to the backup timestamp, so later changes may be lost.

Retention removes only direct-child files matching WonderLearn's generated
archive naming pattern in the configured backup directory. It does not follow
symlinks or delete unrelated files. Run it manually if needed:

```powershell
uv run --project services/api python -m app.backup prune `
  --backup-dir backups --retention-count 30
```

Backups contain private learner content and are not encrypted; protect the
directory and any copies. Deleting a live user/document does not erase it from
older archives. Expiration is governed by the configured retention count, and
any copied archive must be deleted separately. Uploaded PDF source files are
not retained by the current application, so only their extracted text,
metadata, chunks, and derived records can be restored. Camera frames and
environment files/secrets are not included. Re-download the pinned embedding
model if needed; tutor model files are managed outside WonderLearn and are not
included.

SQLite is still a single-file, single-host persistence design without
replication or point-in-time recovery. Keep at least one protected copy on a
different physical device for device-loss recovery; this workflow deliberately
does not configure automatic external backup services.

---

## 5. Observability

### Structured logging

Logs are emitted as single-line JSON to stderr.  Example:

```json
{"level":"INFO","logger":"wonderlearn.access","message":"request_handled","module":"middleware","request_id":"a1b2c3d4","method":"POST","path":"/api/v1/documents","status_code":201,"duration_ms":45.23,"query":""}
```

Secret-like keys (token, key, secret, password, authorization, api_key,
credential) are redacted as `[REDACTED]`.  Uploaded file contents, camera
frames, and private document text are **never logged**.

### Log level

Controlled by `WL_LOG_LEVEL` (default `INFO`).  Use `DEBUG` for troubleshooting,
`WARNING` or `ERROR` for quiet production operation.

### Health and readiness checks

| Endpoint | Purpose | Success | Failure |
|---|---|---|---|
| `GET /api/v1/health` | Liveness — process is up | HTTP 200 | HTTP 5xx |
| `GET /api/v1/ready` | Readiness — SQLite + FTS5 accessible | HTTP 200 | HTTP 503 |

Readiness runs `PRAGMA quick_check` and verifies FTS5 can create a temp table.
A 503 means a storage prerequisite failed.

### Request IDs

Every request gets an `X-Request-ID` (echoed from the inbound header or a new
UUID).  The ID appears in all access logs and error responses:

```json
{"error":{"code":"DOCUMENT_NOT_FOUND","message":"The requested document was not found.","requestId":"a1b2c3d4","details":null}}
```

Correlate logs by `request_id` to trace a request through the system.

---

## 6. Reliability behaviors

- **Timeouts:** The tutor adapter enforces `WL_TUTOR_TIMEOUT_SECONDS` (default
  90s, max 600s).  Other endpoints rely on the reverse proxy for request
  timeouts (recommend 30s).
- **Unavailable models:** If the embedding model is unavailable, uploads fall
  back to `embeddingStatus: "unavailable"`; retrieval remains lexical-only.
  `GET /api/v1/embeddings/status` reports model availability.
- **Malformed provider responses:** Tutor responses are validated against
  schema, citation-label, and lexical-support checks before storage.  Unknown
  or malformed citations are rejected.
- **Storage failures:** SQLite errors return `503 STORAGE_UNAVAILABLE` with a
  generic message (no internals exposed).  Readiness reflects storage health.
- **Graceful shutdown:** The uvicorn lifespan shuts down cleanly on SIGTERM.
  SQLite connections are opened and closed per operation, so no connection
  pool drainage is needed.

---

## 7. Resource requirements and cold start

| Resource | Minimum | Recommended |
|---|---|---|
| RAM | 512 MB | 1 GB |
| CPU | 1 vCPU | 2 vCPU |
| Disk | 1 GB (app + DB) | 5 GB (with model cache + backups) |

Cold start: first request after startup may take 1–3 s (SQLite initialization,
embedding service instantiation).  Semantic model load adds 1–5 s on first use
if not already cached.

---

## 8. Rollback and recovery

Rollback is a configuration and artifact operation, not a deployment pipeline
operation (there is no auto-deploy).

1. **Code rollback:** Redeploy the previous container image or commit.
2. **Database rollback:** Restore the SQLite file from the most recent backup
   (see §4).  Restoring an older DB may make recently uploaded documents
   unavailable — this is expected and acceptable for a single-file database.
3. **Verify:** `GET /api/v1/ready` returns 200, then smoke-test the critical
   endpoints (§9).

---

## 9. Deployment smoke-test checklist

After any deployment, run these against the production URL:

- [ ] `GET /api/v1/health` → HTTP 200, `"status": "ok"`
- [ ] `GET /api/v1/ready` → HTTP 200, `"status": "ready"`, all checks true
- [ ] `POST /api/v1/documents` with a small PDF → HTTP 201, returns `documentId`
- [ ] `POST /api/v1/retrieve` with a query → HTTP 200, returns results
- [ ] `POST /api/v1/answers` → HTTP 200, returns extractive answer
- [ ] `DELETE /api/v1/documents/{id}` → HTTP 200
- [ ] If tutor configured: `GET /api/v1/tutor/status` → reports configured
- [ ] Frontend loads and diagram selection works (same-origin or CORS allowed)
- [ ] Structured JSON logs appear on stderr (no secrets in output)

---

## 10. Costs, free-tier limitations, and unresolved blockers

### Costs

- **₹0 / $0** for self-hosted deployment on your own hardware or free-tier
  compute (e.g., Oracle Cloud Always Free, Fly.io free tier, Render free tier,
  Railway trial).  No paid APIs are used.
- Domain name and TLS certificate may incur cost depending on provider (use
  Let's Encrypt for free TLS).

### Free-tier limitations

- Free-tier compute often sleeps after inactivity, causing cold starts.
- Outbound bandwidth may be capped.
- No managed/cloud backups or automatic scheduling; operators must run the local
  backup command and protect a separate copy for device-loss recovery.
- No uptime SLA.

### Unresolved blockers (honest list)

1. **No managed database:** SQLite is single-node.  No read replicas, failover,
   or point-in-time recovery beyond file backups.
2. **No automatic deployment:** CI runs tests only.  Manual deploy required.
   Review secrets and configuration before first production deploy.
3. **Frontend/backend same-origin recommended:** If served from different
   origins, configure `VITE_API_URL` and `WL_CORS_ORIGINS` carefully.
4. **Tutor quality unreviewed:** The optional local tutor requires a separately
   installed model server (e.g., llama.cpp).  Answer quality has not been
   evaluated for learning outcomes.
5. **Camera tracking unvalidated on real footage:** The diagram tracking
   prototype works with the authored SVG; real camera footage behavior is
   unknown.
6. **No WAL mode:** SQLite rollback journal is default; concurrent write
   contention under heavy load is untested.
7. **Evaluation is synthetic:** `evaluations/run_suite.py` uses a small
   synthetic dataset; results are not product-quality claims.

---

## 11. Authorization check

**Do not deploy to production until:**
1. All checks in §9 pass against a staging-like environment.
2. `.env` contains production-safe values (no localhost-only CORS, no
   loopback-only API host unless behind a proxy).
3. You have verified the backup/restore procedure works.
4. This document has been reviewed and deployment explicitly authorized.
