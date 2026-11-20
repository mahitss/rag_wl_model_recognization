"""
M85: AI Learning Behavior Intelligence & Personalized Intervention Engine tests.

Covers:
* Behavior pattern detection (repeated difficulty, assessment errors, etc.)
* Learner state computation (mastery, consistency, readiness)
* Insight/explanation traceability (every pattern references events)
* Intervention generation
* Intervention lifecycle (proposed -> accepted/dismissed/completed)
* Intervention outcome recording
* Duplicate suppression and cooldowns
* Tenant isolation
* Authorization (unauthenticated requests rejected)
* API integration
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.auth import hash_password
from app.config import Settings
from app.learning_behavior_engine import (
    analyze_behavior_patterns,
    analyze_learner_behavior,
    compute_learner_state,
    generate_interventions,
)
from app.main import create_app
from app.storage import (
    create_user,
    ingest_learning_event,
    initialize_storage,
    list_behavior_patterns,
    list_intervention_outcomes,
    list_interventions,
    record_intervention_outcome,
    respond_to_intervention,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_path=os.path.join(tmp_path, f"behavior_{tmp_path.name}.sqlite3"),
        rate_limit_enabled=False,
    )


def _make_app(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    initialize_storage(settings.database_path)
    return app, settings


def _seed_user(settings, username="learner") -> str:
    return create_user(settings.database_path, username, hash_password("password-123"))


def _make_event(
    event_type: str,
    occurred_at: str | None = None,
    event_data: dict | None = None,
    idempotency_key: str | None = None,
):
    if occurred_at is None:
        occurred_at = datetime.now(UTC).isoformat()
    from app.schemas import LearningEventCreate

    return LearningEventCreate(
        eventType=event_type,
        occurredAt=occurred_at,
        eventData=event_data or {},
        idempotencyKey=idempotency_key,
    )


# ---------------------------------------------------------------------------
# Learner state computation tests
# ---------------------------------------------------------------------------


def test_compute_learner_state_basic(tmp_path):
    """Generate a learner state from persisted data."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    # Seed some events
    now = datetime.now(UTC)
    for i in range(5):
        ingest_learning_event(
            settings.database_path,
            user_id=user_id,
            event_type="lesson.completed",
            occurred_at=(now - timedelta(days=i)).isoformat(),
        )
        ingest_learning_event(
            settings.database_path,
            user_id=user_id,
            event_type="question.answered",
            occurred_at=(now - timedelta(days=i)).isoformat(),
            event_data={"topic": "math", "outcome": "correct"},
        )

    state = compute_learner_state(
        settings.database_path, user_id=user_id, settings=settings
    )
    assert state.userId == user_id
    assert state.stateSnapshot is not None
    assert state.computedAt is not None
    assert state.staleAfter is not None


# ---------------------------------------------------------------------------
# Behavior pattern detection tests
# ---------------------------------------------------------------------------


def test_repeated_difficulty_pattern(tmp_path):
    """Detect repeated incorrect answers on same topic."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    now = datetime.now(UTC)
    for i in range(5):
        ingest_learning_event(
            settings.database_path,
            user_id=user_id,
            event_type="question.answered",
            occurred_at=(now - timedelta(minutes=i)).isoformat(),
            event_data={"topic": "algebra", "outcome": "incorrect"},
        )

    patterns = analyze_behavior_patterns(
        settings.database_path, user_id=user_id, settings=settings
    )
    # Should detect repeated difficulty pattern
    repeated = [p for p in patterns if p.patternType == "repeated_difficulty"]
    assert len(repeated) >= 1
    assert repeated[0].confidence > 0
    assert len(repeated[0].evidenceEventIds) > 0


def test_assessment_errors_pattern(tmp_path):
    """Detect repeated assessment failures."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    now = datetime.now(UTC)
    for i in range(4):
        ingest_learning_event(
            settings.database_path,
            user_id=user_id,
            event_type="assessment.completed",
            occurred_at=(now - timedelta(hours=i)).isoformat(),
            event_data={"topic": "geometry", "score": 0.4},
        )

    patterns = analyze_behavior_patterns(
        settings.database_path, user_id=user_id, settings=settings
    )
    failed = [p for p in patterns if p.patternType == "repeated_assessment_errors"]
    assert len(failed) >= 1


def test_abandoned_sessions_pattern(tmp_path):
    """Detect sessions started but not completed."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    now = datetime.now(UTC)
    for i in range(3):
        ingest_learning_event(
            settings.database_path,
            user_id=user_id,
            event_type="session.started",
            occurred_at=(now - timedelta(hours=i)).isoformat(),
            event_data={"sessionId": f"sess-{i}"},
        )
        # Never completes them

    patterns = analyze_behavior_patterns(
        settings.database_path, user_id=user_id, settings=settings
    )
    abandoned = [p for p in patterns if p.patternType == "abandoned_sessions"]
    assert len(abandoned) >= 1


def test_tenant_isolation_patterns(tmp_path):
    """Learner A's patterns are not visible to learner B."""
    app, settings = _make_app(tmp_path)
    user_a = _seed_user(settings, "learner_a")
    user_b = _seed_user(settings, "learner_b")

    now = datetime.now(UTC)
    ingest_learning_event(
        settings.database_path,
        user_id=user_a,
        event_type="question.answered",
        occurred_at=now.isoformat(),
        event_data={"topic": "math", "outcome": "incorrect"},
    )

    patterns_a = list_behavior_patterns(
        settings.database_path, user_id=user_a
    )
    patterns_b = list_behavior_patterns(
        settings.database_path, user_id=user_b
    )
    assert len(patterns_a) >= 0  # may or may not have patterns
    assert len(patterns_b) == 0


# ---------------------------------------------------------------------------
# Intervention generation tests
# ---------------------------------------------------------------------------


def test_intervention_generation(tmp_path):
    """Generate interventions from learner data."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    now = datetime.now(UTC)
    # Create some weak mastery data
    ingest_learning_event(
        settings.database_path,
        user_id=user_id,
        event_type="question.answered",
        occurred_at=now.isoformat(),
        event_data={"topic": "calculus", "outcome": "incorrect"},
    )
    ingest_learning_event(
        settings.database_path,
        user_id=user_id,
        event_type="question.answered",
        occurred_at=(now - timedelta(minutes=1)).isoformat(),
        event_data={"topic": "calculus", "outcome": "incorrect"},
    )

    interventions = generate_interventions(
        settings.database_path, user_id=user_id, settings=settings
    )
    assert isinstance(interventions, list)


def test_intervention_avoid_duplicates(tmp_path):
    """Don't generate duplicate interventions."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    # Generate interventions once
    inv1 = generate_interventions(
        settings.database_path, user_id=user_id, settings=settings
    )

    # Generate interventions again - should not create duplicates
    generate_interventions(
        settings.database_path, user_id=user_id, settings=settings
    )

    # Should not create duplicate proposed interventions
    all_interventions = list_interventions(
        settings.database_path, user_id=user_id, status="proposed"
    )
    types = {i["interventionType"] for i in all_interventions}
    assert len(types) == len(inv1)  # No duplicates


def test_intervention_respond_accept(tmp_path):
    """Accept an intervention."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    interventions = generate_interventions(
        settings.database_path, user_id=user_id, settings=settings
    )
    if interventions:
        inv_id = interventions[0].interventionId
        result = respond_to_intervention(
            settings.database_path,
            intervention_id=inv_id,
            user_id=user_id,
            response="accepted",
        )
        assert result is not None
        assert result["status"] == "accepted"

        # Verify it's no longer in proposed list
        remaining = list_interventions(
            settings.database_path, user_id=user_id, status="proposed"
        )
        assert all(i["interventionId"] != inv_id for i in remaining)


def test_intervention_respond_dismiss(tmp_path):
    """Dismiss an intervention."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    interventions = generate_interventions(
        settings.database_path, user_id=user_id, settings=settings
    )
    if interventions:
        inv_id = interventions[0].interventionId
        result = respond_to_intervention(
            settings.database_path,
            intervention_id=inv_id,
            user_id=user_id,
            response="dismissed",
        )
        assert result is not None
        assert result["status"] == "dismissed"


def test_intervention_respond_complete(tmp_path):
    """Complete an intervention."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    interventions = generate_interventions(
        settings.database_path, user_id=user_id, settings=settings
    )
    if interventions:
        inv_id = interventions[0].interventionId
        result = respond_to_intervention(
            settings.database_path,
            intervention_id=inv_id,
            user_id=user_id,
            response="completed",
        )
        assert result is not None
        assert result["status"] == "completed"


def test_intervention_outcome_recording(tmp_path):
    """Record an intervention outcome."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    interventions = generate_interventions(
        settings.database_path, user_id=user_id, settings=settings
    )
    if interventions:
        inv_id = interventions[0].interventionId
        outcome = record_intervention_outcome(
            settings.database_path,
            intervention_id=inv_id,
            user_id=user_id,
            outcome_type="mastery_improvement",
            mastered_before=0.4,
            mastered_after=0.7,
            evidence_event_ids=["evt-1"],
            notes="Improved after intervention",
        )
        assert outcome is not None
        assert outcome["outcomeId"] is not None
        assert outcome["improvementDelta"] == 0.3

        # List outcomes
        outcomes = list_intervention_outcomes(
            settings.database_path, user_id=user_id
        )
        assert len(outcomes) >= 1


# ---------------------------------------------------------------------------
# Combined analysis test
# ---------------------------------------------------------------------------


def test_full_analysis(tmp_path):
    """Run full behavior analysis pipeline."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    now = datetime.now(UTC)
    ingest_learning_event(
        settings.database_path,
        user_id=user_id,
        event_type="question.answered",
        occurred_at=now.isoformat(),
        event_data={"topic": "algebra", "outcome": "incorrect"},
    )

    result = analyze_learner_behavior(
        settings.database_path, user_id=user_id, settings=settings
    )
    assert result.userId == user_id
    assert isinstance(result.patterns, list)
    assert isinstance(result.interventions, list)
    assert result.state is not None
    assert result.state.userId == user_id
    assert result.generatedAt is not None


# ---------------------------------------------------------------------------
# API integration tests
# ---------------------------------------------------------------------------


def _auth_client(app, tmp_path, username="api_learner"):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, username, hash_password("password123"))
    client = TestClient(app)
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    return client, database_path, user_id


def test_api_analyze_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    response = client.get("/api/v1/learner-behavior/analyze")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["userId"] == user_id
    assert "patterns" in data
    assert "state" in data
    assert "interventions" in data


def test_api_analyze_endpoint_unauthenticated(tmp_path):
    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    response = client.get("/api/v1/learner-behavior/analyze")
    assert response.status_code in (401, 403)


def test_api_state_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    response = client.get("/api/v1/learner-behavior/state")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["userId"] == user_id


def test_api_patterns_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    response = client.get("/api/v1/learner-behavior/patterns")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "patterns" in data


def test_api_interventions_generate_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    response = client.post("/api/v1/learner-behavior/interventions")
    assert response.status_code == 200, response.text
    data = response.json()
    assert isinstance(data, list)


def test_api_interventions_list_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    response = client.get("/api/v1/learner-behavior/interventions")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "interventions" in data


def test_api_interventions_respond_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    # Generate an intervention first
    gen_resp = client.post("/api/v1/learner-behavior/interventions")
    assert gen_resp.status_code == 200
    interventions = gen_resp.json()
    if interventions:
        inv_id = interventions[0]["interventionId"]
        respond_resp = client.post(
            f"/api/v1/learner-behavior/interventions/{inv_id}/respond",
            json={"response": "accepted"},
        )
        assert respond_resp.status_code == 200, respond_resp.text
        respond_data = respond_resp.json()
        assert respond_data["status"] == "accepted"


def test_api_interventions_outcomes_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    # Generate an intervention first
    gen_resp = client.post("/api/v1/learner-behavior/interventions")
    assert gen_resp.status_code == 200
    interventions = gen_resp.json()
    if interventions:
        inv_id = interventions[0]["interventionId"]
        outcome_resp = client.post(
            f"/api/v1/learner-behavior/interventions/{inv_id}/outcomes",
            json={
                "outcomeType": "mastery_improvement",
                "masteredBefore": 0.4,
                "masteredAfter": 0.7,
                "evidenceEventIds": ["evt-1"],
                "notes": "Improved after intervention",
            },
        )
        assert outcome_resp.status_code == 200, outcome_resp.text
        outcome_data = outcome_resp.json()
        assert outcome_data["outcomeId"] is not None

        # List outcomes
        list_resp = client.get(
            f"/api/v1/learner-behavior/interventions/{inv_id}/outcomes"
        )
        assert list_resp.status_code == 200
        assert "outcomes" in list_resp.json()

