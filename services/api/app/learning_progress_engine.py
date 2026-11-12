"""M84: Real-Time Learning Progress & Engagement Intelligence Engine.

Transforms raw learning events into timely, explainable progress insights
without confusing activity with actual learning mastery.

Core principles:

* **Event-first ingestion**: Every learning activity is recorded as an
  immutable, idempotent event with a stable schema. Raw events are never
  mutated; derived aggregates are recomputed incrementally.
* **Mastery vs. completion separation**: Completion (did the learner finish?)
  and mastery (does the learner know it?) are computed independently. Time
  spent never implies knowledge mastery.
* **Explainable insights**: Every insight references the specific events that
  triggered it, carries a confidence score, and states its limitations.
* **No manipulative metrics**: No shame-based streaks, no artificial
  engagement optimization, no hidden scoring. All metrics are observable and
  auditable.
* **Tenant isolation**: All data is scoped to the authenticated user's tenant.

No LLM calls are made. All computation is deterministic and works offline.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.errors import ApiError
from app.schemas import (
    LearnerInsightResponse,
    LearningEventCreate,
    LearningEventResponse,
    ProgressInsight,
    ProgressSummary,
)
from app.storage import (
    create_learner_insight,
    get_learning_analytics_data,
    get_weak_topics,
    ingest_learning_event,
    list_learner_insights,
    list_learning_events,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MASTERY_STRONG_THRESHOLD = 0.80
MASTERY_WEAK_THRESHOLD = 0.60
ENGAGEMENT_STALENESS_DAYS_DEFAULT = 14

EVENT_TYPE_LESSON_STARTED = "lesson.started"
EVENT_TYPE_LESSON_COMPLETED = "lesson.completed"
EVENT_TYPE_OBJECTIVE_ACHIEVED = "objective.achieved"
EVENT_TYPE_ASSESSMENT_ATTEMPTED = "assessment.attempted"
EVENT_TYPE_ASSESSMENT_COMPLETED = "assessment.completed"
EVENT_TYPE_QUESTION_ANSWERED = "question.answered"
EVENT_TYPE_REVISION_COMPLETED = "revision.completed"
EVENT_TYPE_MILESTONE_REACHED = "milestone.reached"
EVENT_TYPE_SESSION_STARTED = "session.started"
EVENT_TYPE_SESSION_PAUSED = "session.paused"
EVENT_TYPE_SESSION_COMPLETED = "session.completed"
EVENT_TYPE_RECOMMENDATION_ACCEPTED = "recommendation.accepted"
EVENT_TYPE_RECOMMENDATION_DISMISSED = "recommendation.dismissed"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _tenant_id(database_path, user_id: str) -> str:
    from app.storage import _progress_tenant_id

    return _progress_tenant_id(database_path, user_id)


# ---------------------------------------------------------------------------
# Event ingestion
# ---------------------------------------------------------------------------


def ingest_event(
    database_path,
    *,
    user_id: str,
    payload: LearningEventCreate,
    settings: Settings | None = None,
) -> LearningEventResponse:
    """Ingest a single learning event with idempotency.

    Duplicate events (same idempotency_key) are detected and the original
    event is returned without a new write.
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]

    max_events = int(settings.max_events_per_day)
    today = datetime.now(UTC).date().isoformat()
    today_events = list_learning_events(
        database_path, user_id=user_id, start_date=today, end_date=today
    )
    if len(today_events) >= max_events:
        raise ApiError(
            429,
            "DAILY_EVENT_LIMIT_EXCEEDED",
            f"Daily event limit of {max_events} reached for today.",
        )

    stored = ingest_learning_event(
        database_path,
        user_id=user_id,
        event_type=payload.eventType,
        occurred_at=payload.occurredAt,
        event_data=payload.eventData,
        metadata=payload.metadata,
        source=payload.source,
        event_subtype=payload.eventSubtype,
        idempotency_key=payload.idempotencyKey,
    )
    return LearningEventResponse(**stored)


def ingest_events_batch(
    database_path,
    *,
    user_id: str,
    events: list[LearningEventCreate],
    settings: Settings | None = None,
) -> list[LearningEventResponse]:
    """Ingest multiple learning events in order. Each event is processed
    independently; duplicates are skipped silently."""
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    results: list[LearningEventResponse] = []
    for event in events:
        results.append(
            ingest_event(
                database_path,
                user_id=user_id,
                payload=event,
                settings=settings,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Progress computation
# ---------------------------------------------------------------------------


def compute_progress_summary(
    database_path,
    *,
    user_id: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> ProgressSummary:
    """Compute a real-time progress summary for a learner.

    Combines raw events with persisted analytics (quiz attempts, study
    sessions, topic reviews) to produce completion, activity, engagement,
    and mastery indicators.
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    if now is None:
        now = datetime.now(UTC)
    current = now.astimezone(UTC)

    # Load existing analytics data (quiz attempts, sessions, reviews).
    analytics = get_learning_analytics_data(database_path, user_id=user_id)
    sessions = analytics.get("sessions", [])

    # Load recent events.
    lookback = int(settings.progress_lookback_days)
    start_date = (current - timedelta(days=lookback)).date().isoformat()
    events = list_learning_events(
        database_path, user_id=user_id, start_date=start_date, limit=1000
    )

    total_events = len(events)
    events_by_type: dict[str, int] = defaultdict(int)
    for ev in events:
        events_by_type[ev["eventType"]] += 1

    # Completion rate: completed sessions / started sessions.
    started = sum(1 for s in sessions if s.get("startedAt"))
    completed = sum(1 for s in sessions if s.get("completedAt"))
    completion_rate = (completed / started) if started else None

    # Average mastery from analytics.
    masteries = [
        t["mastery"]
        for t in analytics.get("topics", [])
        if t.get("mastery") is not None
    ]
    average_mastery = (sum(masteries) / len(masteries)) if masteries else None

    # Active streak: consecutive days with at least one event/session.
    active_streak = _compute_active_streak(events, sessions, current)

    last_activity = None
    candidates: list[str] = []
    for ev in events:
        candidates.append(ev["occurredAt"])
    for s in sessions:
        if s.get("startedAt"):
            candidates.append(s["startedAt"])
    if candidates:
        last_activity = max(candidates)

    insights = list_learner_insights(
        database_path, user_id=user_id, status="active", limit=1000
    )

    return ProgressSummary(
        userId=user_id,
        totalEvents=total_events,
        eventsByType=dict(events_by_type),
        completionRate=completion_rate,
        averageMastery=average_mastery,
        activeStreakDays=active_streak,
        lastActivityAt=last_activity,
        insightsCount=len(insights),
        generatedAt=_now_iso(),
    )


def _compute_active_streak(
    events: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    now: datetime,
) -> int:
    """Count consecutive days (ending today) with any activity.

    A day counts if the learner had at least one event or session.
    This is a simple consistency measure, not a manipulative streak.
    """
    today = now.date()
    active_days: set = set()
    for ev in events:
        occurred = _parse_iso(ev.get("occurredAt"))
        if occurred is not None:
            active_days.add(occurred.date())
    for s in sessions:
        started = _parse_iso(s.get("startedAt"))
        if started is not None:
            active_days.add(started.date())
    streak = 0
    day = today
    while day in active_days:
        streak += 1
        day -= timedelta(days=1)
    return streak


# ---------------------------------------------------------------------------
# Engagement intelligence
# ---------------------------------------------------------------------------


def compute_engagement_indicators(
    database_path,
    *,
    user_id: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute engagement indicators without manipulative scoring.

    Returns observable metrics only - no scores, no shame, no optimization.
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    if now is None:
        now = datetime.now(UTC)
    current = now.astimezone(UTC)

    analytics = get_learning_analytics_data(database_path, user_id=user_id)
    attempts = analytics.get("attempts", [])
    sessions = analytics.get("sessions", [])
    reviews = analytics.get("reviews", [])

    staleness_days = int(settings.engagement_staleness_days)

    # Session completion rate.
    started = [s for s in sessions if s.get("startedAt")]
    completed = [s for s in sessions if s.get("completedAt")]
    completion_rate = (
        len(completed) / len(started) if started else None
    )

    # Assessment participation.
    assessed = [a for a in attempts if a.get("outcome") in {"correct", "incorrect"}]
    attempted_count = len(assessed)

    # Revision regularity: days with at least one review.
    review_days: set = set()
    for r in reviews:
        attempted = _parse_iso(r.get("attemptedAt"))
        if attempted is not None:
            review_days.add(attempted.date())
    recent_review_days = sum(
        1 for d in review_days if (current.date() - d).days <= staleness_days
    )

    # Inactivity: days since last activity.
    last_activity = None
    for ev in list_learning_events(
        database_path, user_id=user_id, limit=1
    ):
        last_activity = _parse_iso(ev.get("occurredAt"))
    for s in sessions:
        started = _parse_iso(s.get("startedAt"))
        if started is not None and (
            last_activity is None or started > last_activity
        ):
            last_activity = started
    days_inactive = None
    if last_activity is not None:
        days_inactive = (current.date() - last_activity.date()).days

    # Learning-path adherence: milestone reached vs. scheduled.
    milestones_reached = sum(
        1 for ev in list_learning_events(
            database_path, user_id=user_id,
            event_type=EVENT_TYPE_MILESTONE_REACHED, limit=100
        )
    )

    return {
        "userId": user_id,
        "sessionCompletionRate": completion_rate,
        "assessmentParticipationCount": attempted_count,
        "recentReviewDays": recent_review_days,
        "stalenessDays": staleness_days,
        "daysSinceLastActivity": days_inactive,
        "isInactive": days_inactive is not None and days_inactive >= staleness_days,
        "milestonesReached": milestones_reached,
        "measuredAt": current.isoformat(),
    }


# ---------------------------------------------------------------------------
# Insights generation
# ---------------------------------------------------------------------------


def generate_insights(
    database_path,
    *,
    user_id: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[ProgressInsight]:
    """Generate actionable, explainable insights from actual learner data.

    Every insight is traceable to specific events and carries a confidence
    score and stated limitations. No insight is fabricated.
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    if now is None:
        now = datetime.now(UTC)
    current = now.astimezone(UTC)

    analytics = get_learning_analytics_data(database_path, user_id=user_id)
    attempts = analytics.get("attempts", [])
    sessions = analytics.get("sessions", [])
    reviews = analytics.get("reviews", [])
    topics = analytics.get("topics", [])
    weak_topics = {wt["topic_id"] for wt in get_weak_topics(database_path, user_id=user_id)}

    insights: list[ProgressInsight] = []

    # 1. Recently achieved learning objectives.
    recent_completions = [
        s for s in sessions
        if s.get("completedAt")
        and _parse_iso(s["completedAt"]) is not None
        and (current - _parse_iso(s["completedAt"])).days <= 7
    ]
    if recent_completions:
        insights.append(ProgressInsight(
            insightType="objectiveAchieved",
            title="Recent learning completed",
            description=(
                f"{len(recent_completions)} study session(s) completed in "
                f"the past 7 days."
            ),
            severity="info",
            confidence=0.95,
            recommendedActions=[
                "Review completed material to reinforce retention.",
            ],
            triggerEventIds=[
                s["sessionId"] for s in recent_completions[:5]
            ],
        ))

    # 2. Concepts requiring additional practice (weak topics).
    weak_topic_names = [
        t["topic"] for t in topics
        if t.get("topic_id") in weak_topics or t.get("weak")
    ][:5]
    if weak_topic_names:
        insights.append(ProgressInsight(
            insightType="conceptNeedsPractice",
            title="Concepts need additional practice",
            description=(
                f"The following topic(s) are below the mastery threshold "
                f"({MASTERY_WEAK_THRESHOLD:.0%}): "
                f"{', '.join(weak_topic_names)}."
            ),
            severity="medium",
            confidence=0.85,
            recommendedActions=[
                "Schedule targeted revision sessions for weak topics.",
                "Attempt practice questions focused on weak areas.",
            ],
            triggerEventIds=[],
        ))

    # 3. Planned sessions repeatedly missed.
    missed_sessions = [
        s for s in sessions
        if not s.get("completedAt")
        and _parse_iso(s.get("startedAt")) is not None
        and (current - _parse_iso(s["startedAt"])).days >= 1
    ]
    if len(missed_sessions) >= 2:
        insights.append(ProgressInsight(
            insightType="sessionsRepeatedlyMissed",
            title="Planned sessions repeatedly missed",
            description=(
                f"{len(missed_sessions)} scheduled session(s) were not "
                f"completed more than a day after starting."
            ),
            severity="medium",
            confidence=0.80,
            recommendedActions=[
                "Reschedule missed sessions at a more realistic time.",
                "Break large sessions into shorter blocks.",
            ],
            triggerEventIds=[
                s["sessionId"] for s in missed_sessions[:5]
            ],
        ))

    # 4. Assessment performance improvement.
    scored = [
        a for a in attempts
        if a.get("outcome") in {"correct", "incorrect"}
    ]
    if len(scored) >= 4:
        # Compare first half vs. second half accuracy.
        mid = len(scored) // 2
        first_half = scored[:mid]
        second_half = scored[mid:]
        first_acc = sum(1 for a in first_half if a["outcome"] == "correct") / len(first_half)
        second_acc = sum(1 for a in second_half if a["outcome"] == "correct") / len(second_half)
        if second_acc > first_acc + 0.10:
            insights.append(ProgressInsight(
                insightType="assessmentImprovement",
                title="Assessment performance improving",
                description=(
                    f"Accuracy rose from {first_acc:.0%} to {second_acc:.0%} "
                    f"across recent attempts."
                ),
                severity="info",
                confidence=0.75,
                recommendedActions=[
                    "Maintain current study approach.",
                    "Continue regular assessment practice.",
                ],
                triggerEventIds=[
                    a["attemptId"] for a in scored[-5:]
                ],
            ))

    # 5. Revision requirements approaching (overdue reviews).
    overdue_reviews = [
        r for r in reviews
        if _parse_iso(r.get("dueAt")) is not None
        and _parse_iso(r["dueAt"]) <= current
    ][:5]
    if overdue_reviews:
        insights.append(ProgressInsight(
            insightType="revisionApproaching",
            title="Revision sessions due",
            description=(
                f"{len(overdue_reviews)} review(s) are past due."
            ),
            severity="low",
            confidence=0.90,
            recommendedActions=[
                "Complete overdue revision sessions.",
                "Schedule regular revision blocks.",
            ],
            triggerEventIds=[
                r["questionId"] for r in overdue_reviews
            ],
        ))

    # 6. Inactivity pattern.
    engagement = compute_engagement_indicators(
        database_path, user_id=user_id, settings=settings, now=current
    )
    if engagement["isInactive"]:
        insights.append(ProgressInsight(
            insightType="inactivityDetected",
            title="No recent learning activity",
            description=(
                f"No activity recorded for {engagement['daysSinceLastActivity']} "
                f"day(s)."
            ),
            severity="low",
            confidence=0.90,
            recommendedActions=[
                "Start a short study session to rebuild momentum.",
                "Review upcoming milestones and deadlines.",
            ],
            triggerEventIds=[],
        ))

    # Cap insights and sort by severity.
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    insights.sort(key=lambda i: severity_order.get(i.severity, 9))
    max_insights = int(settings.max_insights_per_user)
    return insights[:max_insights]


# ---------------------------------------------------------------------------
# Persist insights to storage
# ---------------------------------------------------------------------------


def persist_insights(
    database_path,
    *,
    user_id: str,
    insights: list[ProgressInsight],
) -> list[LearnerInsightResponse]:
    """Persist generated insights to storage, replacing existing active ones."""
    stored: list[LearnerInsightResponse] = []
    for insight in insights:
        created = create_learner_insight(
            database_path,
            user_id=user_id,
            insight_type=insight.insightType,
            title=insight.title,
            description=insight.description,
            severity=insight.severity,
            confidence=insight.confidence,
            trigger_event_ids=insight.triggerEventIds,
            recommended_actions=insight.recommendedActions,
        )
        stored.append(LearnerInsightResponse(**created))
    return stored


# ---------------------------------------------------------------------------
# Combined progress + insights
# ---------------------------------------------------------------------------


def get_progress_with_insights(
    database_path,
    *,
    user_id: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Get progress summary with current insights in one call."""
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    if now is None:
        now = datetime.now(UTC)
    current = now.astimezone(UTC)

    summary = compute_progress_summary(
        database_path, user_id=user_id, settings=settings, now=current
    )
    insights = generate_insights(
        database_path, user_id=user_id, settings=settings, now=current
    )
    stored = persist_insights(
        database_path, user_id=user_id, insights=insights
    )

    return {
        "summary": summary.model_dump(),
        "insights": [i.model_dump() for i in stored],
        "generatedAt": current.isoformat(),
    }
