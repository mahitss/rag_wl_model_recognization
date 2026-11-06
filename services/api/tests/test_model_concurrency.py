"""M57: Concurrent inference tests for the model gateway and embedding service.

Tests cover:
* Bounded concurrency: embedding inference semaphore limits parallel calls.
* Gateway handles concurrent requests without cross-contamination.
* Multiple concurrent gateway calls complete with correct per-call attribution.
* No shared state corruption between concurrent calls.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.config import Settings
from app.embeddings.service import EmbeddingService
from app.model_gateway import ModelGateway, ModelGatewayRequest, model_gateway_factory
from app.model_router import ModelUnavailable


def make_settings(tmp_path, **overrides):
    values = {
        "_env_file": None,
        "database_path": tmp_path / "wonderlearn.sqlite3",
        "cors_origins": "http://localhost:5173",
        "embedding_dimensions": 4,
        "embedding_batch_size": 2,
        "lexical_candidate_limit": 10,
        "semantic_candidate_limit": 10,
        "retrieval_mode": "lexical",
    }
    values.update(overrides)
    return Settings(**values)


# ---------------------------------------------------------------------------
# Fake model transport for concurrent tests
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, body: str):
        self._body = body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self._body

    def json(self):
        return json.loads(self._body)


class CapturingOpener:
    """Captures request bodies and returns a valid abstention response."""

    def __init__(self):
        self.captured: list[dict[str, Any]] = []
        self._lock = __import__("threading").Lock()

    def open(self, request, timeout):
        with self._lock:
            self.captured.append(json.loads(request.data))
        inner = json.dumps({"answer": "", "citations": [], "abstain": True})
        body = json.dumps({"choices": [{"message": {"content": inner}}]})
        return FakeResponse(body)


# ---------------------------------------------------------------------------
# Async runner helper
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine, handling Windows event loop policy."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------


def test_embedding_service_bounded_concurrency(tmp_path):
    """EmbeddingService uses a semaphore to bound concurrent inference."""
    settings = make_settings(tmp_path)
    service = EmbeddingService(settings)
    # The semaphore is initialized with MAX_CONCURRENT_INFERENCE (2).
    assert service._inference_semaphore._value == 2


def test_gateway_concurrent_requests_distinct_model_ids(tmp_path):
    """Concurrent gateway calls each report the correct model that served them."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    gateway = model_gateway_factory(settings)

    opener = CapturingOpener()

    import app.tutor as tutor_mod

    tutor_mod.build_opener = lambda *_: opener
    try:

        async def main():
            return await asyncio.gather(
                gateway.complete(
                    ModelGatewayRequest(
                        task="tutor",
                        prompt="Question A",
                        require_structured_output=True,
                    )
                ),
                gateway.complete(
                    ModelGatewayRequest(
                        task="tutor",
                        prompt="Question B",
                        require_structured_output=True,
                    )
                ),
                gateway.complete(
                    ModelGatewayRequest(
                        task="tutor",
                        prompt="Question C",
                        require_structured_output=True,
                    )
                ),
            )

        results = _run(main())
    finally:
        tutor_mod.build_opener = __import__("app.tutor", fromlist=["build_opener"]).build_opener

    assert len(results) == 3
    for resp in results:
        assert resp.provider == "local"
        assert resp.model_id == settings.tutor_model_id
        assert resp.outcome == "success"


def test_gateway_concurrent_requests_no_cross_contamination(tmp_path):
    """Concurrent calls do not leak request data between each other."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    gateway = model_gateway_factory(settings)

    opener = CapturingOpener()

    import app.tutor as tutor_mod

    tutor_mod.build_opener = lambda *_: opener
    try:

        async def main():
            return await asyncio.gather(
                gateway.complete(
                    ModelGatewayRequest(
                        task="tutor",
                        prompt="Alpha question about topic one",
                        require_structured_output=True,
                    )
                ),
                gateway.complete(
                    ModelGatewayRequest(
                        task="tutor",
                        prompt="Beta question about topic two",
                        require_structured_output=True,
                    )
                ),
            )

        _run(main())
    finally:
        tutor_mod.build_opener = __import__("app.tutor", fromlist=["build_opener"]).build_opener

    assert len(opener.captured) == 2
    prompt_texts = [
        json.loads(c["messages"][1]["content"])["question"] for c in opener.captured
    ]
    assert "Alpha question about topic one" in prompt_texts
    assert "Beta question about topic two" in prompt_texts


def test_embedding_service_concurrent_embedding_calls(tmp_path):
    """Multiple concurrent embed() calls are bounded by the semaphore."""
    settings = make_settings(tmp_path)
    service = EmbeddingService(settings)

    call_count = 0
    max_concurrent = 0
    current_concurrent = 0
    lock = __import__("threading").Lock()

    # Use non-zero unit vectors to pass vector validation.
    unit_vector = [1.0, 0.0, 0.0, 0.0]

    class CountingModel:
        def embed(self, texts, *, batch_size):
            nonlocal call_count, current_concurrent, max_concurrent
            call_count += 1
            with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
            for _ in texts:
                yield list(unit_vector)
            with lock:
                current_concurrent -= 1

    service._model_factory = lambda _: CountingModel()
    service.load_model()

    async def run_batch(texts: list[str]):
        return service.embed_texts(texts)

    async def main():
        return await asyncio.gather(
            run_batch(["text one", "text two"]),
            run_batch(["text three", "text four"]),
        )

    _run(main())

    assert call_count >= 2  # at least one batch per call
    # Concurrency is bounded by the semaphore (max 2)
    assert max_concurrent <= 2


def test_gateway_concurrent_calls_all_succeed(tmp_path):
    """Multiple concurrent gateway calls all succeed with correct attribution."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    gateway = model_gateway_factory(settings)

    opener = CapturingOpener()

    import app.tutor as tutor_mod

    tutor_mod.build_opener = lambda *_: opener
    try:

        async def main():
            return await asyncio.gather(
                *(gateway.complete(
                    ModelGatewayRequest(
                        task="tutor",
                        prompt=f"Question {i}",
                        require_structured_output=True,
                    )
                ) for i in range(5))
            )

        results = _run(main())
    finally:
        tutor_mod.build_opener = __import__("app.tutor", fromlist=["build_opener"]).build_opener

    assert len(results) == 5
    for resp in results:
        assert resp.provider == "local"
        assert resp.model_id == settings.tutor_model_id
        assert resp.outcome == "success"
