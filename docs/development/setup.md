# Local setup (Windows PowerShell)

M0 provides a local API health/readiness service and an intentionally minimal React shell. It does **not** implement PDF ingestion, retrieval, a tutor, camera processing, or model inference. Nothing downloads a dataset/model or calls a paid service.

## Verified toolchain and pinned packages

The host reports Python 3.13.5, Node.js 24.11.0, npm 11.6.0, Git 2.52.0, and uv 0.12.15. `uv` selected Python 3.12.13 for the locked project environment. The backend declares Python `>=3.11,<3.14`, FastAPI 0.143.3, pydantic-settings 2.16.0, and Uvicorn 0.54.0. Development tools are pytest 9.1.1, HTTPX 0.28.1, and Ruff 0.17.1. Frontend dependencies are pinned exactly in `apps/web/package.json` and `package-lock.json` (React 19.3.1, Vite 8.3.4, TypeScript 7.0.2). `uv.lock` and `package-lock.json` capture resolved dependency trees.

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

Settings use the `WL_` prefix and load `.env` from the current working directory. Supported M0 settings:

- `WL_DATABASE_PATH`: SQLite file path. Default: `data/local/wonderlearn.sqlite3`.
- `WL_CORS_ORIGINS`: comma-separated local HTTP origins. Default allows Vite at localhost and 127.0.0.1 port 5173. M0 rejects non-local origins.

## Project locations

- `apps/web`: minimal React/TypeScript shell.
- `services/api`: FastAPI modular-monolith boundary and system routes.
- `ml/notebooks`: future training experiments; no training code in M0.
- `data/manifests`: dataset provenance metadata only.
- `evaluations`: future evaluation material; no model results claimed.
- `scripts`: local maintenance and setup helpers.
- `tests`: reserved for cross-component checks; API tests currently live beside the service in `services/api/tests`.

The service's future retrieval module belongs under `services/api/app/retrieval`, matching the architecture tree. No separate retrieval service or fake RAG package is created.
