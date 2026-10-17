"""Validated environment-backed configuration for the local API."""

from functools import lru_cache
from pathlib import Path
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

    database_path: Path = Path("data/local/wonderlearn.sqlite3")
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_upload_bytes: int = 10 * 1024 * 1024
    max_pdf_pages: int = 250
    max_extracted_chars: int = 5_000_000
    chunk_size_chars: int = 2_000
    chunk_overlap_chars: int = 200
    max_retrieval_results: int = 10

    @field_validator("database_path")
    @classmethod
    def database_path_must_name_a_file(cls, value: Path) -> Path:
        if value.exists() and value.is_dir():
            raise ValueError("WL_DATABASE_PATH must point to a file, not a directory")
        return value

    @field_validator("cors_origins")
    @classmethod
    def cors_origins_must_be_local_http_origins(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        if not origins:
            raise ValueError("WL_CORS_ORIGINS must contain at least one origin")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in {"localhost", "127.0.0.1"}
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError("M0 CORS origins must use localhost or 127.0.0.1 over HTTP")
        return ",".join(origins)

    @field_validator(
        "max_upload_bytes",
        "max_pdf_pages",
        "max_extracted_chars",
        "chunk_size_chars",
        "max_retrieval_results",
    )
    @classmethod
    def positive_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Configured limits must be positive")
        return value

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
