"""FastAPI application factory and M0 health/readiness routes."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.storage import check_storage, initialize_storage


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        initialize_storage(configured.database_path)
        yield

    application = FastAPI(
        title="WonderLearn API",
        version="0.1.0",
        description="Local development foundation. Learning capabilities are not implemented yet.",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configured.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Content-Type"],
    )

    @application.get("/api/v1/health", tags=["system"])
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "wonderlearn-api",
            "capabilities": {
                "document_ingestion": False,
                "retrieval": False,
                "model_inference": False,
            },
        }

    @application.get("/api/v1/ready", tags=["system"])
    def readiness() -> JSONResponse:
        checks = check_storage(configured.database_path)
        ready = all(checks.values())
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", "checks": checks},
        )

    return application


app = create_app()
