"""Validated environment-backed configuration for the local API."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    request_timeout_seconds: float = 30.0
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = 60
    auth_rate_limit_requests_per_minute: int = 10
    expensive_rate_limit_requests_per_minute: int = 10
    max_request_body_bytes: int | None = 12 * 1024 * 1024
    database_path: Path = Path("data/local/wonderlearn.sqlite3")
    backup_directory: Path = Path("backups")
    backup_retention_count: int = 30
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_upload_bytes: int = 10 * 1024 * 1024
    max_image_upload_bytes: int = 10 * 1024 * 1024
    max_image_dimension: int = 4096
    max_image_pixels: int = 16_000_000
    max_pdf_pages: int = 250
    max_extracted_chars: int = 5_000_000
    chunk_size_chars: int = 2_000
    chunk_overlap_chars: int = 200
    max_retrieval_results: int = 10
    retrieval_mode: Literal["lexical", "semantic", "hybrid"] = "lexical"
    embedding_model_id: str = "BAAI/bge-small-en-v1.5"
    embedding_model_revision: str = "c32e6154d1bb7a0e47c5e745fd895e7700f44385"
    embedding_dimensions: int = 384
    embedding_batch_size: int = 32
    lexical_candidate_limit: int = 50
    semantic_candidate_limit: int = 50
    rrf_k: int = 60
    semantic_min_similarity: float = 0.60
    embedding_cache_path: Path = Path("artifacts/embedding-models")
    tutor_base_url: str | None = None
    tutor_model_id: str = "local-model"
    tutor_timeout_seconds: float = 90.0
    tutor_max_tokens: int = 700

    # Session and authentication settings
    session_ttl_seconds: int = 30 * 24 * 60 * 60  # 30 days

    @field_validator("database_path")
    @classmethod
    def database_path_must_name_a_file(cls, value: Path) -> Path:
        if value.exists() and value.is_dir():
            raise ValueError("WL_DATABASE_PATH must point to a file, not a directory")
        return value

    @field_validator("cors_origins")
    @classmethod
    def cors_origins_must_be_safe_origins(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        if not origins:
            raise ValueError("WL_CORS_ORIGINS must contain at least one origin")
        for origin in origins:
            if origin == "*":
                raise ValueError("WL_CORS_ORIGINS must not use wildcard '*'")
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("WL_CORS_ORIGINS must use http or https")
            if not parsed.hostname:
                raise ValueError("WL_CORS_ORIGINS entries must include a hostname")
            if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError(
                    "WL_CORS_ORIGINS entries must not include path, query, fragment, or credentials"
                )
            # In development, only loopback HTTP origins are allowed.
            # In production, HTTPS origins are permitted (e.g. a hosted frontend).
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError(
                    "WL_CORS_ORIGINS HTTP origins must use localhost or "
                    "127.0.0.1; use HTTPS for remote origins"
                )
        return ",".join(origins)

    @field_validator("tutor_base_url")
    @classmethod
    def tutor_url_must_be_loopback(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("WL_TUTOR_BASE_URL must use HTTP on a loopback host")
        return normalized

    @field_validator(
        "max_upload_bytes",
        "max_image_upload_bytes",
        "max_image_dimension",
        "max_image_pixels",
        "max_pdf_pages",
        "max_extracted_chars",
        "chunk_size_chars",
        "max_retrieval_results",
        "embedding_dimensions",
        "embedding_batch_size",
        "lexical_candidate_limit",
        "semantic_candidate_limit",
        "rrf_k",
    )
    @classmethod
    def positive_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Configured limits must be positive")
        return value

    @field_validator("tutor_max_tokens")
    @classmethod
    def tutor_token_limit_is_bounded(cls, value: int) -> int:
        if not 1 <= value <= 4_096:
            raise ValueError("WL_TUTOR_MAX_TOKENS must be between 1 and 4096")
        return value

    @field_validator("tutor_timeout_seconds")
    @classmethod
    def tutor_timeout_is_bounded(cls, value: float) -> float:
        if not 1 <= value <= 600:
            raise ValueError("WL_TUTOR_TIMEOUT_SECONDS must be between 1 and 600")
        return value

    @field_validator("rate_limit_requests_per_minute")
    @classmethod
    def rate_limit_requests_is_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("WL_RATE_LIMIT_REQUESTS_PER_MINUTE must be positive")
        return value

    @field_validator(
        "auth_rate_limit_requests_per_minute",
        "expensive_rate_limit_requests_per_minute",
        "session_ttl_seconds",
    )
    @classmethod
    def authentication_limits_are_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Authentication and session limits must be positive")
        return value

    @field_validator("backup_retention_count")
    @classmethod
    def backup_retention_count_is_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("WL_BACKUP_RETENTION_COUNT must be positive")
        return value

    @field_validator("max_request_body_bytes", mode="before")
    @classmethod
    def max_request_body_bytes_is_positive(cls, value: int | None) -> int | None:
        if value == "" or value is None:
            return None
        if isinstance(value, str):
            value = int(value)
        if value <= 0:
            raise ValueError("WL_MAX_REQUEST_BODY_BYTES must be positive or unset")
        return value

    @field_validator(
        "embedding_dimensions",
        "embedding_batch_size",
        "lexical_candidate_limit",
        "semantic_candidate_limit",
        "rrf_k",
    )
    @classmethod
    def embedding_limits_are_bounded(cls, value: int, info) -> int:
        maximum = 4_096 if info.field_name == "embedding_dimensions" else 1_000
        if value > maximum:
            raise ValueError(f"{info.field_name} cannot exceed {maximum}")
        return value

    @field_validator("semantic_min_similarity")
    @classmethod
    def semantic_threshold_is_valid(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("WL_SEMANTIC_MIN_SIMILARITY must be between 0 and 1")
        return value

    @field_validator("embedding_model_id", "embedding_model_revision")
    @classmethod
    def model_settings_are_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Embedding model identifier and revision cannot be empty")
        return value.strip()

    @field_validator("tutor_model_id")
    @classmethod
    def tutor_model_id_must_be_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("WL_TUTOR_MODEL_ID cannot be empty")
        return value.strip()

    @field_validator("chunk_overlap_chars")
    @classmethod
    def overlap_must_be_smaller_than_chunk_size(cls, value: int, info) -> int:
        if value < 0:
            raise ValueError("WL_CHUNK_OVERLAP_CHARS cannot be negative")
        chunk_size = info.data.get("chunk_size_chars", 2_000)
        if value >= chunk_size:
            raise ValueError("WL_CHUNK_OVERLAP_CHARS must be smaller than WL_CHUNK_SIZE_CHARS")
        return value

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @property
    def tutor_configured(self) -> bool:
        return self.tutor_base_url is not None

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate_startup(self) -> list[str]:
        """Return a list of human-readable startup warnings (never fatal).

        Checks that the database directory is writable, the embedding cache
        path exists or can be created, and that production settings are safe.
        """
        warnings: list[str] = []

        db_dir = self.database_path.parent
        if not db_dir.exists():
            try:
                db_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                warnings.append(f"cannot create database directory {db_dir}: {exc}")

        cache_dir = self.embedding_cache_path
        if not cache_dir.exists():
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                warnings.append(f"cannot create embedding cache directory {cache_dir}: {exc}")

        if self.is_production:
            if any(origin.startswith("http://") for origin in self.allowed_origins):
                raise ValueError("Production CORS origins must use HTTPS")
            if self.api_host in {"127.0.0.1", "localhost", "::1"}:
                warnings.append(
                    "WL_API_HOST is loopback in production; bind to "
                    "0.0.0.0 only behind a trusted reverse proxy"
                )
            if not self.rate_limit_enabled:
                raise ValueError("WL_RATE_LIMIT_ENABLED must be true in production")
            if self.max_request_body_bytes is None:
                raise ValueError("WL_MAX_REQUEST_BODY_BYTES must be set in production")

        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
