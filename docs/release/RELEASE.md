# WonderLearn Release Audit & Release Notes — M21

## Release verdict

**WonderLearn is release-ready for local-first, single-user demo/internal use.**

It is **not ready for public multi-user deployment** without an access-control layer (reverse-proxy authentication, VPN, or equivalent). The application is architected as local-first by design: there is no user account system, no cross-user isolation, and all data lives in a single local SQLite file. These are documented architectural constraints, not bugs.

**Verification basis (all green at commit `c756cba`):**
- Backend: 124 passed, 1 skipped (opt-in model integration)
- Frontend: 35 passed, 0 failed
- TypeScript typecheck: clean
- Production build (`npm run build`): successful
- Ruff lint: clean
- Git working tree: clean

---

## Clean installation and startup

Run from the repository root. Verified toolchain: Python 3.12+, Node 24 LTS, uv, Git.

### One-time setup

```powershell
# 1. Copy environment template (do not commit .env; it is gitignored)
Copy-Item .env.example .env

# 2. Install backend dependencies (dev + optional OCR extra)
uv sync --project services/api --extra dev --extra ocr

# 3. Install frontend dependencies
npm ci --prefix apps/web
```

OCR additionally requires the free Tesseract executable on `PATH` (`tesseract --version`). The tutor requires a separately installed loopback OpenAI-compatible server (e.g. `llama-server`). Neither is required for the core demo.

### Run locally

Open two PowerShell terminals at the repository root.

Terminal 1 — API:

```powershell
uv run --project services/api uvicorn app.main:app --app-dir services/api --host 127.0.0.1 --port 8000
```

Terminal 2 — frontend:

```powershell
npm run dev --prefix apps/web
```

Open `http://127.0.0.1:5173`.

- Health: `http://127.0.0.1:8000/api/v1/health`
- Readiness: `http://127.0.0.1:8000/api/v1/ready`
- OpenAPI docs: `http://127.0.0.1:8000/docs`

Readiness returning HTTP 503 means a storage prerequisite failed (check `WL_DATABASE_PATH` is writable).

### Production (Docker)

```bash
docker build -t wonderlearn-api .
docker run -d --name wonderlearn-api --env-file .env -p 8000:8000 \
  -v $(pwd)/data/local:/wonderlearn/data/local \
  -v $(pwd)/artifacts/embedding-models:/wonderlearn/artifacts/embedding-models \
  wonderlearn-api
```

Bind to `0.0.0.0` only behind a trusted reverse proxy that terminates TLS. The Docker image runs as non-root user `wonderlearn` and exposes a `/api/v1/ready` healthcheck.

---

## Environment-variable reference

Backend variables use the `WL_` prefix. Copy `.env.example` to `.env` and override. `.env` is gitignored — never commit it.

| Variable | Default | Meaning |
|---|---|---|
| `WL_ENVIRONMENT` | `development` | `development` or `production` |
| `WL_LOG_LEVEL` | `INFO` | Structured log level |
| `WL_API_HOST` | `127.0.0.1` | Bind host (use `0.0.0.0` only behind a trusted proxy) |
| `WL_API_PORT` | `8000` | Listen port |
| `WL_DATABASE_PATH` | `data/local/wonderlearn.sqlite3` | SQLite file path |
| `WL_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Allowed CORS origins (no wildcard; HTTPS for remote in prod) |
| `WL_MAX_UPLOAD_BYTES` | `10485760` | Max PDF upload (bytes) |
| `WL_MAX_IMAGE_UPLOAD_BYTES` | `10485760` | Max image upload (bytes) |
| `WL_MAX_REQUEST_BODY_BYTES` | *(unset)* | ASGI-level request body cap (recommended in production) |
| `WL_RATE_LIMIT_ENABLED` | `false` | Fixed-window rate limiting (enable in production) |
| `WL_RATE_LIMIT_REQUESTS_PER_MINUTE` | `60` | Requests per client IP + path per minute |
| `WL_RETRIEVAL_MODE` | `lexical` | `lexical` / `semantic` / `hybrid` |
| `WL_TUTOR_BASE_URL` | *(unset)* | Optional loopback-only tutor server |
| `WL_TUTOR_TIMEOUT_SECONDS` | `90` | Tutor request timeout |

Frontend variables (`VITE_` prefix, `apps/web/.env.local`):

| Variable | Default | Meaning |
|---|---|---|
| `VITE_API_URL` | *(unset)* | Backend origin if not same-origin; Vite dev proxy handles `http://127.0.0.1:8000` when unset |

No API keys, tokens, or credentials are required. The tutor URL is loopback-only and validated at startup. Secrets are redacted in structured logs.

---

## Verified feature list

The following features are implemented and covered by automated tests:

1. **PDF ingestion** — pypdf extraction, per-page deterministic chunking, FTS5 + BM25 retrieval, SHA-256 deduplication, encrypted/empty/malformed rejection, page/text limits.
2. **Retrieval & answers** — lexical (default), optional semantic/hybrid; extractive verbatim answers with citations; honest abstention when no evidence.
3. **Diagram exploration** — authored photosynthesis diagram with normalized regions, keyboard-accessible region selection, evidence provenance.
4. **Camera alignment/tracking prototype** — browser-side preview, manual translation/scale/rotation, local ORB tracking prototype (not validated on real footage; frames never persisted).
5. **Image upload & regions** — PNG/JPEG upload, signature + size/dimension/pixel validation, decompression-bomb protection, normalized region selection, optional local Tesseract OCR, region evidence + tutor.
6. **Grounded tutor** — optional loopback OpenAI-compatible server; citation allow-listing; malformed-response rejection; abstention when evidence insufficient.
7. **Evidence-backed quizzes** — multiple-choice + short-answer generation from retrieved evidence; lexical-support validation; deterministic scoring (exact option ID / token-set F1); attempt history.
8. **Learning analytics** — quiz accuracy, attempt counts, completed sessions, overdue reviews, topic mastery (deterministic heuristic, not validated learning measure).
9. **Study coach** — rule-based daily plan with priorities, accept/edit/dismiss/complete, optional tutor explanation.
10. **Knowledge graph** — candidate concept/relationship extraction from chunks, human verify/dismiss, citations to source passages.
11. **Security** — structured JSON logging with secret redaction, request IDs, security headers (nosniff, DENY framing, same-origin referrer, CSP), CORS without wildcard, rate-limit middleware, max-body middleware, no secret leakage in errors.
12. **Evaluation** — reproducible backend + retrieval suite; synthetic dataset; machine-readable JSON/Markdown reports.

---

## Known limitations (honest, not bugs)

These are documented architectural constraints. They do not fail tests but bound what the app can do:

- **No authentication** — single logical user. Anyone able to reach `http://127.0.0.1:8000` has full read/write access. Do not expose on a shared network without a reverse-proxy auth layer.
- **Rate limiting off by default** — infrastructure exists (`WL_RATE_LIMIT_ENABLED`); enable in production.
- **SQLite single-writer** — concurrent writes serialize; WAL mode helps concurrent reads but not write scale.
- **No bundled model** — tutor and semantic embeddings require manual local setup; nothing is downloaded automatically.
- **Tutor/quiz quality unreviewed** — answer quality, quiz validity, graph extraction, and short-answer scoring need human review; not validated for learning outcomes.
- **Camera tracking prototype** — works with the authored SVG; not validated on real camera footage.
- **Synthetic evaluation only** — retrieval benchmarks use a small authored corpus; not product-quality claims.
- **No automatic/cloud backups** — the local `app.backup` CLI creates and
  validates SQLite archives; operators must schedule it and protect a separate
  copy for device-loss recovery.
- **Large frontend bundle** — opencv.js (~15 MB gzipped ~4 MB) dominates the build; code-splitting is an outstanding optimization.

---

## Deployment and rollback

### Deployment
1. Build the Docker image or run bare metal with `WL_ENVIRONMENT=production`.
2. Set `WL_API_HOST=0.0.0.0` only behind a trusted TLS-terminating reverse proxy.
3. Set `WL_CORS_ORIGINS` to your frontend HTTPS origin(s).
4. Enable `WL_RATE_LIMIT_ENABLED=true` and set `WL_MAX_REQUEST_BODY_BYTES`.
5. Mount `data/local` and `artifacts/embedding-models` as volumes for persistence.
6. Verify `GET /api/v1/ready` returns HTTP 200 before exposing traffic.

### Rollback
1. Redeploy the previous container image or commit.
2. Restore the SQLite file from the most recent file-level backup (see backup procedure below).
3. Verify `GET /api/v1/ready` returns 200, then run the smoke-test checklist.

### Backup
1. Stop the API (or quiesce writes).
2. Copy `data/local/wonderlearn.sqlite3` to a timestamped backup.
3. Copy `artifacts/embedding-models` if semantic retrieval is used.
4. Verify with `sqlite3 file.sqlite3 "PRAGMA quick_check;"`.

### Recovery
1. Stop the API.
2. Restore the SQLite file and embedding cache to their configured paths.
3. Start the API; confirm readiness HTTP 200.

---

## Test commands and actual results

Run from the repository root:

```powershell
# Backend (all tests)
uv run --project services/api --extra dev pytest

# Backend lint
uv run --project services/api --extra dev ruff check services/api

# Frontend tests
npm test --prefix apps/web

# Frontend typecheck
npm run typecheck --prefix apps/web

# Frontend production build
npm run build --prefix apps/web

# Full evaluation suite (offline-default, lexical)
uv run --locked --project services/api --extra dev --extra semantic python evaluations/run_suite.py

# Evaluation including M18 real-world quality checks
uv run --locked --project services/api --extra dev --extra semantic python evaluations/run_suite.py --quality
```

**Actual results at release:**
- Backend: 124 passed, 1 skipped (opt-in model integration requires `WL_RUN_MODEL_INTEGRATION=1` and a locally cached model)
- Frontend: 35 passed
- Ruff: all checks passed
- TypeScript: clean
- Build: successful (`dist/` output)
- Evaluation: exit 0; lexical Recall@5 = 1.000 on the M14 synthetic set; M18 quality suite Recall@5 = 1.000, citation validity = 1.000

No secrets committed. `.env` is gitignored. CI runs tests and lint only — no automatic deployment.

---

## Remaining blockers and risks

| ID | Severity | Finding | Recommendation |
|---|---|---|---|
| R1 | P1 | No authentication; single-user by design | Do not expose publicly without reverse-proxy auth/VPN |
| R2 | P1 | Rate limiting disabled by default | Enable `WL_RATE_LIMIT_ENABLED=true` in production |
| R3 | P2 | SQLite single-writer concurrency | Accept for local-first; evaluate WAL tuning under write load |
| R4 | P2 | No bundled tutor/semantic model | Document manual setup; not a release blocker for local-first demo |
| R5 | P2 | Camera tracking unvalidated on real footage | Do not demonstrate camera tracking with unowned footage |
| R6 | P3 | No CI auto-deployment | Intentional; manual deploy after secret/config review |
| R7 | P3 | Large frontend bundle (opencv.js) | Consider code-splitting for bandwidth-constrained users |

---

## Authorization checklist

Do not deploy to production until:
1. All smoke-test checks in `docs/deployment.md` §9 pass against a staging-like environment.
2. `.env` contains production-safe values (no localhost-only CORS unless behind a proxy; `WL_RATE_LIMIT_ENABLED=true`).
3. Backup/restore procedure has been verified.
4. Deployment has been explicitly authorized.
