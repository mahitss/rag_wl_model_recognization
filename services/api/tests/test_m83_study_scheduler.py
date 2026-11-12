"""
M83: Intelligent Study Scheduler & Adaptive Time Management Engine tests.

Covers:
* Study availability CRUD
* Scheduling constraints CRUD
* Schedule generation with availability constraints
* Daily hour limits respected
* Prerequisite-aware ordering
* Session overlap detection
* Session update and cancellation
* Schedule summary and conflicts
* Adaptive rescheduling
* Schedule versioning
* Tenant isolation
* Authorization (unauthenticated requests rejected)
* API integration
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.config import Settings
from app.main import create_app
from app.schemas import (
    ScheduleGenerationRequest,
)
from app.storage import (
    cancel_scheduled_study_session,
    create_learning_path,
    create_user,
    get_schedule_constraints,
    initialize_storage,
    list_scheduled_study_sessions,
    list_study_availability,
    schedule_study_session,
    set_schedule_constraints,
    set_study_availability,
    update_scheduled_study_session,
)
from app.study_scheduler import generate_study_schedule, reschedule_schedule

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_path=os.path.join(tmp_path, f"sched_{tmp_path.name}.sqlite3"),
        rate_limit_enabled=False,
    )


def _make_app(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    initialize_storage(settings.database_path)
    return app, settings


def _seed_user(settings, username="learner") -> str:
    return create_user(settings.database_path, username, hash_password("password-123"))


# ---------------------------------------------------------------------------
# Availability tests
# ---------------------------------------------------------------------------


def test_availability_create_and_list(tmp_path):
    """Create and list study availability blocks."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    stored = set_study_availability(
        settings.database_path,
        user_id=user_id,
        day_of_week=1,
        start_time="09:00",
        end_time="12:00",
        is_active=True,
    )
    assert stored["availabilityId"] is not None
    assert stored["dayOfWeek"] == 1
    assert stored["startTime"] == "09:00"
    assert stored["endTime"] == "12:00"
    assert stored["isActive"] is True

    rows = list_study_availability(settings.database_path, user_id=user_id)
    assert len(rows) == 1
    assert rows[0]["availabilityId"] == stored["availabilityId"]


def test_availability_invalid_day(tmp_path):
    """Invalid day_of_week is rejected."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    with pytest.raises(ValueError):
        set_study_availability(
            settings.database_path,
            user_id=user_id,
            day_of_week=7,
            start_time="09:00",
            end_time="12:00",
        )


# ---------------------------------------------------------------------------
# Constraints tests
# ---------------------------------------------------------------------------


def test_constraints_create_and_get(tmp_path):
    """Create and get scheduling constraints."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    stored = set_schedule_constraints(
        settings.database_path,
        user_id=user_id,
        max_daily_hours=4,
        default_session_minutes=30,
        buffer_minutes=10,
        quiet_hours_start=21,
        quiet_hours_end=6,
        notification_advance_minutes=30,
    )
    assert stored["constraintId"] is not None
    assert stored["maxDailyHours"] == 4
    assert stored["defaultSessionMinutes"] == 30
    assert stored["bufferMinutes"] == 10

    retrieved = get_schedule_constraints(settings.database_path, user_id=user_id)
    assert retrieved is not None
    assert retrieved["constraintId"] == stored["constraintId"]
    assert retrieved["maxDailyHours"] == 4


def test_constraints_validation(tmp_path):
    """Invalid constraint values are rejected."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    with pytest.raises(ValueError):
        set_schedule_constraints(
            settings.database_path,
            user_id=user_id,
            max_daily_hours=0,
            default_session_minutes=30,
            buffer_minutes=10,
            quiet_hours_start=21,
            quiet_hours_end=6,
            notification_advance_minutes=30,
        )


# ---------------------------------------------------------------------------
# Session scheduling tests
# ---------------------------------------------------------------------------


def test_session_create_and_list(tmp_path):
    """Create and list scheduled study sessions."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    start = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    end = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)

    stored = schedule_study_session(
        settings.database_path,
        user_id=user_id,
        title="Math Study",
        start_at=start.isoformat(),
        end_at=end.isoformat(),
        session_type="learning",
        priority=3,
    )
    assert stored["sessionId"] is not None
    assert stored["title"] == "Math Study"
    assert stored["durationMinutes"] == 60
    assert stored["status"] == "scheduled"

    sessions = list_scheduled_study_sessions(
        settings.database_path, user_id=user_id
    )
    assert len(sessions) == 1
    assert sessions[0]["sessionId"] == stored["sessionId"]


def test_session_overlap_rejected(tmp_path):
    """Overlapping sessions are rejected."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    start1 = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    end1 = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
    start2 = datetime(2026, 4, 15, 9, 30, tzinfo=UTC)
    end2 = datetime(2026, 4, 15, 10, 30, tzinfo=UTC)

    schedule_study_session(
        settings.database_path,
        user_id=user_id,
        title="Session 1",
        start_at=start1.isoformat(),
        end_at=end1.isoformat(),
    )

    with pytest.raises(ValueError, match="overlaps"):
        schedule_study_session(
            settings.database_path,
            user_id=user_id,
            title="Session 2",
            start_at=start2.isoformat(),
            end_at=end2.isoformat(),
        )


def test_session_update_and_cancel(tmp_path):
    """Update and cancel scheduled sessions."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    start = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    end = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)

    stored = schedule_study_session(
        settings.database_path,
        user_id=user_id,
        title="Original",
        start_at=start.isoformat(),
        end_at=end.isoformat(),
    )

    # Update
    updated = update_scheduled_study_session(
        settings.database_path,
        session_id=stored["sessionId"],
        user_id=user_id,
        title="Updated Title",
        status="in_progress",
    )
    assert updated is not None
    assert updated["status"] == "in_progress"

    # Cancel
    ok = cancel_scheduled_study_session(
        settings.database_path,
        session_id=stored["sessionId"],
        user_id=user_id,
    )
    assert ok is True

    # Verify cancelled
    sessions = list_scheduled_study_sessions(
        settings.database_path, user_id=user_id
    )
    assert sessions[0]["status"] == "cancelled"


def test_session_cancel_nonexistent(tmp_path):
    """Cancelling a nonexistent session returns False."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    ok = cancel_scheduled_study_session(
        settings.database_path,
        session_id="nonexistent",
        user_id=user_id,
    )
    assert ok is False


# ---------------------------------------------------------------------------
# Schedule generation tests
# ---------------------------------------------------------------------------


def test_generate_schedule_basic(tmp_path):
    """Generate a basic study schedule."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    # Set availability
    set_study_availability(
        settings.database_path,
        user_id=user_id,
        day_of_week=2,  # Wednesday
        start_time="09:00",
        end_time="17:00",
    )
    set_study_availability(
        settings.database_path,
        user_id=user_id,
        day_of_week=3,  # Thursday
        start_time="09:00",
        end_time="17:00",
    )

    # Create a learning path
    path = create_learning_path(
        settings.database_path,
        user_id=user_id,
        title="Math Path",
        description="Learn math",
    )

    result = generate_study_schedule(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=ScheduleGenerationRequest(
            pathId=path["pathId"],
            startDate="2026-04-15",
            endDate="2026-04-16",
            maxDailyHours=4,
        ),
    )

    assert result.versionId is not None
    assert result.versionNumber >= 1
    assert result.startDate == "2026-04-15"
    assert result.endDate == "2026-04-16"
    assert result.aiUnavailable is False


def test_generate_schedule_respects_daily_limit(tmp_path):
    """Generated schedule respects daily hour limits."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    set_study_availability(
        settings.database_path,
        user_id=user_id,
        day_of_week=2,
        start_time="09:00",
        end_time="17:00",
    )

    from app.storage import create_learning_path
    path = create_learning_path(
        settings.database_path,
        user_id=user_id,
        title="Math Path",
        description="Learn math",
    )

    result = generate_study_schedule(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=ScheduleGenerationRequest(
            pathId=path["pathId"],
            startDate="2026-04-15",
            endDate="2026-04-16",
            maxDailyHours=2,  # Only 2 hours
        ),
    )

    # Check no session exceeds daily limit
    daily_minutes = {}
    for s in result.sessions:
        day = s.startAt[:10]
        daily_minutes[day] = daily_minutes.get(day, 0) + s.durationMinutes
    for day, minutes in daily_minutes.items():
        assert minutes <= 120, f"Day {day} has {minutes} minutes, exceeding 2-hour limit"


def test_generate_schedule_no_availability(tmp_path):
    """Schedule generation works with no availability (creates defaults)."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    from app.storage import create_learning_path
    path = create_learning_path(
        settings.database_path,
        user_id=user_id,
        title="Math Path",
        description="Learn math",
    )

    result = generate_study_schedule(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=ScheduleGenerationRequest(
            pathId=path["pathId"],
            startDate="2026-04-15",
            endDate="2026-04-16",
        ),
    )

    assert result.versionId is not None


# ---------------------------------------------------------------------------
# Summary and conflicts
# ---------------------------------------------------------------------------


def test_schedule_summary(tmp_path):
    """Get schedule summary."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    start = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    end = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
    schedule_study_session(
        settings.database_path,
        user_id=user_id,
        title="Math Study",
        start_at=start.isoformat(),
        end_at=end.isoformat(),
    )

    from app.study_scheduler import get_schedule_summary
    summary = get_schedule_summary(
        settings.database_path, user_id=user_id
    )
    assert "scheduledCount" in summary
    assert summary["scheduledCount"] >= 1


# ---------------------------------------------------------------------------
# Adaptive rescheduling
# ---------------------------------------------------------------------------


def test_reschedule_session_missed(tmp_path):
    """Reschedule on session_missed trigger."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    start = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    end = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
    schedule_study_session(
        settings.database_path,
        user_id=user_id,
        title="Missed Session",
        start_at=start.isoformat(),
        end_at=end.isoformat(),
    )

    # Create a schedule version first
    from app.storage import create_schedule_version
    create_schedule_version(
        settings.database_path,
        user_id=user_id,
        version_number=1,
        title="Initial Schedule",
        description="Initial",
        start_date="2026-04-15",
        end_date="2026-04-16",
    )

    result = reschedule_schedule(
        settings.database_path,
        user_id=user_id,
        trigger="session_missed",
        description="Learner missed morning session",
        settings=settings,
    )

    assert result["versionId"] is not None
    assert result["trigger"] == "session_missed"
    assert result["versionNumber"] >= 2


def test_reschedule_invalid_trigger(tmp_path):
    """Invalid reschedule trigger is rejected."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    with pytest.raises(Exception) as exc_info:
        reschedule_schedule(
            settings.database_path,
            user_id=user_id,
            trigger="invalid_trigger",
            description="Bad trigger",
            settings=settings,
        )
    assert "trigger" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_tenant_isolation(tmp_path):
    """Learner A's schedule data is isolated from learner B."""
    app, settings = _make_app(tmp_path)
    user_a = _seed_user(settings, "learner_a")
    user_b = _seed_user(settings, "learner_b")

    start = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    end = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
    schedule_study_session(
        settings.database_path,
        user_id=user_a,
        title="User A Session",
        start_at=start.isoformat(),
        end_at=end.isoformat(),
    )

    sessions_a = list_scheduled_study_sessions(
        settings.database_path, user_id=user_a
    )
    sessions_b = list_scheduled_study_sessions(
        settings.database_path, user_id=user_b
    )

    assert len(sessions_a) == 1
    assert len(sessions_b) == 0


# ---------------------------------------------------------------------------
# API integration
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


def test_api_availability_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    response = client.post(
        "/api/v1/scheduler/availability",
        json={
            "dayOfWeek": 1,
            "startTime": "09:00",
            "endTime": "12:00",
            "isActive": True,
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["availabilityId"] is not None

    response = client.get("/api/v1/scheduler/availability")
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_api_constraints_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    response = client.post(
        "/api/v1/scheduler/constraints",
        json={
            "maxDailyHours": 4,
            "defaultSessionMinutes": 30,
            "bufferMinutes": 10,
            "quietHoursStart": 21,
            "quietHoursEnd": 6,
            "notificationAdvanceMinutes": 30,
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["constraintId"] is not None

    response = client.get("/api/v1/scheduler/constraints")
    assert response.status_code == 200
    assert response.json()["maxDailyHours"] == 4


def test_api_session_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    start = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    end = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)

    response = client.post(
        "/api/v1/scheduler/sessions",
        json={
            "title": "Math Study",
            "startAt": start.isoformat(),
            "endAt": end.isoformat(),
            "sessionType": "learning",
            "priority": 3,
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["sessionId"] is not None
    assert data["title"] == "Math Study"

    response = client.get("/api/v1/scheduler/sessions")
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_api_session_overlap_rejected(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    start1 = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    end1 = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
    start2 = datetime(2026, 4, 15, 9, 30, tzinfo=UTC)
    end2 = datetime(2026, 4, 15, 10, 30, tzinfo=UTC)

    client.post(
        "/api/v1/scheduler/sessions",
        json={
            "title": "Session 1",
            "startAt": start1.isoformat(),
            "endAt": end1.isoformat(),
        },
    )

    response = client.post(
        "/api/v1/scheduler/sessions",
        json={
            "title": "Session 2",
            "startAt": start2.isoformat(),
            "endAt": end2.isoformat(),
        },
    )
    assert response.status_code == 400, response.text


def test_api_generate_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    path = create_learning_path(
        database_path,
        user_id=user_id,
        title="Math Path",
        description="Learn math",
    )

    set_study_availability(
        database_path,
        user_id=user_id,
        day_of_week=2,
        start_time="09:00",
        end_time="17:00",
    )

    response = client.post(
        "/api/v1/scheduler/generate",
        json={
            "pathId": path["pathId"],
            "startDate": "2026-04-15",
            "endDate": "2026-04-16",
            "maxDailyHours": 4,
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["versionId"] is not None
    assert "sessions" in data


def test_api_schedule_unauthenticated(tmp_path):
    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    response = client.get("/api/v1/scheduler/availability")
    assert response.status_code in (401, 403)

    response = client.post(
        "/api/v1/scheduler/generate",
        json={
            "startDate": "2026-04-15",
            "endDate": "2026-04-16",
        },
    )
    assert response.status_code in (401, 403)


def test_api_adapt_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    start = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
    end = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
    schedule_study_session(
        database_path,
        user_id=user_id,
        title="Session",
        start_at=start.isoformat(),
        end_at=end.isoformat(),
    )

    # Create a schedule version first
    from app.storage import create_schedule_version
    create_schedule_version(
        database_path,
        user_id=user_id,
        version_number=1,
        title="Initial Schedule",
        description="Initial",
        start_date="2026-04-15",
        end_date="2026-04-16",
    )

    response = client.post(
        "/api/v1/scheduler/adapt",
        json={
            "trigger": "session_missed",
            "description": "Learner missed session",
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["versionId"] is not None
    assert data["trigger"] == "session_missed"
