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

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()
