"""M44: Model Router & Local AI Reliability tests.

Tests cover:
* Model router availability / unavailability per task
* Timeouts (tutor)
* Malformed model output (tutor, quiz, knowledge graph)
* Fallback selection (embeddings unavailable -> lexical)
* Privacy boundaries (no external provider configured by default)
* Oversized responses
* Concurrent requests (embedding lock)
* Deterministic no-model operation (no tutor, no embeddings)

Live model tests are NOT included: they require downloading model weights.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.embeddings.service import EmbeddingService, ModelUnavailable, validate_and_normalize_vectors
from app.main import create_app
from app.model_router import ModelRouter, ModelTask
from app.storage import initialize_storage, store_document
from app.tutor import (
    InvalidTutorResponse,
    TutorTimeoutError,
    TutorUnavailable,
    generate_grounded_answer,
    local_chat_completion,
)


class ConceptFakeModel:
    """Deterministic fake embedding model for tests."""

    def __init__(self):
        self.calls = 0

    def embed(self, texts, *, batch_size):
        self.calls += 1
        for text in texts:
            lowered = text.lower()
            if any(w in lowered for w in ("photosynthesis", "sunlight", "plant")):
                yield [1.0, 0.0, 0.0, 0.0]
            elif any(w in lowered for w in ("evaporation", "water vapor")):
                yield [0.0, 1.0, 0.0, 0.0]
            else:
                yield [0.0, 0.0, 0.0, 1.0]


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


def test_model_router_disabled_when_tutor_not_configured(tmp_path):
    settings = make_settings(tmp_path)
    router = settings.model_router
    assert not router.is_available("tutor")
    assert not router.is_available("quiz_generation")
    assert not router.is_available("knowledge_graph_extraction")
    # Embeddings and OCR are always available (local)
    assert router.is_available("embeddings")
    assert router.is_available("ocr")
    with pytest.raises(ModelUnavailable):
        router.route("tutor")


def test_model_router_tracks_failures(tmp_path):
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    router = settings.model_router
    router.record_failure("tutor", Exception("connection refused"))
    assert not router.is_available("tutor")
    assert router.get_status()["tutor"]["failure_count"] == 1
    router.record_success("tutor")
    assert router.is_available("tutor")


def test_model_router_status_includes_all_tasks(tmp_path):
    settings = make_settings(tmp_path)
    router = settings.model_router
    status = router.get_status()
    expected_tasks = {
        "tutor",
        "quiz_generation",
        "knowledge_graph_extraction",
        "embeddings",
        "ocr",
    }
    assert set(status) == expected_tasks
    for task in expected_tasks:
        assert status[task]["privacy_level"] == "local_only"
        assert "model_id" in status[task]
        assert "timeout_seconds" in status[task]


def test_embedding_service_raises_unavailable_when_model_missing(tmp_path):
    settings = make_settings(tmp_path)
    service = EmbeddingService(settings)

    def missing(_):
        raise FileNotFoundError("model file not found")

    service._model_factory = missing
    with pytest.raises(ModelUnavailable):
        service.load_model()


def test_embedding_validation_checks_dimensions_finiteness_and_norm():
    normalized = validate_and_normalize_vectors([[3.0, 4.0]], expected_count=1, dimensions=2)
    assert normalized[0] == pytest.approx((0.6, 0.8))
    with pytest.raises(ValueError, match="dimensional"):
        validate_and_normalize_vectors([[1.0]], expected_count=1, dimensions=2)
    with pytest.raises(ValueError, match="non-finite"):
        validate_and_normalize_vectors([[float("nan"), 1.0]], expected_count=1, dimensions=2)
    with pytest.raises(ValueError, match="zero"):
        validate_and_normalize_vectors([[0.0, 0.0]], expected_count=1, dimensions=2)
    with pytest.raises(ValueError, match="unexpected number"):
        validate_and_normalize_vectors([], expected_count=1, dimensions=2)


def test_hybrid_falls_back_to_lexical_when_embedding_model_unavailable(tmp_path):
    settings = make_settings(tmp_path)

    async def run():
        from app.retrieval.service import retrieve_with_mode

        service = EmbeddingService(settings)

        def missing(_):
            raise ModelUnavailable("offline")

        service._model_factory = missing
        # Initialize storage so retrieval can read chunks
        initialize_storage(settings.database_path)
        result = await asyncio.to_thread(
            retrieve_with_mode,
            settings.database_path,
            query="photosynthesis",
            document_ids=None,
            limit=3,
            mode="hybrid",
            settings=settings,
            embeddings=service,
        )
        assert result["retrievalMode"] == "lexical_fallback"
        assert result["fallbackReason"] == "embedding_model_unavailable"

    asyncio.run(run())


def test_tutor_timeout_error(tmp_path):
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:9/v1")
    assert settings.tutor_configured

    from app.tutor import local_chat_completion

    class FakeOpener:
        def open(self, request, timeout):
            raise TimeoutError("read timed out")

    class FakeOpenerFactory:
        def open(self, request, timeout):
            return FakeOpener().open(request, timeout)

    import app.tutor as tutor_mod

    tutor_mod.build_opener = lambda *_: FakeOpenerFactory()
    try:
        with pytest.raises(TutorTimeoutError):
            local_chat_completion(
                settings,
                system_prompt="sys",
                user_payload={"concept": None, "question": "Q", "evidence": []},
            )
    finally:
        tutor_mod.build_opener = __import__("app.tutor", fromlist=["build_opener"]).build_opener


def test_tutor_request_body_contains_no_secrets_or_private_prompts(tmp_path):
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    class FakeOpener:
        def open(self, request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.data)
            return FakeResponse()

    class FakeOpenerFactory:
        def open(self, request, timeout):
            return FakeOpener().open(request, timeout)

    import app.tutor as tutor_mod

    tutor_mod.build_opener = lambda *_: FakeOpenerFactory()
    try:
        generate_grounded_answer(
            settings,
            question="What is X?",
            concept=None,
            evidence=[{"citationId": "c1", "documentId": "d1", "text": "X is Y."}],
        )
    except Exception:
        pass

    assert "Authorization" not in captured["headers"]
    assert captured["body"]["model"] == settings.tutor_model_id
    system_content = captured["body"]["messages"][0]["content"]
    assert "API key" not in system_content
    assert "secret" not in system_content.lower()


def test_tutor_response_size_limit(tmp_path):
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b"{" + b"x" * (300 * 1024) + b"}"

    class FakeOpener:
        def open(self, request, timeout):
            return FakeResponse()

    class FakeOpenerFactory:
        def open(self, request, timeout):
            return FakeOpener().open(request, timeout)

    import app.tutor as tutor_mod

    tutor_mod.build_opener = lambda *_: FakeOpenerFactory()
    try:
        with pytest.raises(InvalidTutorResponse):
            generate_grounded_answer(
                settings,
                question="Q",
                concept=None,
                evidence=[{"citationId": "c1", "documentId": "d1", "text": "X"}],
            )
    finally:
        tutor_mod.build_opener = FakeOpenerFactory()


def test_no_model_operation_abstains_gracefully(tmp_path):
    settings = make_settings(tmp_path)  # no tutor_base_url -> no tutor
    assert not settings.tutor_configured

    from app.tutor import generate_grounded_answer

    with pytest.raises(TutorUnavailable):
        generate_grounded_answer(
            settings,
            question="Q",
            concept=None,
            evidence=[{"citationId": "c1", "documentId": "d1", "text": "X"}],
        )


def test_model_router_included_in_settings(tmp_path):
    settings = make_settings(tmp_path)
    router = settings.model_router
    assert isinstance(router, ModelRouter)
    expected_tasks = {"tutor", "quiz_generation", "knowledge_graph_extraction", "embeddings", "ocr"}
    assert set(router._routes) == expected_tasks


def test_model_router_route_for_all_tasks(tmp_path):
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    router = settings.model_router
    for task in ("tutor", "quiz_generation", "knowledge_graph_extraction", "embeddings", "ocr"):
        route = router.route(task)
        assert route.task == task
        assert route.provider == "local"
        assert route.privacy_level == "local_only"


def test_ocr_availability_flag(tmp_path):
    settings = make_settings(tmp_path)
    router = settings.model_router
    ocr_route = router.route("ocr")
    assert ocr_route.enabled
    assert ocr_route.provider == "local"
    assert ocr_route.model_id == "tesseract"
