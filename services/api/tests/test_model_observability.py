"""M45: AI Observability & Model Evaluation Dashboard tests.

Tests cover:
* Model observability recording and snapshot aggregation
* Privacy-safe telemetry (no user IDs, document IDs, prompts, secrets)
* Admin authorization on diagnostic endpoints
* Metric aggregation (success/failure rates, fallback tracking)
* Empty datasets and bounded retention
* Quality evaluation integration (offline, local only)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.model_observability import (
    ModelObservability,
    get_model_observability,
)
from app.storage import initialize_storage


def _make_admin_client(tmp_path, admin_username="obs-admin"):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "obs-admin.sqlite3",
        rate_limit_enabled=False,
        admin_usernames=admin_username,
    )
    initialize_storage(settings.database_path)
    client = TestClient(create_app(settings))
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    client.post(
        "/api/v1/auth/register",
        json={"username": admin_username, "password": "secret-456-abc"},
    )
    client.post(
        "/api/v1/auth/login",
        json={"username": admin_username, "password": "secret-456-abc"},
    )
    return client


def _make_user_client(tmp_path, username="obs-user"):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "obs-user.sqlite3",
        rate_limit_enabled=False,
    )
    initialize_storage(settings.database_path)
    client = TestClient(create_app(settings))
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "secret-456-abc"},
    )
    client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "secret-456-abc"},
    )
    return client


# ---------------------------------------------------------------------------
# ModelObservability unit tests
# ---------------------------------------------------------------------------


def test_observability_records_and_aggregates_outcomes():
    obs = ModelObservability(max_records=100)
    obs.record(
        task_type="tutor",
        provider="local",
        model_id="local-tutor",
        outcome="success",
        latency_ms=120.5,
        fallback_occurred=False,
        citation_validation="valid",
        abstention=False,
    )
    obs.record(
        task_type="tutor",
        provider="local",
        model_id="local-tutor",
        outcome="timeout",
        latency_ms=90000.0,
        fallback_occurred=True,
        citation_validation=None,
        abstention=True,
    )
    obs.record(
        task_type="quiz",
        provider="local",
        model_id="local-tutor",
        outcome="success",
        latency_ms=50.0,
        fallback_occurred=False,
        citation_validation="valid",
        abstention=False,
    )

    snapshot = obs.snapshot()
    assert snapshot["total_observations"] == 3
    assert snapshot["task_counts"]["tutor"] == 2
    assert snapshot["task_counts"]["quiz"] == 1
    assert snapshot["outcome_counts"]["success"] == 2
    assert snapshot["outcome_counts"]["timeout"] == 1
    assert snapshot["success_rate"] == pytest.approx(0.6667, abs=1e-4)
    assert snapshot["failure_rate"] == pytest.approx(0.3333, abs=1e-4)
    assert snapshot["fallback_count"] == 1
    assert snapshot["fallback_rate"] == pytest.approx(0.3333, abs=1e-4)
    assert snapshot["latency_ms"]["count"] == 3
    assert snapshot["latency_ms"]["max_ms"] == 90000.0
    assert len(snapshot["recent_failures"]) == 1
    assert snapshot["recent_failures"][0]["outcome"] == "timeout"


def test_observability_empty_snapshot():
    obs = ModelObservability(max_records=100)
    snapshot = obs.snapshot()
    assert snapshot["total_observations"] == 0
    assert snapshot["task_counts"] == {}
    assert snapshot["outcome_counts"] == {}
    assert snapshot["success_rate"] is None
    assert snapshot["failure_rate"] is None
    assert snapshot["fallback_count"] == 0
    assert snapshot["fallback_rate"] is None
    assert snapshot["latency_ms"]["count"] == 0
    assert snapshot["recent_failures"] == []


def test_observability_bounded_retention():
    obs = ModelObservability(max_records=3)
    for i in range(5):
        obs.record(
            task_type="tutor",
            provider="local",
            model_id="local-tutor",
            outcome="success",
            latency_ms=float(i),
            fallback_occurred=False,
        )
    snapshot = obs.snapshot()
    assert snapshot["total_observations"] == 3
    # Oldest two should have been dropped; latencies should be 2, 3, 4.
    assert snapshot["latency_ms"]["max_ms"] == 4.0
    assert snapshot["latency_ms"]["p50_ms"] == 3.0


def test_observability_no_private_data_stored():
    obs = ModelObservability(max_records=100)
    # Record with fields that *look* like private data but are only metadata.
    obs.record(
        task_type="tutor",
        provider="local",
        model_id="local-tutor",
        outcome="success",
        latency_ms=10.0,
        fallback_occurred=False,
    )
    snapshot = obs.snapshot()
    payload = str(snapshot)
    # No user IDs, document IDs, prompts, or secrets should appear.
    assert "userId" not in payload
    assert "documentId" not in payload
    assert "prompt" not in payload.lower()
    assert "secret" not in payload.lower()
    assert "authorization" not in payload.lower()


def test_get_model_observability_is_singleton():
    first = get_model_observability()
    second = get_model_observability()
    assert first is second


# ---------------------------------------------------------------------------
# API route tests (admin authorization)
# ---------------------------------------------------------------------------


def test_observability_snapshot_requires_admin(tmp_path):
    client = _make_user_client(tmp_path)
    response = client.get("/api/v1/admin/models/observability/snapshot")
    assert response.status_code == 403


def test_observability_snapshot_returns_200_for_admin(tmp_path):
    client = _make_admin_client(tmp_path)
    response = client.get("/api/v1/admin/models/observability/snapshot")
    assert response.status_code == 200
    payload = response.json()
    assert "totalObservations" in payload
    assert "taskCounts" in payload
    assert "outcomeCounts" in payload
    assert "latencyMs" in payload
    assert "recentFailures" in payload


def test_observability_evaluate_requires_admin(tmp_path):
    client = _make_user_client(tmp_path)
    response = client.post(
        "/api/v1/admin/models/evaluate",
        json={
            "taskType": "tutor",
            "dataset": "retrieval_eval",
            "metric": "retrieval_quality",
        },
    )
    assert response.status_code == 403


def test_observability_evaluate_unknown_metric_returns_422(tmp_path):
    client = _make_admin_client(tmp_path)
    response = client.post(
        "/api/v1/admin/models/evaluate",
        json={
            "taskType": "tutor",
            "dataset": "retrieval_eval",
            "metric": "nonexistent_metric",
        },
    )
    assert response.status_code == 422


def test_observability_evaluate_retrieval_quality_returns_metrics(tmp_path):
    client = _make_admin_client(tmp_path)
    response = client.post(
        "/api/v1/admin/models/evaluate",
        json={
            "taskType": "tutor",
            "dataset": "retrieval_eval",
            "metric": "retrieval_quality",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["taskType"] == "tutor"
    assert payload["dataset"] == "retrieval_eval"
    assert payload["metric"] == "retrieval_quality"
    assert payload["evaluatedAt"] is not None
    assert isinstance(payload["details"], dict)


def test_observability_evaluate_model_failure_rate(tmp_path):
    client = _make_admin_client(tmp_path)
    response = client.post(
        "/api/v1/admin/models/evaluate",
        json={
            "taskType": "tutor",
            "dataset": "observability",
            "metric": "model_failure_rate",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["metric"] == "model_failure_rate"
    assert "value" in payload
    assert isinstance(payload["details"], dict)


def test_observability_evaluate_citation_validity(tmp_path):
    client = _make_admin_client(tmp_path)
    response = client.post(
        "/api/v1/admin/models/evaluate",
        json={
            "taskType": "tutor",
            "dataset": "retrieval_eval",
            "metric": "citation_validity",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["metric"] == "citation_validity"


def test_observability_evaluate_evidence_coverage(tmp_path):
    client = _make_admin_client(tmp_path)
    response = client.post(
        "/api/v1/admin/models/evaluate",
        json={
            "taskType": "tutor",
            "dataset": "retrieval_eval",
            "metric": "evidence_coverage",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["metric"] == "evidence_coverage"


def test_observability_evaluate_quiz_schema_validity(tmp_path):
    client = _make_admin_client(tmp_path)
    response = client.post(
        "/api/v1/admin/models/evaluate",
        json={
            "taskType": "quiz",
            "dataset": "quality_v2",
            "metric": "quiz_schema_validity",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["metric"] == "quiz_schema_validity"


# ---------------------------------------------------------------------------
# Cross-user isolation: telemetry contains no user-scoped data
# ---------------------------------------------------------------------------


def test_telemetry_cross_user_isolation(tmp_path):
    # Two separate users generate telemetry; the admin snapshot must not
    # contain user-scoped identifiers.
    admin_client = _make_admin_client(tmp_path, admin_username="iso-admin")

    # Record observations directly (simulating two different workflows).
    obs = get_model_observability()
    obs.record(
        task_type="tutor",
        provider="local",
        model_id="local-tutor",
        outcome="success",
        latency_ms=10.0,
        fallback_occurred=False,
    )
    obs.record(
        task_type="quiz",
        provider="local",
        model_id="local-tutor",
        outcome="failure",
        latency_ms=500.0,
        fallback_occurred=False,
    )

    response = admin_client.get("/api/v1/admin/models/observability/snapshot")
    assert response.status_code == 200
    payload = response.json()
    serialized = str(payload)
    assert "userId" not in serialized
    assert "documentId" not in serialized
    assert payload["totalObservations"] == 2
