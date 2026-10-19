# WonderLearn API — production Docker image.
#
# Build:
#   docker build -t wonderlearn-api .
#
# Run (production):
#   docker run --env-file .env -p 8000:8000 wonderlearn-api
#
# The image runs as a non-root user. The SQLite database path and embedding
# cache must be mounted as volumes for persistence across restarts.

# ---------------------------------------------------------------
# Stage 1: dependency installation (build cache friendly)
# ---------------------------------------------------------------
FROM python:3.12-slim AS deps

WORKDIR /wonderlearn

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install uv and runtime dependencies (no dev tools in the final image).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

# Copy only the lockfile first for better layer caching.
COPY services/api/uv.lock services/api/pyproject.toml ./
RUN uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------
# Stage 2: runtime image
# ---------------------------------------------------------------
FROM python:3.12-slim AS runtime

WORKDIR /wonderlearn

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/wonderlearn/.venv/bin:$PATH"

# Runtime system dependencies (tesseract for optional OCR).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        libgomp1 \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for the application.
RUN useradd --create-home --shell /bin/bash wonderlearn \
    && mkdir -p /wonderlearn/data/local /wonderlearn/artifacts/embedding-models \
    && chown -R wonderlearn:wonderlearn /wonderlearn

# Copy the virtualenv from the deps stage.
COPY --from=deps /wonderlearn/.venv /wonderlearn/.venv

# Copy application source.
COPY services/api/app ./app
COPY services/api/pyproject.toml ./pyproject.toml

# Directories for persistence (mount volumes here in production).
VOLUME ["/wonderlearn/data/local", "/wonderlearn/artifacts/embedding-models"]

USER wonderlearn

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/ready || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
