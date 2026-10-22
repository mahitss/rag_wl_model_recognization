"""Local-only sentence embedding, validation, normalization, and index rebuilding."""

import hashlib
import json
import math
import struct
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings
from app.storage import (
    embedding_index_counts,
    fetch_chunk_batch,
    is_valid_normalized_vector_blob,
    store_chunk_embeddings,
)

QUERY_INSTRUCTION_PREFIX = "Represent this sentence for searching relevant passages: "


class ModelUnavailable(RuntimeError):
    """Raised when the configured embedding runtime or local model cache is unavailable."""


class EmbeddingModel(Protocol):
    def embed(self, texts: list[str], *, batch_size: int) -> Iterable[Sequence[float]]: ...


def embedding_config_hash(settings: Settings) -> str:
    config = {
        "model_id": settings.embedding_model_id,
        "model_revision": settings.embedding_model_revision,
        "dimensions": settings.embedding_dimensions,
        "chunk_size_chars": settings.chunk_size_chars,
        "chunk_overlap_chars": settings.chunk_overlap_chars,
    }
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()


def validate_and_normalize_vectors(
    vectors: Iterable[Sequence[float]], *, expected_count: int, dimensions: int
) -> list[tuple[float, ...]]:
    normalized: list[tuple[float, ...]] = []
    for vector in vectors:
        try:
            values = tuple(float(value) for value in vector)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Embedding output must contain numeric values") from exc
        if len(values) != dimensions:
            raise ValueError(f"Expected {dimensions}-dimensional embedding vectors")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Embedding output contains a non-finite value")
        norm = math.hypot(*values)
        if not math.isfinite(norm) or norm == 0.0:
            raise ValueError("Embedding output cannot be a zero or invalid vector")
        values = tuple(value / norm for value in values)
        try:
            packed = struct.pack(f"<{dimensions}f", *values)
        except (OverflowError, struct.error) as exc:
            raise ValueError("Embedding output cannot be stored as float32") from exc
        normalized.append(struct.unpack(f"<{dimensions}f", packed))
    if len(normalized) != expected_count:
        raise ValueError("Embedding model returned an unexpected number of vectors")
    return normalized


def _load_fastembed(settings: Settings) -> EmbeddingModel:
    try:
        from fastembed import TextEmbedding

        return TextEmbedding(
            model_name=settings.embedding_model_id,
            cache_dir=str(settings.embedding_cache_path),
            revision=settings.embedding_model_revision,
            local_files_only=True,
            cuda=False,
        )
    except Exception as exc:
        raise ModelUnavailable(
            "The configured embedding model is unavailable locally. "
            "Install the semantic extra and run scripts/download_embedding_model.py."
        ) from exc


class EmbeddingService:
    def __init__(self, settings: Settings, model_factory=None) -> None:
        self.settings = settings
        self._model_factory = model_factory or _load_fastembed
        self._model: EmbeddingModel | None = None
        self._model_error: ModelUnavailable | None = None
        self._lock = threading.Lock()
        self._inference_lock = threading.Lock()

    @property
    def model_state(self) -> str:
        if self._model is not None:
            return "available"
        if self._model_error is not None:
            return "unavailable"
        return "not_checked"

    def load_model(self) -> EmbeddingModel:
        if self._model is not None:
            return self._model
        if self._model_error is not None:
            raise self._model_error
        with self._lock:
            if self._model is not None:
                return self._model
            if self._model_error is not None:
                raise self._model_error
            try:
                self._model = self._model_factory(self.settings)
            except ModelUnavailable as exc:
                self._model_error = exc
                raise
            except Exception as exc:
                self._model_error = ModelUnavailable(
                    "The configured embedding model could not be loaded locally."
                )
                raise self._model_error from exc
        return self._model

    def embed_texts(self, texts: list[str], *, query: bool = False) -> list[tuple[float, ...]]:
        if not texts:
            return []
        model = self.load_model()
        all_vectors: list[tuple[float, ...]] = []
        for start in range(0, len(texts), self.settings.embedding_batch_size):
            batch = texts[start : start + self.settings.embedding_batch_size]
            inputs = [QUERY_INSTRUCTION_PREFIX + text for text in batch] if query else batch
            try:
                with self._inference_lock:
                    raw_vectors = list(
                        model.embed(inputs, batch_size=self.settings.embedding_batch_size)
                    )
                vectors = validate_and_normalize_vectors(
                    raw_vectors,
                    expected_count=len(batch),
                    dimensions=self.settings.embedding_dimensions,
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Embedding model returned invalid vectors") from exc
            except Exception as exc:
                raise ModelUnavailable(
                    "The local embedding model failed during inference."
                ) from exc
            all_vectors.extend(vectors)
        return all_vectors

    def status(self, database_path: Path, *, user_id: str | None = None) -> dict[str, Any]:
        return {
            "modelId": self.settings.embedding_model_id,
            "modelRevision": self.settings.embedding_model_revision,
            "dimensions": self.settings.embedding_dimensions,
            "modelState": self.model_state,
            "index": embedding_index_counts(
                database_path,
                config_hash=embedding_config_hash(self.settings),
                dimensions=self.settings.embedding_dimensions,
                user_id=user_id,
            ),
        }

    def rebuild(
        self,
        database_path: Path,
        *,
        force: bool = False,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        config_hash = embedding_config_hash(self.settings)
        cursor: str | None = None
        indexed = 0
        while True:
            batch = fetch_chunk_batch(
                database_path,
                after_chunk_id=cursor,
                limit=self.settings.embedding_batch_size,
                user_id=user_id,
            )
            if not batch:
                break
            cursor = str(batch[-1]["chunk_id"])
            pending = [
                chunk
                for chunk in batch
                if force
                or chunk["config_hash"] != config_hash
                or chunk["dimensions"] != self.settings.embedding_dimensions
                or not is_valid_normalized_vector_blob(
                    chunk["vector"], self.settings.embedding_dimensions
                )
            ]
            if not pending:
                continue
            vectors = self.embed_texts([str(chunk["text"]) for chunk in pending])
            entries = [
                {
                    "chunk_id": chunk["chunk_id"],
                    "model_id": self.settings.embedding_model_id,
                    "model_revision": self.settings.embedding_model_revision,
                    "config_hash": config_hash,
                    "dimensions": self.settings.embedding_dimensions,
                    "vector": struct.pack(f"<{self.settings.embedding_dimensions}f", *vector),
                }
                for chunk, vector in zip(pending, vectors, strict=True)
            ]
            store_chunk_embeddings(database_path, entries, user_id=user_id)
            indexed += len(entries)
        status = self.status(database_path, user_id=user_id)
        status["indexedThisRun"] = indexed
        status["complete"] = status["index"]["missing_chunks"] == 0
        return status

    def query_vector(self, query: str) -> tuple[float, ...]:
        return self.embed_texts([query], query=True)[0]
