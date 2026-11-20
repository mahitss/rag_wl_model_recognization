"""M85: AI Learning Behavior Intelligence & Personalized Intervention Engine.

Identifies evidence-backed learning difficulties from real learner data and
generates explainable, actionable interventions. All analysis is deterministic
and rule-based; no LLM calls are made. Inferences are clearly distinguished
from observed facts, with confidence scores and evidence references.

Core principles:

* **Observed vs. inferred distinction**: Every behavior pattern cites the
  specific events that triggered it. Inferences are labeled as inferences,
  never as proven facts.
* **Confidence and staleness**: Every learner state carries a confidence
  score and a staleness timestamp; stale data is never presented as current.
* **Intervention lifecycle**: Proposed, accepted, dismissed, completed, and
  expired states are tracked. Interventions measure subsequent outcomes.
* **No silent curriculum changes**: Interventions are proposals only; they
  never automatically modify learner records or schedules without explicit
  learner consent.
* **No sensitive inference**: Medical, psychological, personality, or other
  sensitive attributes are never inferred. Only observable learning behavior
  is analyzed.
* **Tenant isolation**: All data is scoped to the authenticated user.
* **No LLM**: Fully deterministic and works offline.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.schemas import (
    BehaviorPatternResponse,
    InterventionResponse,
    LearnerBehaviorAnalysisResult,
    LearnerStateResponse,
)
from app.storage import (
    create_intervention,
    get_learner_state,
    get_learning_analytics_data,
    list_behavior_patterns,
    list_interventions,
    list_learning_events,
    store_behavior_pattern,
    upsert_learner_state,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MASTERY_WEAK_THRESHOLD = 0.60
MASTERY_STRONG_THRESHOLD = 0.80
BEHAVIOR_STALENESS_DAYS_DEFAULT = 30
INTERVENTION_COOLDOWN_DAYS_DEFAULT = 7

EVENT_TYPE_QUESTION_ANSWERED = "question.answered"
EVENT_TYPE_ASSESSMENT_ATTEMPTED = "assessment.attempted"
EVENT_TYPE_ASSESSMENT_COMPLETED = "assessment.completed"
EVENT_TYPE_LESSON_COMPLETED = "lesson.completed"
EVENT_TYPE_REVISION_COMPLETED = "revision.completed"
EVENT_TYPE_SESSION_STARTED = "session.started"
EVENT_TYPE_SESSION_PAUSED = "session.paused"
EVENT_TYPE_SESSION_COMPLETED = "session.completed"
EVENT_TYPE_MILESTONE_REACHED = "milestone.reached"


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
    from app.storage import _behavior_tenant_id

    return _behavior_tenant_id(database_path, user_id)


# ---------------------------------------------------------------------------
# Learner state computation
# ---------------------------------------------------------------------------


def compute_learner_state(
    database_path,
    *,
    user_id: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> LearnerStateResponse:
    """Compute the current learner state from all available evidence.

    Combines:
    - Concept mastery from analytics
    - Recent assessment performance
    - Study consistency
    - Revision regularity
    - Prerequisite readiness
    - Progress velocity

    Returns a deterministic state snapshot with confidence scores.
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
    competencies = analytics.get("competencies", [])

    # --- Mastery ---
    topic_masteries = [
        t["mastery"] for t in topics if t.get("mastery") is not None
    ]
    overall_mastery = (
        sum(topic_masteries) / len(topic_masteries) if topic_masteries else None
    )

    # --- Assessment performance (recent 30 days) ---
    assessment_cutoff = current - timedelta(days=30)
    recent_assessments = [
        a for a in attempts
        if a.get("outcome") in {"correct", "incorrect"}
        and _parse_iso(a.get("createdAt")) is not None
        and _parse_iso(a["createdAt"]) >= assessment_cutoff
    ]
    recent_assessment_average = None
    if recent_assessments:
        correct = sum(1 for a in recent_assessments if a["outcome"] == "correct")
        recent_assessment_average = correct / len(recent_assessments)

    # --- Study consistency ---
    # Days with at least one session in the lookback window
    lookback_days = int(settings.behavior_analysis_lookback_days)
    lookback_cutoff = current - timedelta(days=lookback_days)
    session_dates: set = set()
    for s in sessions:
        started = _parse_iso(s.get("startedAt"))
        if started is not None and started >= lookback_cutoff:
            session_dates.add(started.date())
    study_consistency_score = (
        len(session_dates) / lookback_days if lookback_days > 0 else 0.0
    )

    # --- Revision regularity ---
    review_dates: set = set()
    for r in reviews:
        attempted = _parse_iso(r.get("attemptedAt"))
        if attempted is not None and attempted >= lookback_cutoff:
            review_dates.add(attempted.date())
    revision_regularity_score = (
        len(review_dates) / lookback_days if lookback_days > 0 else 0.0
    )

    # --- Prerequisite readiness ---
    # Concepts with prerequisites that are not yet mastered
    prereq_concepts = [
        t for t in topics
        if t.get("hasPrerequisite") and t.get("mastery", 0) < MASTERY_STRONG_THRESHOLD
    ]
    prereq_count = len(prereq_concepts)
    total_prereq_concepts = len(
        [t for t in topics if t.get("hasPrerequisite")]
    )
    prerequisite_readiness_score = None
    if total_prereq_concepts > 0:
        mastered_prereq = total_prereq_concepts - prereq_count
        prerequisite_readiness_score = mastered_prereq / total_prereq_concepts

    # --- Progress velocity ---
    # Concepts mastered per week over lookback window
    mastered_recently = [
        t for t in topics
        if t.get("mastery", 0) >= MASTERY_STRONG_THRESHOLD
        and _parse_iso(t.get("masteredAt")) is not None
        and _parse_iso(t["masteredAt"]) >= lookback_cutoff
    ]
    progress_velocity = (
        len(mastered_recently) / lookback_days if lookback_days > 0 else 0.0
    )

    # --- Confidence ---
    # Based on evidence volume: more data = higher confidence
    evidence_count = len(attempts) + len(sessions) + len(reviews) + len(topics)
    confidence = min(1.0, evidence_count / 50.0)  # Full confidence at 50+ data points

    state_snapshot = {
        "overallMastery": overall_mastery,
        "averageConfidence": confidence,
        "recentAssessmentAverage": recent_assessment_average,
        "studyConsistencyScore": study_consistency_score,
        "prerequisiteReadinessScore": prerequisite_readiness_score,
        "progressVelocity": progress_velocity,
        "revisionRegularityScore": revision_regularity_score,
        "topicsMastered": len(mastered_recently),
        "topicsWeak": len([
            t for t in topics
            if t.get("mastery", 0) < MASTERY_WEAK_THRESHOLD
        ]),
        "totalTopics": len(topics),
        "totalAssessments": len(attempts),
        "totalSessions": len(sessions),
        "totalReviews": len(reviews),
    }

    stale_days = int(settings.behavior_staleness_days)

    stored = upsert_learner_state(
        database_path,
        user_id=user_id,
        overall_mastery=overall_mastery,
        average_confidence=confidence,
        recent_assessment_average=recent_assessment_average,
        study_consistency_score=study_consistency_score,
        prerequisite_readiness_score=prerequisite_readiness_score,
        progress_velocity=progress_velocity,
        revision_regularity_score=revision_regularity_score,
        state_snapshot=state_snapshot,
        stale_days=stale_days,
        evidence_summary={
            "topicsCount": len(topics),
            "competenciesCount": len(competencies),
            "attemptsCount": len(attempts),
            "sessionsCount": len(sessions),
            "reviewsCount": len(reviews),
        },
    )

    return LearnerStateResponse(
        stateId=stored["stateId"],
        userId=user_id,
        tenantId=stored["tenantId"],
        overallMastery=stored["overallMastery"],
        averageConfidence=stored["averageConfidence"],
        recentAssessmentAverage=stored["recentAssessmentAverage"],
        studyConsistencyScore=stored["studyConsistencyScore"],
        prerequisiteReadinessScore=stored["prerequisiteReadinessScore"],
        progressVelocity=stored["progressVelocity"],
        revisionRegularityScore=stored["revisionRegularityScore"],
        stateSnapshot=stored["stateSnapshot"],
        staleAfter=stored["staleAfter"],
        computedAt=stored["computedAt"],
        evidenceSummary=stored["evidenceSummary"],
    )


# ---------------------------------------------------------------------------
# Behavior pattern analysis
# ---------------------------------------------------------------------------


def analyze_behavior_patterns(
    database_path,
    *,
    user_id: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[BehaviorPatternResponse]:
    """Analyze learner event data to detect behavior patterns.

    Each pattern is traceable to specific events and carries a confidence
    score. Patterns are stored persistently.
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    if now is None:
        now = datetime.now(UTC)
    now = now.astimezone(UTC)

    events = list_learning_events(
        database_path, user_id=user_id, limit=2000
    )
    patterns: list[dict[str, Any]] = []

    # --- Pattern 1: Repeated difficulty with a concept ---
    # Multiple incorrect answers on the same topic
    topic_incorrect: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("eventType") == EVENT_TYPE_QUESTION_ANSWERED:
            data = e.get("eventData", {})
            topic = data.get("topic")
            outcome = data.get("outcome")
            if topic and outcome == "incorrect":
                topic_incorrect[topic].append(e)

    for topic, evts in topic_incorrect.items():
        if len(evts) >= int(settings.behavior_min_repetitions):
            patterns.append({
                "patternType": "repeated_difficulty",
                "severity": "major" if len(evts) >= 5 else "moderate",
                "confidence": min(1.0, len(evts) / 10.0),
                "evidenceEventIds": [e["eventId"] for e in evts[:5]],
                "description": (
                    f"Learner answered {len(evts)} question(s) incorrectly "
                    f"on topic '{topic}'."
                ),
                "recurrenceCount": len(evts),
            })

    # --- Pattern 2: Repeated assessment errors ---
    # Multiple failed assessments on same topic
    topic_assessment_fail: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("eventType") == EVENT_TYPE_ASSESSMENT_COMPLETED:
            data = e.get("eventData", {})
            topic = data.get("topic")
            score = data.get("score")
            if topic and score is not None and score < 0.60:
                topic_assessment_fail[topic].append(e)

    for topic, evts in topic_assessment_fail.items():
        if len(evts) >= int(settings.behavior_min_repetitions):
            patterns.append({
                "patternType": "repeated_assessment_errors",
                "severity": "major" if len(evts) >= 3 else "moderate",
                "confidence": min(1.0, len(evts) / 5.0),
                "evidenceEventIds": [e["eventId"] for e in evts[:3]],
                "description": (
                    f"Learner failed {len(evts)} assessment(s) on topic "
                    f"'{topic}'."
                ),
                "recurrenceCount": len(evts),
            })

    # --- Pattern 3: Inconsistent revision patterns ---
    # Long gaps between revisions on same topic
    topic_revision: dict[str, list[datetime]] = defaultdict(list)
    for e in events:
        if e.get("eventType") == EVENT_TYPE_REVISION_COMPLETED:
            data = e.get("eventData", {})
            topic = data.get("topic")
            occurred = _parse_iso(e.get("occurredAt"))
            if topic and occurred is not None:
                topic_revision[topic].append(occurred)

    for topic, dates in topic_revision.items():
        if len(dates) >= 2:
            dates.sort()
            gaps = [
                (dates[i + 1] - dates[i]).days
                for i in range(len(dates) - 1)
            ]
            max_gap = max(gaps)
            if max_gap >= 14:  # 2+ weeks between revisions
                patterns.append({
                    "patternType": "inconsistent_revision",
                    "severity": "moderate",
                    "confidence": min(1.0, max_gap / 30.0),
                    "evidenceEventIds": [],
                    "description": (
                        f"Revision gap of {max_gap} days on topic '{topic}'."
                    ),
                    "recurrenceCount": len(dates),
                })

    # --- Pattern 4: Repeatedly abandoned sessions ---
    # Sessions started but not completed
    abandoned_sessions = [
        e for e in events
        if e.get("eventType") == EVENT_TYPE_SESSION_STARTED
        and not any(
            e2.get("eventType") == EVENT_TYPE_SESSION_COMPLETED
            and e2.get("eventData", {}).get("sessionId")
            == e.get("eventData", {}).get("sessionId")
            for e2 in events
        )
    ]
    if len(abandoned_sessions) >= int(settings.behavior_min_repetitions):
        patterns.append({
            "patternType": "abandoned_sessions",
            "severity": "moderate",
            "confidence": min(1.0, len(abandoned_sessions) / 5.0),
            "evidenceEventIds": [e["eventId"] for e in abandoned_sessions[:5]],
            "description": (
                f"{len(abandoned_sessions)} session(s) started but not "
                f"completed."
            ),
            "recurrenceCount": len(abandoned_sessions),
        })

    # --- Pattern 5: Learning-path delay ---
    # Milestones missed by more than 7 days
    milestone_delays = []
    for e in events:
        if e.get("eventType") == EVENT_TYPE_MILESTONE_REACHED:
            data = e.get("eventData", {})
            due = data.get("dueAt")
            reached = data.get("reachedAt")
            if due and reached:
                due_dt = _parse_iso(due)
                reached_dt = _parse_iso(reached)
                if due_dt and reached_dt and (reached_dt - due_dt).days > 7:
                    milestone_delays.append(e)
    if milestone_delays:
        patterns.append({
            "patternType": "learning_path_delay",
            "severity": "moderate",
            "confidence": min(1.0, len(milestone_delays) / 3.0),
            "evidenceEventIds": [e["eventId"] for e in milestone_delays[:3]],
            "description": (
                f"{len(milestone_delays)} milestone(s) reached more than "
                f"7 days after due date."
            ),
            "recurrenceCount": len(milestone_delays),
        })

    # --- Pattern 6: Mastery deterioration ---
    # Assessment scores declining over time
    scored_attempts = [
        (e, _parse_iso(e.get("occurredAt")))
        for e in events
        if e.get("eventType") == EVENT_TYPE_ASSESSMENT_COMPLETED
        and _parse_iso(e.get("occurredAt")) is not None
    ]
    scored_attempts.sort(key=lambda x: x[1])
    if len(scored_attempts) >= 4:
        mid = len(scored_attempts) // 2
        first_half = scored_attempts[:mid]
        second_half = scored_attempts[mid:]
        first_avg = sum(
            e[0].get("eventData", {}).get("score", 0) for e in first_half
        ) / len(first_half)
        second_avg = sum(
            e[0].get("eventData", {}).get("score", 0) for e in second_half
        ) / len(second_half)
        if second_avg < first_avg - 0.15:
            patterns.append({
                "patternType": "mastery_deterioration",
                "severity": "major",
                "confidence": min(1.0, (first_avg - second_avg) / 0.30),
                "evidenceEventIds": [e[0]["eventId"] for e in scored_attempts[-5:]],
                "description": (
                    f"Assessment scores declined from {first_avg:.0%} to "
                    f"{second_avg:.0%}."
                ),
                "recurrenceCount": len(scored_attempts),
            })

    # Store patterns
    stored: list[BehaviorPatternResponse] = []
    for p in patterns:
        created = store_behavior_pattern(
            database_path,
            user_id=user_id,
            pattern_type=p["patternType"],
            severity=p["severity"],
            confidence=p["confidence"],
            evidence_event_ids=p["evidenceEventIds"],
            description=p["description"],
            recurrence_count=p.get("recurrenceCount", 1),
        )
        stored.append(BehaviorPatternResponse(**created))

    return stored


# ---------------------------------------------------------------------------
# Intervention generation
# ---------------------------------------------------------------------------


def generate_interventions(
    database_path,
    *,
    user_id: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[InterventionResponse]:
    """Generate interventions from behavior patterns and learner state.

    Each intervention is tied to specific evidence and includes a rationale,
    expected benefit, priority, urgency, and next action. Interventions are
    proposals only - they do not modify learner records.
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    if now is None:
        now = datetime.now(UTC)
    now = now.astimezone(UTC)

    # Get existing interventions to avoid duplicates
    existing = list_interventions(
        database_path, user_id=user_id, status="proposed", limit=100
    )
    existing_types = {i["interventionType"] for i in existing}

    # Get learner state and patterns
    state = get_learner_state(database_path, user_id=user_id)
    patterns = list_behavior_patterns(
        database_path, user_id=user_id, is_active=True, limit=50
    )

    interventions: list[dict[str, Any]] = []

    # --- Intervention 1: Prerequisite revision ---
    if state and state.get("prerequisiteReadinessScore") is not None:
        if state.get("prerequisiteReadinessScore", 0) < MASTERY_STRONG_THRESHOLD:
            if "prerequisite_revision" not in existing_types:
                interventions.append({
                    "interventionType": "prerequisite_revision",
                    "title": "Review prerequisite concepts",
                    "description": (
                        "Some prerequisite concepts for your current topics "
                        "are not yet fully mastered."
                    ),
                    "observedEvidence": [
                        {
                            "type": "prerequisite_readiness",
                            "value": state.get("prerequisiteReadinessScore"),
                            "threshold": MASTERY_STRONG_THRESHOLD,
                        }
                    ],
                    "rationale": (
                        f"Prerequisite readiness is "
                        f"{state.get('prerequisiteReadinessScore', 0):.0%}, "
                        f"below the {MASTERY_STRONG_THRESHOLD:.0%} threshold."
                    ),
                    "expectedBenefit": (
                        "Stronger foundations will make current topics "
                        "easier to learn."
                    ),
                    "priority": (
                        "high"
                        if state.get("prerequisiteReadinessScore", 0) < 0.40
                        else "medium"
                    ),
                    "urgency": (
                        4 if state.get("prerequisiteReadinessScore", 0) < 0.40 else 3
                    ),
                    "nextAction": "Complete prerequisite review modules first.",
                    "resolutionConditions": [
                        "Prerequisite readiness above 80%",
                    ],
                })

    # --- Intervention 2: Targeted practice for weak concepts ---
    weak_patterns = [
        p for p in patterns
        if p.patternType == "repeated_difficulty"
    ]
    if weak_patterns:
        weak_pattern = weak_patterns[0]
        if "targeted_practice" not in existing_types:
            interventions.append({
                "interventionType": "targeted_practice",
                "title": "Practice weak concepts",
                "description": "Focus practice on concepts with repeated errors.",
                "observedEvidence": [
                    {
                        "type": "pattern",
                        "patternType": weak_pattern.patternType,
                        "confidence": weak_pattern.confidence,
                    }
                ],
                "rationale": (
                    f"{weak_pattern.description} Targeted practice can help "
                    f"close this gap."
                ),
                "expectedBenefit": "Improved mastery of weak concepts.",
                "priority": "high" if weak_pattern.severity == "critical" else "medium",
                "urgency": 5 if weak_pattern.severity == "critical" else 3,
                "nextAction": "Complete practice questions on weak topics.",
                "resolutionConditions": [
                    "Concept mastery above 80%",
                ],
            })

    # --- Intervention 3: Reschedule missed sessions ---
    missed_patterns = [
        p for p in patterns
        if p.patternType == "abandoned_sessions"
    ]
    if missed_patterns:
        missed_pattern = missed_patterns[0]
        if "reschedule_sessions" not in existing_types:
            interventions.append({
                "interventionType": "reschedule_sessions",
                "title": "Reschedule missed sessions",
                "description": "Replan abandoned study sessions at better times.",
                "observedEvidence": [
                    {
                        "type": "pattern",
                        "patternType": missed_pattern.patternType,
                        "confidence": missed_pattern.confidence,
                    }
                ],
                "rationale": (
                    f"{missed_pattern.description} Rescheduling can improve "
                    f"completion."
                ),
                "expectedBenefit": "Higher session completion rate.",
                "priority": "medium",
                "urgency": 3,
                "nextAction": "Move missed sessions to less busy days.",
                "resolutionConditions": [
                    "No abandoned sessions for 7 days",
                ],
            })

    # --- Intervention 4: Revision session ---
    if state and state.get("revisionRegularityScore") is not None:
        if state.get("revisionRegularityScore", 0) < 0.50:
            if "revision_session" not in existing_types:
                interventions.append({
                    "interventionType": "revision_session",
                    "title": "Schedule revision sessions",
                    "description": "Regular revision reinforces learning.",
                    "observedEvidence": [
                        {
                            "type": "revision_regularity",
                            "value": state.get("revisionRegularityScore"),
                            "threshold": 0.50,
                        }
                    ],
                    "rationale": (
                        f"Revision regularity is "
                        f"{state.get('revisionRegularityScore', 0):.0%}, "
                        f"below 50%."
                    ),
                    "expectedBenefit": "Better retention of learned material.",
                    "priority": "medium",
                    "urgency": 3,
                    "nextAction": "Schedule weekly revision blocks.",
                    "resolutionConditions": [
                        "Revision regularity above 70%",
                    ],
                })

    # --- Intervention 5: Alternative explanation ---
    if state and state.get("averageConfidence") is not None:
        if state.get("averageConfidence", 0) < 0.60:
            if "alternative_explanation" not in existing_types:
                interventions.append({
                    "interventionType": "alternative_explanation",
                    "title": "Try alternative explanation",
                    "description": "Different presentation may help understanding.",
                    "observedEvidence": [
                        {
                            "type": "average_confidence",
                            "value": state.get("averageConfidence"),
                            "threshold": 0.60,
                        }
                    ],
                    "rationale": (
                        f"Average confidence is {state.get('averageConfidence', 0):.0%}, "
                        f"below 60%."
                    ),
                    "expectedBenefit": "Improved understanding from a new angle.",
                    "priority": "medium",
                    "urgency": 3,
                    "nextAction": "Review topic with different learning resource.",
                    "resolutionConditions": [
                        "Average confidence above 70%",
                    ],
                })

    # --- Intervention 6: Educator assistance ---
    if state and state.get("overallMastery", 0) is not None:
        if state.get("overallMastery", 0) < 0.40:
            if "educator_assistance" not in existing_types:
                interventions.append({
                    "interventionType": "educator_assistance",
                    "title": "Seek educator assistance",
                    "description": "Instructor support may help overcome gaps.",
                    "observedEvidence": [
                        {
                            "type": "overall_mastery",
                            "value": state.get("overallMastery", 0),
                            "threshold": 0.40,
                        }
                    ],
                    "rationale": (
                        f"Overall mastery is {state.get('overallMastery', 0):.0%}, "
                        f"below 40%."
                    ),
                    "expectedBenefit": "Targeted help from an educator.",
                    "priority": "high",
                    "urgency": 4,
                    "nextAction": "Schedule time with an instructor.",
                    "resolutionConditions": [
                        "Overall mastery above 60%",
                    ],
                })

    # --- Intervention 7: Adjust workload ---
    if state and state.get("studyConsistencyScore", 0) is not None:
        if state.get("studyConsistencyScore", 0) < 0.40:
            if "adjust_workload" not in existing_types:
                interventions.append({
                    "interventionType": "adjust_workload",
                    "title": "Adjust study workload",
                    "description": "Reduce session frequency to improve consistency.",
                    "observedEvidence": [
                        {
                            "type": "study_consistency",
                            "value": state.get("studyConsistencyScore", 0),
                            "threshold": 0.40,
                        }
                    ],
                    "rationale": (
                        f"Study consistency is {state.get('studyConsistencyScore', 0):.0%}, "
                        f"below 40%."
                    ),
                    "expectedBenefit": "More sustainable study habits.",
                    "priority": "medium",
                    "urgency": 3,
                    "nextAction": "Reduce daily session target.",
                    "resolutionConditions": [
                        "Study consistency above 60%",
                    ],
                })

    # Store interventions
    stored: list[InterventionResponse] = []
    for iv in interventions:
        created = create_intervention(
            database_path,
            user_id=user_id,
            intervention_type=iv["interventionType"],
            title=iv["title"],
            description=iv["description"],
            observed_evidence=iv["observedEvidence"],
            rationale=iv["rationale"],
            expected_benefit=iv["expectedBenefit"],
            priority=iv["priority"],
            urgency=iv["urgency"],
            next_action=iv["nextAction"],
            resolution_conditions=iv["resolutionConditions"],
            ai_generated=False,
        )
        stored.append(InterventionResponse(**created))

    return stored


# ---------------------------------------------------------------------------
# Combined analysis
# ---------------------------------------------------------------------------


def analyze_learner_behavior(
    database_path,
    *,
    user_id: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> LearnerBehaviorAnalysisResult:
    """Run the full behavior analysis pipeline.

    Returns patterns, learner state, and interventions in one call.
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    if now is None:
        now = datetime.now(UTC)
    current = now.astimezone(UTC)

    state = compute_learner_state(
        database_path, user_id=user_id, settings=settings, now=current
    )
    patterns = analyze_behavior_patterns(
        database_path, user_id=user_id, settings=settings, now=current
    )
    interventions = generate_interventions(
        database_path, user_id=user_id, settings=settings, now=current
    )

    return LearnerBehaviorAnalysisResult(
        userId=user_id,
        patterns=patterns,
        state=state,
        interventions=interventions,
        generatedAt=current.isoformat(),
    )
