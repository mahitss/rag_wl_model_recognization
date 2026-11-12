"""M83: Intelligent Study Scheduler & Adaptive Time Management API routes.

Endpoints:

* GET    /api/v1/scheduler/availability              - List study availability
* POST   /api/v1/scheduler/availability              - Set study availability
* GET    /api/v1/scheduler/constraints               - Get scheduling constraints
* POST   /api/v1/scheduler/constraints               - Set scheduling constraints
* POST   /api/v1/scheduler/generate                  - Generate a study schedule
* GET    /api/v1/scheduler/sessions                  - List scheduled sessions
* POST   /api/v1/scheduler/sessions                  - Create a scheduled session
* PUT    /api/v1/scheduler/sessions/{session_id}     - Update a session
* DELETE /api/v1/scheduler/sessions/{session_id}     - Cancel a session
* GET    /api/v1/scheduler/summary                   - Schedule summary
* GET    /api/v1/scheduler/conflicts                 - Detect conflicts
* POST   /api/v1/scheduler/adapt                     - Adaptively reschedule
* GET    /api/v1/scheduler/versions                  - List schedule versions

All endpoints require authentication and tenant-scoped access.
"""
# ruff: noqa: B008

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, Query, Request

from app.auth import AuthUser, require_auth
from app.config import Settings
from app.errors import ApiError
from app.schemas import (
    ScheduleAdaptationRequest,
    ScheduleConstraintsCreate,
    ScheduledStudySessionCreate,
    ScheduleGenerationRequest,
    StudyAvailabilityCreate,
    UpdateScheduledStudySession,
)
from app.storage import (
    cancel_scheduled_study_session,
    get_learning_path,
    get_schedule_constraints,
    list_schedule_versions,
    list_scheduled_study_sessions,
    list_study_availability,
    schedule_study_session,
    set_schedule_constraints,
    set_study_availability,
    update_scheduled_study_session,
)
from app.study_scheduler import (
    detect_schedule_conflicts,
    generate_study_schedule,
    get_schedule_summary,
    reschedule_schedule,
)

router = APIRouter(prefix="/api/v1/scheduler", tags=["scheduler"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _require_path_ownership(
    database_path, path_id: str, user_id: str
) -> None:
    path = get_learning_path(database_path, path_id)
    if path is None:
        raise ApiError(404, "LEARNING_PATH_NOT_FOUND", "Learning path was not found.")
    if path["userId"] != user_id:
        raise ApiError(403, "LEARNING_PATH_FORBIDDEN", "You do not own this path.")


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


@router.get("/availability")
def list_availability(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> list[dict]:
    """List study availability blocks for the authenticated learner."""
    database_path = _database_path(request)
    return list_study_availability(database_path, user_id=current_user.user_id)


@router.post("/availability", status_code=201)
def create_availability(
    request: Request,
    payload: StudyAvailabilityCreate = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict:
    """Set or update study availability for a day of the week."""
    database_path = _database_path(request)
    return set_study_availability(
        database_path,
        user_id=current_user.user_id,
        day_of_week=payload.dayOfWeek,
        start_time=payload.startTime,
        end_time=payload.endTime,
        is_active=payload.isActive,
    )


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


@router.get("/constraints")
def get_constraints(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict:
    """Get scheduling constraints for the authenticated learner."""
    database_path = _database_path(request)
    constraints = get_schedule_constraints(
        database_path, user_id=current_user.user_id
    )
    if constraints is None:
        settings = _settings(request)
        return {
            "constraintId": "default",
            "userId": current_user.user_id,
            "tenantId": "",
            "maxDailyHours": int(settings.scheduler_max_daily_hours),
            "defaultSessionMinutes": int(
                settings.scheduler_default_session_minutes
            ),
            "bufferMinutes": int(settings.scheduler_buffer_minutes),
            "quietHoursStart": int(settings.scheduler_quiet_hours_start),
            "quietHoursEnd": int(settings.scheduler_quiet_hours_end),
            "notificationAdvanceMinutes": int(
                settings.scheduler_notification_advance_minutes
            ),
            "createdAt": _now_iso(),
            "updatedAt": _now_iso(),
        }
    return constraints


@router.post("/constraints", status_code=201)
def set_constraints(
    request: Request,
    payload: ScheduleConstraintsCreate = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict:
    """Set scheduling constraints for the authenticated learner."""
    database_path = _database_path(request)
    return set_schedule_constraints(
        database_path,
        user_id=current_user.user_id,
        max_daily_hours=payload.maxDailyHours,
        default_session_minutes=payload.defaultSessionMinutes,
        buffer_minutes=payload.bufferMinutes,
        quiet_hours_start=payload.quietHoursStart,
        quiet_hours_end=payload.quietHoursEnd,
        notification_advance_minutes=payload.notificationAdvanceMinutes,
    )


# ---------------------------------------------------------------------------
# Schedule generation
# ---------------------------------------------------------------------------


@router.post("/generate", status_code=201)
def generate_schedule(
    request: Request,
    payload: ScheduleGenerationRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict:
    """Generate a feasible, personalized study schedule.

    Packs sessions into available time slots respecting daily limits,
    quiet hours, prerequisites, and learner availability.  No LLM is used;
    generation is fully deterministic.
    """
    database_path = _database_path(request)
    result = generate_study_schedule(
        database_path,
        user_id=current_user.user_id,
        settings=_settings(request),
        request=payload,
    )
    return result.model_dump()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.get("/sessions")
def list_sessions(
    request: Request,
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    status: str | None = Query(default=None),
    current_user: AuthUser = Depends(require_auth),
) -> list[dict]:
    """List scheduled study sessions for the authenticated learner."""
    database_path = _database_path(request)
    return list_scheduled_study_sessions(
        database_path,
        user_id=current_user.user_id,
        start_date=start_date,
        end_date=end_date,
        status=status,
    )


@router.post("/sessions", status_code=201)
def create_session(
    request: Request,
    payload: ScheduledStudySessionCreate = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict:
    """Create a scheduled study session.  Overlaps are rejected."""
    database_path = _database_path(request)
    if payload.pathId is not None:
        _require_path_ownership(
            database_path, payload.pathId, current_user.user_id
        )
    try:
        return schedule_study_session(
            database_path,
            user_id=current_user.user_id,
            title=payload.title,
            start_at=payload.startAt,
            end_at=payload.endAt,
            session_type=payload.sessionType,
            priority=payload.priority,
            path_id=payload.pathId,
            module_id=payload.moduleId,
            objective_id=payload.objectiveId,
            description=payload.description,
        )
    except ValueError as exc:
        raise ApiError(400, "SESSION_CONFLICT", str(exc)) from exc


@router.put("/sessions/{session_id}")
def update_session(
    request: Request,
    session_id: str,
    payload: UpdateScheduledStudySession = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict:
    """Update a scheduled study session.  Only the owner may update."""
    database_path = _database_path(request)
    stored = update_scheduled_study_session(
        database_path,
        session_id=session_id,
        user_id=current_user.user_id,
        title=payload.title,
        start_at=payload.startAt,
        end_at=payload.endAt,
        session_type=payload.sessionType,
        priority=payload.priority,
        status=payload.status,
        actual_duration_minutes=payload.actualDurationMinutes,
        notes=payload.notes,
    )
    if stored is None:
        raise ApiError(404, "SESSION_NOT_FOUND", "Session was not found.")
    # Fetch the full session to return.
    sessions = list_scheduled_study_sessions(
        database_path, user_id=current_user.user_id
    )
    for s in sessions:
        if s["sessionId"] == session_id:
            return s
    raise ApiError(404, "SESSION_NOT_FOUND", "Session was not found.")


@router.delete("/sessions/{session_id}")
def delete_session(
    request: Request,
    session_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict:
    """Cancel a scheduled study session."""
    database_path = _database_path(request)
    ok = cancel_scheduled_study_session(
        database_path, session_id=session_id, user_id=current_user.user_id
    )
    if not ok:
        raise ApiError(404, "SESSION_NOT_FOUND", "Session was not found.")
    return {"sessionId": session_id, "cancelled": True}


# ---------------------------------------------------------------------------
# Summary & conflicts
# ---------------------------------------------------------------------------


@router.get("/summary")
def get_summary(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict:
    """Get a summary of the current schedule."""
    database_path = _database_path(request)
    return get_schedule_summary(database_path, user_id=current_user.user_id)


@router.get("/conflicts")
def list_conflicts(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> list[dict]:
    """Detect overlapping scheduled sessions."""
    database_path = _database_path(request)
    return detect_schedule_conflicts(database_path, user_id=current_user.user_id)


# ---------------------------------------------------------------------------
# Adaptive rescheduling
# ---------------------------------------------------------------------------


@router.post("/adapt", status_code=201)
def adapt_schedule(
    request: Request,
    payload: ScheduleAdaptationRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict:
    """Adaptively reschedule the learner's study schedule.

    Supported triggers: session_missed, duration_changed,
    task_completed_early, task_completed_late, assessment_gap,
    deadline_changed, availability_changed.
    """
    database_path = _database_path(request)
    return reschedule_schedule(
        database_path,
        user_id=current_user.user_id,
        trigger=payload.trigger,
        description=payload.description,
        settings=_settings(request),
    )


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


@router.get("/versions")
def list_versions(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> list[dict]:
    """List schedule versions for the authenticated learner."""
    database_path = _database_path(request)
    return list_schedule_versions(database_path, user_id=current_user.user_id)
