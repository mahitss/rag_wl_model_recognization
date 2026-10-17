"""FastAPI application factory and local learning API routes."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routes import make_error_response, router
from app.config import Settings, get_settings
from app.errors import ApiError
from app.storage import initialize_storage


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        initialize_storage(configured.database_path)
        yield

    application = FastAPI(
        title="WonderLearn API",
        version="0.1.0",
        description=(
            "Local PDF ingestion and SQLite FTS5 evidence retrieval. "
            "M1 does not include generated model inference."
        ),
        lifespan=lifespan,
    )
    application.state.settings = configured
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configured.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @application.exception_handler(ApiError)
    async def handle_api_error(_, exc: ApiError):
        return make_error_response(exc.status_code, exc.code, exc.message, exc.details)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(_, __: RequestValidationError):
        return make_error_response(422, "INVALID_REQUEST", "The request did not pass validation.")

    application.include_router(router)
    return application


app = create_app()
