"""Structured, secret-safe logging for the WonderLearn API.

Logs are emitted as single-line JSON records.  The formatter redacts any
value whose key looks like a secret or credential (token, key, secret,
password, authorization, api_key, etc.) and never records uploaded file
contents, camera frames, or private document text.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Any

_SECRET_KEY_RE = re.compile(
    r"(token|key|secret|password|passwd|pwd|authorization|api[_ ]?key|access[_ ]?|credential)",
    re.IGNORECASE,
)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if _SECRET_KEY_RE.search(str(k)) else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str) and len(value) > 10_000:
        return value[:10_000] + "…[truncated]"
    return value


class StructuredFormatter(logging.Formatter):
    """Emit one JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Attach request_id and other context when present on the record.
        for attr in ("request_id", "method", "path", "status_code", "duration_ms"):
            value = getattr(record, attr, None)
            if value is not None:
                payload[attr] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(log_level: str | None = None) -> None:
    """Configure the root logger with structured JSON output to stderr.

    Safe to call multiple times; subsequent calls are no-ops once a handler
    is attached to the root logger.
    """
    root = logging.getLogger()
    if root.handlers:
        return

    level_name = (log_level or os.getenv("WL_LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(StructuredFormatter())
    root.addHandler(handler)

    # Keep third-party loggers quiet unless explicitly asked otherwise.
    for name in ("uvicorn", "uvicorn.error", "fastapi", "httpx", "sqlalchemy"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger("wonderlearn").info("logging_configured", extra={"level": level_name})


def get_logger(name: str = "wonderlearn") -> logging.Logger:
    return logging.getLogger(name)


def secret_field(key: str, value: Any) -> Any:
    """Return a redacted value for any field whose name suggests a secret."""
    if _SECRET_KEY_RE.search(key):
        return "[REDACTED]"
    return _redact(value)
