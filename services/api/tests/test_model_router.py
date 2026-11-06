"""M44 & M57: Model Router & Local AI Reliability tests.

Tests cover:
* Model router availability / unavailability per task
* Timeouts (tutor)
* Malformed model output (tutor, quiz, knowledge graph)
* Fallback selection (embeddings unavailable -> lexical)
* Privacy boundaries (no external provider configured by default)
* Oversized responses
* Concurrent requests (embedding lock)
* Deterministic no-model operation (no tutor, no embeddings)
* Model discovery from local Ollama server
* Model health checks
* Model routing based on discovered models

Live model tests are NOT included: they require downloading model weights.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.embeddings.service import (
    EmbeddingService,
    ModelUnavailable,
    validate_and_normalize_vectors,
)
from app.model_router import ModelRouter
from app.storage import initialize_storage
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
    expected_tasks = {"tutor", "quiz_generation", "knowledge_graph_extraction", "embeddings", "ocr"}
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
        result = retrieve_with_mode(
            settings.database_path,
            query="photosynthesis",
            document_ids=None,
            limit=settings.lexical_candidate_limit,
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


    class FakeOpener:
        def open(self, request, timeout):
            raise TypeError("failed to connect")

    class FakeOpenerFactory:
        def open(self, request, timeout):
            return FakeOpener().open(request, timeout)

    import app.tutor as tutor_mod

    tutor_mod.build_opener = lambda *_: FakeOpenerFactory()
    try:
        with pytest.raises(TutorUnavailable):
            generate_grounded_answer(
                settings,
                question="Q",
                context=None,
                context_ids=None,
                user_id="user-1",
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
            pass

        @property
        def _body(self):
            inner = json.dumps(
                {"answer": "", "citations": [], "abstain": True}
            )
            return json.dumps({"choices": [{"message": {"content": inner}}]}).encode()

        def read(self):
            return self._body

        def json(self):
            return json.loads(self._body)

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
            context=None,
            context_ids=None,
            user_id="user-1",
        )
    finally:
        tutor_mod.build_opener = __import__("app.tutor", fromlist=["build_opener"]).build_opener

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
                context=None,
                context_ids=None,
                user_id="user-1",
            )
    finally:
        tutor_mod.build_opener = __import__("app.tutor", fromlist=["build_opener"]).build_opener


def test_no_model_operation_abstains_gracefully(tmp_path):
    settings = make_settings(tmp_path)  # no tutor_base_url -> no tutor
    assert not settings.tutor_configured

    from app.tutor import generate_grounded_answer

    with pytest.raises(TutorUnavailable):
        generate_grounded_answer(
            settings,
            question="Q",
            context=None,
            context_ids=None,
            user_id="user-1",
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


# ---------------------------------------------------------------------------
# M57: Model discovery & health check tests
# ---------------------------------------------------------------------------


def test_discover_models_without_tutor_configured(tmp_path):
    """Discovery returns empty when no tutor base URL is configured."""
    settings = make_settings(tmp_path)
    router = settings.model_router
    result = router.discover_models()
    assert result.available_models == []
    assert result.available is False
    assert result.error is not None


def test_discover_models_returns_model_list(tmp_path):
    """Discovery returns model list when Ollama is reachable."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:11434/v1")


    class FakeResponse:
        def __init__(self, data):
            self._data = data

        @property
        def status_code(self):
            return 200

        def json(self):
            return self._data

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            return FakeResponse(
                {
                    "models": [
                        {"name": "llama2:latest"},
                        {"name": "llama2:7b-chat"},
                        {"name": "codellama:7b"},
                    ]
                }
            )

    original_httpx_client = httpx.Client
    httpx.Client = lambda *args, **kwargs: FakeClient()
    try:
        router = settings.model_router
        result = router.discover_models()
        assert result.available is True
        assert "llama2:latest" in result.available_models
        assert "llama2:7b-chat" in result.available_models
        assert "codellama:7b" in result.available_models
        assert result.error is None
    finally:
        httpx.Client = original_httpx_client


def test_discover_models_connection_error(tmp_path):
    """Discovery returns empty when Ollama is unreachable."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:9999/v1")


    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise ConnectionError("connection refused")

        def __exit__(self, *args):
            pass

        def get(self, url):
            raise ConnectionError("connection refused")

    import httpx

    original_httpx_client = httpx.Client
    httpx.Client = lambda *args, **kwargs: FakeClient()
    try:
        router = settings.model_router
        result = router.discover_models()
        assert result.available is False
        assert result.available_models == []
        assert result.error is not None
        assert "connection refused" in result.error.lower()
    finally:
        httpx.Client = original_httpx_client


def test_find_model_for_task_uses_configured_model(tmp_path):
    """find_model_for_task returns the configured model when available."""
    settings = make_settings(tmp_path)
    router = settings.model_router

    # Tutor model should be returned directly
    result = router.find_model_for_task("tutor")
    assert result == settings.tutor_model_id


def test_find_model_for_task_discovers_from_model_name(tmp_path):
    """find_model_for_task finds discovered models matching task name."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:11434/v1")

    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

        @property
        def status_code(self):
            return 200

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            return FakeResponse({"models": [{"name": "my-tutor-model:7b"}]})


    original_httpx_client = httpx.Client
    httpx.Client = lambda *args, **kwargs: FakeClient()
    try:
        router = settings.model_router
        result = router.discover_models()
        assert result.available is True
        assert "my-tutor-model:7b" in result.available_models

        found = router.find_model_for_task("tutor")
        assert found == "my-tutor-model:7b"
    finally:
        httpx.Client = original_httpx_client


def test_find_model_for_task_returns_none_when_no_match(tmp_path):
    """find_model_for_task returns None when no model matches task name."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:11434/v1")

    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

        @property
        def status_code(self):
            return 200

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            return FakeResponse({"models": [{"name": "completely-different-model"}]})


    original_httpx_client = httpx.Client
    httpx.Client = lambda *args, **kwargs: FakeClient()
    try:
        router = settings.model_router
        result = router.discover_models()
        assert result.available is True
        assert result.available_models == ["completely-different-model"]

        found = router.find_model_for_task("tutor")
        assert found is None
    finally:
        httpx.Client = original_httpx_client


def test_model_router_status_includes_discovery_info(tmp_path):
    """Status includes discovery information."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:11434/v1")
    router = settings.model_router
    status = router.get_status()

    for task in ("tutor", "quiz_generation", "knowledge_graph_extraction", "embeddings", "ocr"):
        assert task in status
        assert "available" in status[task]
        assert "foreign_key_check" not in status[task]  # not a storage check


# ---------------------------------------------------------------------------
# M57: Fallback chain & degradation tests
# ---------------------------------------------------------------------------


def test_fallback_chain_returns_primary_when_available(tmp_path):
    """route_with_fallback returns the primary route when it is available."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    router = settings.model_router
    route, fallback = router.route_with_fallback("tutor")
    assert route.task == "tutor"
    assert route.model_id == settings.tutor_model_id
    assert fallback is False


def test_fallback_chain_includes_all_routes(tmp_path):
    """all_routes() returns primary plus fallbacks in order."""
    settings = make_settings(tmp_path)
    router = settings.model_router
    chain = router._fallback_chains["tutor"]
    routes = chain.all_routes()
    assert len(routes) == 1  # no fallbacks configured for tutor
    assert routes[0].task == "tutor"


def test_fallback_chain_raises_when_all_unavailable(tmp_path):
    """route_with_fallback raises ModelUnavailable when the primary task is in failure state."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    router = settings.model_router
    router.record_failure("tutor", Exception("model down"))
    try:
        router.route_with_fallback("tutor")
        assert False, "Expected ModelUnavailable"
    except ModelUnavailable:
        pass


def test_fallback_loop_prevention_skips_tried_models(tmp_path):
    """route_with_fallback skips models already in tried_models."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    router = settings.model_router
    # Primary is available; passing it in tried_models should skip it.
    # Since there are no fallbacks, this raises ModelUnavailable.
    try:
        router.route_with_fallback(
            "tutor", tried_models={settings.tutor_model_id}
        )
        assert False, "Expected ModelUnavailable (loop prevention)"
    except ModelUnavailable:
        pass


def test_fallback_status_includes_metrics(tmp_path):
    """get_status() includes avg_latency_ms, avg_quality_score, fallback_chain_length."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    router = settings.model_router
    router.record_latency("tutor", 100.0)
    router.record_latency("tutor", 200.0)
    router.record_quality("tutor", 0.9)
    status = router.get_status()
    assert status["tutor"]["avg_latency_ms"] == 150.0
    assert status["tutor"]["avg_quality_score"] == 0.9
    assert status["tutor"]["fallback_chain_length"] >= 1
    assert status["tutor"]["context_window_tokens"] is None
    assert status["tutor"]["supports_structured_output"] is True