"""FastAPI application factory and local learning API routes."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.analytics_routes import router as analytics_router
from app.api.v1.assessment_routes import router as assessment_router
from app.api.v1.auth_routes import router as auth_router
from app.api.v1.classroom_intelligence_routes import router as classroom_intelligence_router
from app.api.v1.classroom_routes import router as classroom_router
from app.api.v1.communication_routes import router as communication_router
from app.api.v1.companion_routes import router as companion_router
from app.api.v1.content import router as content_router
from app.api.v1.content_validation import router as content_validation_router
from app.api.v1.courses_routes import router as courses_router
from app.api.v1.exam_routes import router as exam_router
from app.api.v1.gap_routes import router as gap_router
from app.api.v1.groups_ai_routes import router as groups_ai_router
from app.api.v1.groups_routes import router as groups_router
from app.api.v1.institutions_routes import router as institutions_router
from app.api.v1.interoperability_routes import router as interoperability_router
from app.api.v1.journey_routes import router as journey_router
from app.api.v1.learning_path_routes import router as learning_path_router
from app.api.v1.lesson_routes import router as lesson_router
from app.api.v1.model_routes import router as model_routes_router
from app.api.v1.platform_routes import router as platform_router
from app.api.v1.recommendations_routes import router as recommendations_router
from app.api.v1.routes import make_error_response, router
from app.api.v1.search_routes import router as search_router
from app.api.v1.verification_routes import router as verification_router
from app.api.v1.workspaces import router as workspaces_router
from app.api.v2.routes import router as v2_router
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
    # Model observability & evaluation admin routes (admin only)
    application.include_router(model_routes_router)
    # Content verification routes (M59: AI Content Verification & Trust Engine)
    application.include_router(verification_router)
    # Unified learning journey routes (M60: Unified Learning Journey & Experience Orchestration)
    application.include_router(journey_router)
    # Collaborative learning routes (M61: Collaborative Learning & Study Groups)
    application.include_router(groups_router)
    # Collaborative AI study rooms routes (M62: Collaborative AI Study Sessions)
    application.include_router(groups_ai_router)
    # Collaborative AI course builder routes (M63: AI Course Builder & Curriculum Generator)
    application.include_router(courses_router)
    # Collaborative AI assessment & competency routes (M64: Assessment & Competency Management)
    application.include_router(assessment_router)
    # Learning gap intelligence & remediation routes (M65: Learning Gap Intelligence & Remediation)
    application.include_router(gap_router)
    # AI Examination Simulator & Exam Preparation Engine (M66)
    application.include_router(exam_router)
    # Predictive Learning Analytics & Student Performance Intelligence (M67)
    application.include_router(analytics_router)
    # AI Lesson Planner & Teaching Studio (M69)
    application.include_router(lesson_router)
    # Live Interactive Classroom & Virtual Learning Environment (M70)
    application.include_router(classroom_router)
    # Classroom Intelligence & Teaching Effectiveness Analytics (M71)
    application.include_router(classroom_intelligence_router)
    # Communication & Learning Progress Portal (M72)
    application.include_router(communication_router)
    # Institution Management & Multi-Tenant Platform (M73)
    application.include_router(institutions_router)
    # API Platform & Third-Party Integration Ecosystem (M74)
    application.include_router(platform_router)
    # Universal Search & Intelligent Knowledge Discovery (M78)
    application.include_router(search_router)
    # Educational Standards & Learning Interoperability (M80)
    application.include_router(interoperability_router)
    # AI-Powered Learning Content Recommendation & Discovery (M81)
    application.include_router(recommendations_router)
    # AI Learning Path Generator & Dynamic Curriculum Orchestration (M82)
    application.include_router(learning_path_router)
    # API v2 (backward-compatible versioned router)
    application.include_router(v2_router)
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
