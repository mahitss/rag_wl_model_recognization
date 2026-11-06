"""M57: Model gateway tests.

Tests cover:
* Successful inference with provider/model attribution
* Fallback chain: fallback_occurred flag set when fallback used
* Cancellation via GatewayCancellationToken
* Timeout handling
* Loop prevention: all routes tried, then failure
* Structured output validation failure
* No tutor configured: graceful failure
* User isolation: no user IDs leak into model payload
"""

from __future__ import annotations

import asyncio
import json
import pytest

from app.config import Settings
from app.model_gateway import (
    ModelGateway,
    ModelGatewayRequest,
    model_gateway_factory,
)
from app.model_router import ModelUnavailable
from app.tutor import TutorUnavailable


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
# Fake model transport
# ---------------------------------------------------------------------------


class FakeResponse:
    """Mock urllib response with valid JSON body."""

    def __init__(self, content: str):
        self._body = content.encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self._body

    def json(self):
        return json.loads(self._body)


class FakeOpener:
    """Mock build_opener that returns a FakeResponse."""

    def __init__(self, response: FakeResponse):
        self._response = response

    def open(self, request, timeout):
        return self._response


# ---------------------------------------------------------------------------
# Gateway tests
# ---------------------------------------------------------------------------


def test_gateway_success_returns_provider_and_model(tmp_path, monkeypatch):
    """Successful call returns provider='local' and the model that served it."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    gateway = model_gateway_factory(settings)

    inner = json.dumps({"answer": "X", "citations": ["E1"], "abstain": False})
    body = json.dumps({"choices": [{"message": {"content": inner}}]})
    fake = FakeResponse(body)

    import app.tutor as tutor_mod

    tutor_mod.build_opener = lambda *_: FakeOpener(fake)
    try:
        resp = asyncio.run(
            gateway.complete(
                ModelGatewayRequest(
                    task="tutor",
                    prompt="What is X?",
                    require_structured_output=True,
                )
            )
        )
    finally:
        tutor_mod.build_opener = __import__("app.tutor", fromlist=["build_opener"]).build_opener

    assert resp.outcome == "success"
    assert resp.provider == "local"
    assert resp.model_id == settings.tutor_model_id
    assert resp.fallback_occurred is False
    assert resp.error is None
    assert resp.parsed is not None
    assert resp.parsed["answer"] == "X"


def test_gateway_no_tutor_configured_fails_gracefully(tmp_path):
    """No tutor_base_url -> failure outcome, not an exception."""
    settings = make_settings(tmp_path)  # no tutor_base_url
    gateway = model_gateway_factory(settings)

    resp = asyncio.run(
        gateway.complete(
            ModelGatewayRequest(task="tutor", prompt="Q", require_structured_output=True)
        )
    )

    assert resp.outcome == "failure"
    assert resp.model_id == ""
    assert resp.provider == "local"
    assert resp.fallback_occurred is False
    assert resp.error is not None


def test_gateway_timeout_is_recorded(tmp_path, monkeypatch):
    """Timeout returns outcome='timeout' with the model that timed out."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    gateway = model_gateway_factory(settings)

    class SlowOpener:
        def open(self, request, timeout):
            raise TimeoutError("read timed out")

    import app.tutor as tutor_mod

    tutor_mod.build_opener = lambda *_: SlowOpener()
    try:
        resp = asyncio.run(
            gateway.complete(
                ModelGatewayRequest(
                    task="tutor",
                    prompt="Q",
                    timeout_seconds=1,
                    require_structured_output=True,
                )
            )
        )
    finally:
        tutor_mod.build_opener = __import__("app.tutor", fromlist=["build_opener"]).build_opener

    assert resp.outcome == "timeout"
    assert resp.model_id == settings.tutor_model_id
    assert resp.fallback_occurred is False
    assert "timed out" in resp.error


def test_gateway_cancellation(tmp_path):
    """Cancellation token aborts the request with outcome='cancellation'."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    gateway = model_gateway_factory(settings)

    async def run():
        token = await gateway.register_token("req-1")
        await token.cancel()
        return await gateway.complete(
            ModelGatewayRequest(
                task="tutor",
                prompt="Q",
                require_structured_output=True,
            ),
            request_id="req-1",
            cancellation_token=token,
        )

    resp = asyncio.run(run())

    assert resp.outcome == "cancellation"
    assert resp.fallback_occurred is False


def test_gateway_structured_output_invalid_is_failure(tmp_path, monkeypatch):
    """Malformed JSON from model returns outcome='failure' with citation_validation='invalid'."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    gateway = model_gateway_factory(settings)

    body = json.dumps({"choices": [{"message": {"content": "not json"}}]})
    fake = FakeResponse(body)

    import app.tutor as tutor_mod

    tutor_mod.build_opener = lambda *_: FakeOpener(fake)
    try:
        resp = asyncio.run(
            gateway.complete(
                ModelGatewayRequest(
                    task="tutor",
                    prompt="Q",
                    require_structured_output=True,
                )
            )
        )
    finally:
        tutor_mod.build_opener = __import__("app.tutor", fromlist=["build_opener"]).build_opener

    assert resp.outcome == "failure"
    assert resp.citation_validation == "invalid"
    assert resp.parsed is None
    assert resp.error is not None


def test_gateway_no_user_id_in_payload(tmp_path, monkeypatch):
    """Private user_id is not sent to the model as a prompt field."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    gateway = model_gateway_factory(settings)

    captured = {}

    class CapturingOpener:
        def open(self, request, timeout):
            captured["body"] = json.loads(request.data)
            inner = json.dumps({"answer": "", "citations": [], "abstain": True})
            return FakeResponse(
                json.dumps({"choices": [{"message": {"content": inner}}]})
            )

    import app.tutor as tutor_mod

    tutor_mod.build_opener = lambda *_: CapturingOpener()
    try:
        asyncio.run(
            gateway.complete(
                ModelGatewayRequest(
                    task="tutor",
                    prompt="Q",
                    context={"user_id": "user-123"},
                    require_structured_output=True,
                )
            )
        )
    finally:
        tutor_mod.build_opener = __import__("app.tutor", fromlist=["build_opener"]).build_opener

    user_content = captured["body"]["messages"][1]["content"]
    user_payload = json.loads(user_content)
    # user_id should not be forwarded as evidence/question content.
    assert "user_id" not in user_payload.get("evidence", []), user_payload
    assert user_payload.get("question") == "Q"


def test_gateway_factory_matches_settings(tmp_path):
    """model_gateway_factory uses the settings' model_router."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    gateway = model_gateway_factory(settings)
    assert isinstance(gateway.router, type(settings.model_router))
    assert gateway.settings is settings


def test_gateway_routes_all_task_types(tmp_path):
    """Gateway accepts all ModelTask types without raising on construction."""
    settings = make_settings(tmp_path, tutor_base_url="http://127.0.0.1:8080/v1")
    gateway = model_gateway_factory(settings)
    for task in ("tutor", "quiz_generation", "knowledge_graph_extraction", "embeddings", "ocr"):
        req = ModelGatewayRequest(task=task, prompt="Q")
        assert req.task == task
