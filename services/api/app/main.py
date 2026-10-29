"""FastAPI application factory and local learning API routes."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.auth_routes import router as auth_router
from app.api.v1.companion_routes import router as companion_router
from app.api.v1.content import router as content_router
from app.api.v1.content_validation import router as content_validation_router
from app.api.v1.routes import make_error_response, router
from app.api.v1.workspaces import router as workspaces_router
from app.config import Settings, get_settings
from app.embeddings.service import EmbeddingService
from app.errors import ApiError
from app.logging import configure_logging, get_logger
from app.middleware import RequestIdMiddleware
from app.observability import OperationalMetrics
from app.security import (
    CSRFProtectionMiddleware,
    MaxBodySizeMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.storage import initialize_storage

logger = get_logger("wonderlearn")


def create_app(settings: Settings | None = None) -> FastAPI:
    print(f"DEBUG create_app called with settings={settings}")
    configured = settings or get_settings()
    configure_logging(configured.log_level)

    # Startup validation: surface configuration issues before serving traffic.
    startup_warnings = configured.validate_startup()
    for warning in startup_warnings:
        logger.warning("startup_warning", extra={"warning": warning})

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Startup
        logger.info("startup", extra={"environment": configured.environment})
        try:
            initialize_storage(configured.database_path)
            logger.info(
                "storage_initialized",
                extra={"database_path": str(configured.database_path)},
            )
        except Exception:
            logger.exception(
                "storage_initialization_failed",
                extra={"database_path": str(configured.database_path)},
            )
            raise
        yield
        # Shutdown: clean up any long-lived resources.
        logger.info("shutdown")

    application = FastAPI(
        title="WonderLearn API",
        version="0.1.0",
        description=(
            "Local PDF and educational-image ingestion, citation-preserving retrieval, and an "
            "optional grounded tutor connected only to a loopback OpenAI-compatible model server."
        ),
        lifespan=lifespan,
    )
    application.state.settings = configured
    application.state.embedding_service = EmbeddingService(configured)
    application.state.metrics = OperationalMetrics()

    # All API state changes use cookie authentication and require CSRF validation.
    application.add_middleware(CSRFProtectionMiddleware)
    # Security headers (outermost: present on every response, including errors).
    application.add_middleware(SecurityHeadersMiddleware)
    # Max request body size: reject oversized bodies before rate-limit accounting.
    application.add_middleware(
        MaxBodySizeMiddleware,
        max_bytes=configured.max_request_body_bytes,
    )
    # Rate limiting: fixed window per client IP and endpoint.
    application.add_middleware(
        RateLimitMiddleware,
        enabled=configured.rate_limit_enabled,
        requests_per_minute=configured.rate_limit_requests_per_minute,
        auth_requests_per_minute=configured.auth_rate_limit_requests_per_minute,
        expensive_requests_per_minute=configured.expensive_rate_limit_requests_per_minute,
        exempt_paths=frozenset({"/api/v1/health", "/api/v1/ready"}),
    )
    # Request ID + access logging (applied before CORS so headers flow through).
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configured.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "X-CSRF-Token"],
    )

    # Auth router (registration, login, logout, current user)
    application.include_router(auth_router)
    # Main API router (all user-facing endpoints require authentication)
    application.include_router(router)
    # Workspace router (shared study spaces require membership)
    application.include_router(workspaces_router)
    # Content studio router (AI-generated learning resources require ownership)
    application.include_router(content_router)
    # Content studio validation router (evidence verification requires ownership)
    application.include_router(content_validation_router)
    # Companion router (personal AI learning companion, user-scoped)
    application.include_router(companion_router)
    print(f"DEBUG content_validation_router included, routes={len(application.router.routes)}")
    for r in application.router.routes:
        if hasattr(r, 'path'):
            print(f'  Route: {list(r.methods)} {r.path}')
    for r in application.router.routes:
        if hasattr(r, 'path'):
            print(f"  Route: {list(r.methods)} {r.path}")

    @application.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> Any:
        request_id = getattr(request.state, "request_id", None)
        return make_error_response(
            exc.status_code, exc.code, exc.message, exc.details, request_id=request_id
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, __: RequestValidationError) -> Any:
        request_id = getattr(request.state, "request_id", None)
        return make_error_response(
            422,
            "INVALID_REQUEST",
            "The request did not pass validation.",
            request_id=request_id,
        )

    return application


app = create_app()
