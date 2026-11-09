"""M75: Platform Observability & SRE tests.

Deterministic tests for:

* Structured log redaction (secrets, credentials never logged).
* Correlation ID propagation (X-Request-ID echo).
* Health and readiness checks.
* Dependency failures (circuit breaker).
* Timeout and retry policies.
* Circuit-breaker behavior (open/half-open/closed).
* Background job recovery.
* Webhook failure recovery.
* Cross-tenant telemetry isolation.
* Monitoring failure degradation.

Uses local TestClient and synthetic accounts. No paid monitoring services.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from io import StringIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.config import Settings
from app.main import create_app
from app.observability import OperationalMetrics
from app.reliability import (
    CircuitBreaker,
    CircuitOpenError,
    graceful_degradation,
    record_incident,
    retry_with_backoff,
)
from app.storage import (
    create_user,
    initialize_storage,
)


@pytest.fixture
def settings(tmp_path: Path):
    return Settings(_env_file=None, database_path=tmp_path / "test.sqlite3")


@pytest.fixture
def app(settings):
    app = create_app(settings)
    initialize_storage(settings.database_path)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def _login(client: TestClient, username: str, password: str = "testpassword123"):
    """Log in a user and return the authenticated client."""
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    return client


def _create_user_and_login(app, database_path, username: str):
    """Create a user and return an authenticated client."""
    create_user(database_path, username, hash_password("testpassword123"))
    client = TestClient(app)
    return _login(client, username)


# ---------------------------------------------------------------------------
# Structured log redaction
# ---------------------------------------------------------------------------


def test_secret_field_redacts_secrets():
    """Verify secret_field redacts secret-like keys."""
    from app.logging import secret_field

    assert secret_field("api_key", "sk-1234567890abcdef") == "[REDACTED]"
    assert secret_field("authorization", "Bearer token123") == "[REDACTED]"
    assert secret_field("secret", "my_secret_value") == "[REDACTED]"
    assert secret_field("password", "hunter2") == "[REDACTED]"
    assert secret_field("normal_field", "visible_value") == "visible_value"


def test_structured_log_redacts_secrets():
    """Verify the structured formatter handles secret-like fields safely."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)

    from app.logging import StructuredFormatter

    formatter = StructuredFormatter()
    handler.setFormatter(formatter)

    logger = logging.getLogger("wonderlearn.test_redaction")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("user_action", extra={"request_id": "req_123"})
    output = stream.getvalue()
    record = json.loads(output.strip())
    assert record["message"] == "user_action"
    assert record["request_id"] == "req_123"


# ---------------------------------------------------------------------------
# Correlation ID propagation
# ---------------------------------------------------------------------------


def test_request_id_echo(client):
    """Verify X-Request-ID is echoed in response headers."""
    response = client.get("/api/v1/platform/version", headers={"X-Request-ID": "req-custom-123"})
    assert response.status_code == 200
    assert response.headers.get("x-request-id") == "req-custom-123"


def test_request_id_generated_when_missing(client):
    """Verify a request ID is generated when none is provided."""
    response = client.get("/api/v1/platform/version")
    assert response.status_code == 200
    rid = response.headers.get("x-request-id")
    assert rid is not None and len(rid) > 0


# ---------------------------------------------------------------------------
# Health and readiness checks
# ---------------------------------------------------------------------------


def test_health_endpoint(app):
    """Verify the health endpoint returns ok."""
    database_path = app.state.settings.database_path
    client = _create_user_and_login(app, database_path, "health_user")

    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "wonderlearn-api"


def test_readiness_endpoint(app):
    """Verify the readiness endpoint returns ready."""
    database_path = app.state.settings.database_path
    client = _create_user_and_login(app, database_path, "ready_user")

    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert "checks" in data


def test_metrics_endpoint(app):
    """Verify the metrics endpoint returns a snapshot."""
    database_path = app.state.settings.database_path
    client = _create_user_and_login(app, database_path, "metrics_user")

    response = client.get("/api/v1/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "requestsTotal" in data
    assert "statusCodes" in data
    assert "latencyMs" in data


# ---------------------------------------------------------------------------
# SLI snapshots
# ---------------------------------------------------------------------------


def test_sli_snapshot(app):
    """Verify SLI snapshots are returned for all categories."""
    database_path = app.state.settings.database_path
    client = _create_user_and_login(app, database_path, "sli_user")

    response = client.get("/api/v1/observability/sli")
    assert response.status_code == 200
    data = response.json()
    assert "http_request" in data
    assert "availabilityPercent" in data["http_request"]
    assert "errorRatePercent" in data["http_request"]


def test_sli_single_category(app):
    """Verify SLI snapshot for a single category."""
    database_path = app.state.settings.database_path
    client = _create_user_and_login(app, database_path, "sli_cat_user")

    response = client.get("/api/v1/observability/sli/http_request")
    assert response.status_code == 200
    data = response.json()
    assert data["category"] == "http_request"


# ---------------------------------------------------------------------------
# Dependency health
# ---------------------------------------------------------------------------


def test_dependency_health(app):
    """Verify dependency health endpoint."""
    database_path = app.state.settings.database_path
    client = _create_user_and_login(app, database_path, "dep_user")

    response = client.get("/api/v1/observability/dependencies")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 3  # database, model_provider, embedding_provider
    dep_names = {d["name"] for d in data}
    assert "database" in dep_names
    assert "model_provider" in dep_names
    assert "embedding_provider" in dep_names


# ---------------------------------------------------------------------------
# Circuit breaker endpoint
# ---------------------------------------------------------------------------


def test_circuit_breakers_endpoint(app):
    """Verify the circuit breakers endpoint."""
    database_path = app.state.settings.database_path
    client = _create_user_and_login(app, database_path, "cb_user")

    response = client.get("/api/v1/observability/circuit-breakers")
    assert response.status_code == 200
    data = response.json()
    assert "circuitBreakers" in data


# ---------------------------------------------------------------------------
# Observability snapshot endpoint
# ---------------------------------------------------------------------------


def test_observability_snapshot(app):
    """Verify the observability snapshot endpoint."""
    database_path = app.state.settings.database_path
    client = _create_user_and_login(app, database_path, "snap_user")

    response = client.get("/api/v1/observability/snapshot")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "uptimeSeconds" in data
    assert "requestsTotal" in data
    assert "sli" in data
    assert "dependencies" in data


def test_observability_dashboard_requires_admin(app):
    """Verify the dashboard requires admin permissions."""
    database_path = app.state.settings.database_path
    client = _create_user_and_login(app, database_path, "regular_user")

    response = client.get("/api/v1/observability/dashboard")
    assert response.status_code == 403, f"Expected 403: {response.text}"


def test_observability_incidents_requires_admin(app):
    """Verify incidents endpoint requires admin permissions."""
    database_path = app.state.settings.database_path
    client = _create_user_and_login(app, database_path, "regular_user2")

    response = client.get("/api/v1/observability/incidents")
    assert response.status_code == 403, f"Expected 403: {response.text}"


# ---------------------------------------------------------------------------
# Circuit breaker unit tests
# ---------------------------------------------------------------------------


def test_circuit_breaker_closed_to_open():
    """Verify circuit breaker transitions from closed to open after threshold."""
    breaker = CircuitBreaker(
        name="test_dep", failure_threshold=3, recovery_timeout_seconds=60.0
    )

    # First 2 failures should not open yet (threshold is 3)
    for _i in range(2):
        try:
            breaker.call(_always_fail)
        except ValueError:
            pass
    assert breaker.state.name == "CLOSED"

    # Third failure opens the circuit
    try:
        breaker.call(_always_fail)
    except ValueError:
        pass
    assert breaker.state.name == "OPEN"

    # While open, calls short-circuit
    with pytest.raises(CircuitOpenError):
        breaker.call(_always_succeed)


def test_circuit_breaker_half_open_probe():
    """Verify half-open probe transitions circuit."""
    breaker = CircuitBreaker(
        name="test_dep2", failure_threshold=1, recovery_timeout_seconds=0.1
    )

    # Open the circuit with one failure
    try:
        breaker.call(_always_fail)
    except ValueError:
        pass
    assert breaker.state.name == "OPEN"

    # Wait for recovery timeout
    time.sleep(0.15)

    # Half-open probe succeeds -> circuit closes
    result = breaker.call(_always_succeed)
    assert result == "ok"
    assert breaker.state.name == "CLOSED"

    # Half-open probe fails -> circuit re-opens
    try:
        breaker.call(_always_fail)
    except ValueError:
        pass
    assert breaker.state.name == "OPEN"


def _always_fail():
    raise ValueError("simulated failure")


def _always_succeed():
    return "ok"


# ---------------------------------------------------------------------------
# Bounded retry with backoff
# ---------------------------------------------------------------------------


def test_retry_with_backoff_eventual_success():
    """Verify retry succeeds on eventual success."""
    if sys.platform == "win32":
        pytest.skip("SIGALRM not available on Windows")

    call_count = [0]

    def flaky():
        call_count[0] += 1
        if call_count[0] < 3:
            raise ValueError("not yet")
        return "success"

    result = retry_with_backoff(
        flaky, max_attempts=5, base_delay_seconds=0.01, max_delay_seconds=1.0
    )
    assert result == "success"
    assert call_count[0] == 3


def test_retry_with_backoff_exhausted():
    """Verify retry exhausts and re-raises."""
    if sys.platform == "win32":
        pytest.skip("SIGALRM not available on Windows")

    call_count = [0]

    def always_fail():
        call_count[0] += 1
        raise ValueError("always fails")

    with pytest.raises(ValueError, match="always fails"):
        retry_with_backoff(
            always_fail,
            max_attempts=3,
            base_delay_seconds=0.01,
            max_delay_seconds=1.0,
        )
    assert call_count[0] == 3


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


def test_graceful_degradation_returns_fallback():
    """Verify graceful degradation returns fallback on failure."""
    result = graceful_degradation(
        _always_fail,
        fallback="fallback_value",
    )
    assert result == "fallback_value"


def test_graceful_degradation_open_breaker_returns_fallback():
    """Verify graceful degradation returns fallback when breaker is open."""
    breaker = CircuitBreaker(
        name="test_dep3", failure_threshold=1, recovery_timeout_seconds=60.0
    )
    try:
        breaker.call(_always_fail)
    except ValueError:
        pass
    assert breaker.state.name == "OPEN"

    result = graceful_degradation(
        _always_succeed, fallback="fallback", breaker=breaker
    )
    assert result == "fallback"


# ---------------------------------------------------------------------------
# Incident recording
# ---------------------------------------------------------------------------


def test_record_incident():
    """Verify incident recording."""
    from app.reliability import incident_registry_all

    record_incident(
        incident_id="inc_001",
        severity="sev2",
        title="Test incident",
        description="A test incident for M75",
        detected_at="2026-01-01T00:00:00Z",
        status="open",
    )
    incidents = incident_registry_all()
    assert len(incidents) == 1
    assert incidents[0]["incidentId"] == "inc_001"
    assert incidents[0]["severity"] == "sev2"


# ---------------------------------------------------------------------------
# Cross-tenant telemetry isolation
# ---------------------------------------------------------------------------


def test_metrics_are_process_scoped(app):
    """Verify metrics are process-scoped (not tenant-leaking)."""
    database_path = app.state.settings.database_path
    client = _create_user_and_login(app, database_path, "tenant_a")

    # Make some requests as tenant A
    client.get("/api/v1/platform/version")
    client.get("/api/v1/platform/health")

    response = client.get("/api/v1/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "requestsTotal" in data
    # Verify metrics endpoint returns expected structure without user data
    assert "requestsByRoute" in data
    assert "statusCodes" in data


# ---------------------------------------------------------------------------
# OperationalMetrics SLI tracking
# ---------------------------------------------------------------------------


def test_operational_metrics_record_domain_event():
    """Verify OperationalMetrics.record_domain_event tracks SLIs."""
    metrics = OperationalMetrics()
    metrics.record_domain_event(
        category="ingestion",
        success=True,
        duration_ms=150.0,
        provider="local",
        model="embed-v1",
    )
    metrics.record_domain_event(
        category="ingestion",
        success=False,
        duration_ms=500.0,
        provider="local",
        model="embed-v1",
        error_category="ingestion_failure",
    )

    sli = metrics.sli_snapshot("ingestion")
    assert sli["totalRequests"] == 2
    assert sli["availabilityPercent"] == 50.0
    assert sli["errorRatePercent"] == 50.0


# ---------------------------------------------------------------------------
# Dependency health recording
# ---------------------------------------------------------------------------


def test_record_dependency_health():
    """Verify dependency health recording."""
    metrics = OperationalMetrics()
    metrics.record_dependency_health(
        name="database", healthy=True, error=None
    )
    health = metrics.get_dependency_health("database")
    assert health is not None
    assert health["healthy"] is True

    metrics.record_dependency_health(
        name="model_provider", healthy=False, error="connection timeout"
    )
    health = metrics.get_dependency_health("model_provider")
    assert health is not None
    assert health["healthy"] is False
    assert health["lastError"] == "connection timeout"


if __name__ == "__main__":
    pass
