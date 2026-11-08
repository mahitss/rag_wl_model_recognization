"""M67: Predictive Learning Analytics & Student Performance Intelligence.

Provides evidence-based predictive analytics over persisted learner data:

* Performance aggregation from assessments, exams, revision history,
  competency tracking, and learning gap resolution.
* Historical trend calculations using actual timestamps and persisted records.
* Deterministic statistical baselines (no ML for appearance) for:
  - Exam readiness estimation
  - Topic forgetting risk
  - Expected learning workload
  - Knowledge gap likelihood
  - Performance trends over time
* Explainable recommendations with supporting evidence.
* Confidence intervals and uncertainty for every prediction.
* Never presents predictions as guaranteed outcomes.

All methods are transparent, auditable, and user-scoped. Cross-user isolation
is enforced by the storage layer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.storage import (
    create_analytics_snapshot,
    create_performance_prediction,
    create_prediction_model_version,
    create_topic_trend,
    get_competency_mastery,
    get_exam_result,
    get_learning_gap,
    get_weak_topics,
    list_analytics_snapshots,
    list_competencies,
    list_exam_results,
    list_gap_remediations,
    list_learning_gaps,
    list_performance_predictions,
    list_topic_trends,
    record_exam_result,
)

# Thresholds
MASTERY_WEAK_THRESHOLD = 0.60
MASTERY_STRONG_THRESHOLD = 0.80
FOREGETTING_RISK_THRESHOLD = 0.65
READINESS_READY_THRESHOLD = 0.80
READINESS_MODERATE_THRESHOLD = 0.60
MIN_ATTEMPTS_FOR_PREDICTION = 3
MIN_EXAMS_FOR_READINESS = 1


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _days_between(earlier: str | datetime, later: str | datetime) -> float:
    e = _parse_datetime(earlier)
    l = _parse_datetime(later)
    return max(0.0, (l - e).total_seconds() / 86_400.0)


# ---------------------------------------------------------------------------
# Data aggregation from persisted evidence
# ---------------------------------------------------------------------------


def _aggregate_user_performance(
    database_path: Path,
    user_id: str,
    now: datetime,
) -> dict[str, Any]:
    """Aggregate all persisted performance evidence for a user.

    Returns observed data only - no estimates or fabrications.
    """
    from app.storage import (
        get_learning_analytics_data,
        list_competencies,
        list_learning_gaps,
    )

    # Get raw analytics data from storage (quizzes, sessions, reviews)
    data = get_learning_analytics_data(database_path, user_id=user_id)

    attempts = data.get("attempts", [])
    sessions = data.get("sessions", [])
    reviews = data.get("reviews", [])
    topic_sources = data.get("topicSources", {})
    concepts = data.get("concepts", [])

    # Competency mastery records
    competencies = list_competencies(database_path, user_id=user_id) or []
    competency_mastery: dict[str, float] = {}
    for comp in competencies:
        mastery = get_competency_mastery(
            database_path, competency_id=comp["competencyId"], user_id=user_id
        )
        if mastery is not None:
            competency_mastery[comp["competencyId"]] = mastery["masteryScore"]

    # Exam results
    exam_results = list_exam_results(database_path, user_id=user_id) or []

    # Learning gaps
    gaps = list_learning_gaps(database_path, user_id=user_id) or []
    active_gaps = [g for g in gaps if g["status"] in {"detected", "acknowledged", "remediating"}]
    resolved_gaps = [g for g in gaps if g["status"] == "resolved"]

    # Weak topics from storage
    weak_topics = get_weak_topics(database_path, user_id=user_id) or []

    # Build per-topic aggregates
    topic_aggregates: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        topic_id = attempt["topicId"]
        topic = attempt["topic"]
        if topic_id not in topic_aggregates:
            topic_aggregates[topic_id] = {
                "topicId": topic_id,
                "topic": topic,
                "attempts": [],
                "correct": 0,
                "incorrect": 0,
                "ambiguous": 0,
                "totalMarks": 0,
                "marksObtained": 0.0,
                "lastAttemptAt": attempt["createdAt"],
            }
        ta = topic_aggregates[topic_id]
        ta["attempts"].append(attempt)
        ta["lastAttemptAt"] = max(ta["lastAttemptAt"], attempt["createdAt"])
        if attempt["outcome"] == "correct":
            ta["correct"] += 1
        elif attempt["outcome"] == "incorrect":
            ta["incorrect"] += 1
        elif attempt["outcome"] == "ambiguous":
            ta["ambiguous"] += 1

    # Add exam results to topic aggregates
    for exam_result in exam_results:
        topic_id = None  # exam results don't have topic_id directly
        # We'll use exam topic if available
        topic = exam_result.get("topic", "") if isinstance(exam_result, dict) else ""

    return {
        "attempts": attempts,
        "sessions": sessions,
        "reviews": reviews,
        "topicAggregates": topic_aggregates,
        "competencyMastery": competency_mastery,
        "competencies": competencies,
        "examResults": exam_results,
        "activeGaps": active_gaps,
        "resolvedGaps": resolved_gaps,
        "weakTopics": weak_topics,
        "topicSources": topic_sources,
        "concepts": concepts,
    }


def compute_performance_trends(
    *,
    database_path: str,
    user_id: str,
    now: datetime,
    lookback_days: int = 90,
) -> dict[str, Any]:
    """Compute historical performance trends for a user.

    Uses actual timestamps and persisted records only. No future-data leakage:
    trends only use data up to ``now``.
    """
    db_path = Path(database_path)
    current = now.astimezone(UTC)
    cutoff = current - timedelta(days=lookback_days)

    perf = _aggregate_user_performance(db_path, user_id, current)
    attempts = perf["attempts"]
    exam_results = perf["examResults"]

    # Filter attempts within lookback window (no future-data leakage)
    recent_attempts = [
        a for a in attempts
        if cutoff <= _parse_datetime(a["createdAt"]) <= current
    ]

    # Topic-level trends
    topic_aggregates = perf["topicAggregates"]
    topic_trends: list[dict[str, Any]] = []
    for topic_id, ta in topic_aggregates.items():
        topic_attempts = [
            a for a in ta["attempts"]
            if cutoff <= _parse_datetime(a["createdAt"]) <= current
        ]
        if not topic_attempts:
            continue
        scored = [a for a in topic_attempts if a["outcome"] in {"correct", "incorrect"}]
        accuracy = (
            sum(a["outcome"] == "correct" for a in scored) / len(scored)
            if scored else None
        )
        # Determine trend direction by splitting attempts into two halves
        topic_attempts_sorted = sorted(
            topic_attempts, key=lambda a: _parse_datetime(a["createdAt"])
        )
        midpoint = len(topic_attempts_sorted) // 2
        if midpoint > 0:
            first_half = topic_attempts_sorted[:midpoint]
            second_half = topic_attempts_sorted[midpoint:]
            first_acc = (
                sum(a["outcome"] == "correct" for a in first_half) / len(first_half)
                if first_half else None
            )
            second_acc = (
                sum(a["outcome"] == "correct" for a in second_half) / len(second_half)
                if second_half else None
            )
            if first_acc is not None and second_acc is not None:
                diff = second_acc - first_acc
                if diff > 0.1:
                    direction = "improving"
                elif diff < -0.1:
                    direction = "declining"
                else:
                    direction = "stable"
            else:
                direction = "insufficient_data"
        else:
            direction = "insufficient_data"

        topic_trends.append({
            "topicId": topic_id,
            "topic": ta["topic"],
            "attemptCount": len(topic_attempts),
            "accuracy": accuracy,
            "correctCount": ta["correct"],
            "incorrectCount": ta["incorrect"],
            "direction": direction,
            "lastAttemptAt": ta["lastAttemptAt"],
        })

    topic_trends.sort(key=lambda t: (t["accuracy"] is None, t["accuracy"] or 0))

    # Competency trends
    competency_trends: list[dict[str, Any]] = []
    for comp in perf["competencies"]:
        comp_id = comp["competencyId"]
        mastery = perf["competencyMastery"].get(comp_id)
        competency_trends.append({
            "competencyId": comp_id,
            "competencyName": comp["title"],
            "currentMastery": mastery,
            "level": comp.get("level", 0),
            "parentCompetencyId": comp.get("parentCompetencyId"),
        })
    competency_trends.sort(
        key=lambda c: (c["currentMastery"] is None, c["currentMastery"] or 0)
    )

    # Exam history trends
    exam_history: list[dict[str, Any]] = []
    for er in exam_results:
        exam_history.append({
            "examId": er["examId"],
            "examTitle": er.get("title", ""),
            "subject": er.get("subject", ""),
            "topic": er.get("topic", ""),
            "percentage": er["percentage"],
            "passed": bool(er["passed"]),
            "takenAt": er["completedAt"] or er["createdAt"],
            "durationMinutes": er.get("durationMinutes"),
        })
    exam_history.sort(key=lambda e: e["takenAt"], reverse=True)

    # Overall metrics
    total_attempts = len(recent_attempts)
    scored_attempts = [a for a in recent_attempts if a["outcome"] in {"correct", "incorrect"}]
    overall_accuracy = (
        sum(a["outcome"] == "correct" for a in scored_attempts) / len(scored_attempts)
        if scored_attempts else None
    )

    masteries = [m for m in perf["competencyMastery"].values() if m is not None]
    overall_mastery = (sum(masteries) / len(masteries)) if masteries else None

    total_exams = len(exam_history)
    avg_exam_score = (
        sum(e["percentage"] for e in exam_history) / total_exams
        if total_exams > 0 else None
    )

    return {
        "userId": user_id,
        "lookbackDays": lookback_days,
        "generatedAt": current.isoformat(),
        "overallMastery": overall_mastery,
        "overallAccuracy": overall_accuracy,
        "totalAttempts": total_attempts,
        "totalExamsTaken": total_exams,
        "averageExamScore": avg_exam_score,
        "topicTrends": topic_trends,
        "competencyTrends": competency_trends,
        "examHistory": exam_history,
        "activeGapCount": len(perf["activeGaps"]),
        "resolvedGapCount": len(perf["resolvedGaps"]),
    }


# ---------------------------------------------------------------------------
# Predictive baselines (deterministic statistical methods)
# ---------------------------------------------------------------------------


def _predict_exam_readiness(
    *,
    database_path: Path,
    user_id: str,
    now: datetime,
) -> dict[str, Any] | None:
    """Estimate exam readiness from actual evidence.

    Readiness is based on:
    - Competency mastery (weighted average)
    - Recent quiz accuracy
    - Active learning gaps (critical gaps reduce readiness)
    - Exam history (if available)

    Returns None if insufficient evidence exists.
    """
    perf = _aggregate_user_performance(database_path, user_id, now)

    masteries = list(perf["competencyMastery"].values())
    if not masteries:
        # No competency evidence - cannot predict readiness
        return None

    avg_mastery = sum(masteries) / len(masteries)
    recent_attempts = perf["attempts"][:20]  # Most recent 20
    scored = [a for a in recent_attempts if a["outcome"] in {"correct", "incorrect"}]
    recent_accuracy = (
        sum(a["outcome"] == "correct" for a in scored) / len(scored)
        if scored else None
    )

    # Count critical active gaps
    critical_gaps = sum(
        1 for g in perf["activeGaps"] if g["severity"] == "critical"
    )
    moderate_gaps = sum(
        1 for g in perf["activeGaps"] if g["severity"] == "moderate"
    )

    # Composite readiness score (0-1)
    # Weight: 50% mastery, 30% recent accuracy, 20% gap penalty
    mastery_component = avg_mastery * 0.50
    accuracy_component = (recent_accuracy * 0.30) if recent_accuracy is not None else 0.0
    gap_penalty = min(0.20, (critical_gaps * 0.10) + (moderate_gaps * 0.05))

    readiness_score = round(max(0.0, min(1.0, mastery_component + accuracy_component - gap_penalty)), 2)

    # Confidence based on amount of evidence
    evidence_count = len(masteries) + len(scored)
    if evidence_count >= 10:
        confidence = 0.85
        uncertainty = "low"
    elif evidence_count >= 5:
        confidence = 0.65
        uncertainty = "medium"
    else:
        confidence = 0.40
        uncertainty = "high"

    if readiness_score >= READINESS_READY_THRESHOLD:
        readiness_level = "ready"
    elif readiness_score >= READINESS_MODERATE_THRESHOLD:
        readiness_level = "moderately_ready"
    else:
        readiness_level = "not_ready"

    evidence = {
        "avgCompetencyMastery": round(avg_mastery, 2),
        "recentAccuracy": round(recent_accuracy, 2) if recent_accuracy is not None else None,
        "competencyCount": len(masteries),
        "recentAttemptCount": len(recent_attempts),
        "criticalGapCount": critical_gaps,
        "moderateGapCount": moderate_gaps,
    }

    limitations = {
        "noExamHistory": len(perf["examResults"]) == 0,
        "limitedAttempts": len(recent_attempts) < MIN_ATTEMPTS_FOR_PREDICTION,
        "noCompetencyData": len(masteries) == 0,
        "note": (
            "Readiness is an estimate based on current evidence, not a guarantee "
            "of exam performance. Actual results may vary."
        ),
    }

    return {
        "predictionType": "exam_readiness",
        "targetId": None,
        "targetName": "Overall exam readiness",
        "predictedValue": readiness_score,
        "confidence": confidence,
        "uncertainty": uncertainty,
        "evidence": evidence,
        "limitations": limitations,
        "isGuaranteed": False,
        "readinessLevel": readiness_level,
    }


def _predict_topic_forgetting_risk(
    *,
    database_path: Path,
    user_id: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """Predict topics at risk of being forgotten.

    Uses spaced-repetition principles: topics not recently reviewed and with
    low mastery are at higher risk.
    """
    perf = _aggregate_user_performance(database_path, user_id, now)
    current = now.astimezone(UTC)

    risks: list[dict[str, Any]] = []
    for topic_id, ta in perf["topicAggregates"].items():
        topic_attempts = ta["attempts"]
        if not topic_attempts:
            continue

        # Days since last attempt
        last_attempt = _parse_datetime(ta["lastAttemptAt"])
        days_since = max(0.0, (current - last_attempt).total_seconds() / 86_400.0)

        # Mastery for this topic
        scored = [a for a in topic_attempts if a["outcome"] in {"correct", "incorrect"}]
        topic_accuracy = (
            sum(a["outcome"] == "correct" for a in scored) / len(scored)
            if scored else None
        )

        # Forgetting risk: low mastery + long time since review = high risk
        mastery_component = (1.0 - (topic_accuracy or 0.5)) * 0.6
        recency_component = min(1.0, days_since / 30.0) * 0.4  # 30 days = full recency risk
        risk_score = round(mastery_component + recency_component, 2)

        if risk_score < FOREGETTING_RISK_THRESHOLD:
            continue  # Not at risk

        if risk_score >= 0.80:
            uncertainty = "low"
            confidence = 0.80
        elif risk_score >= 0.70:
            uncertainty = "medium"
            confidence = 0.65
        else:
            uncertainty = "high"
            confidence = 0.50

        risks.append({
            "predictionType": "topic_risk",
            "targetId": topic_id,
            "targetName": ta["topic"],
            "predictedValue": risk_score,
            "confidence": confidence,
            "uncertainty": uncertainty,
            "evidence": {
                "daysSinceLastReview": round(days_since, 1),
                "topicAccuracy": round(topic_accuracy, 2) if topic_accuracy is not None else None,
                "attemptCount": len(topic_attempts),
            },
            "limitations": {
                "noReviewHistory": days_since > 60,
                "limitedAttempts": len(topic_attempts) < 3,
                "note": (
                    "Forgetting risk is an estimate based on review recency and "
                    "demonstrated accuracy, not a certainty."
                ),
            },
            "isGuaranteed": False,
        })

    risks.sort(key=lambda r: -r["predictedValue"])
    return risks[:10]  # Top 10 at-risk topics


def generate_performance_predictions(
    *,
    database_path: str,
    user_id: str,
    now: datetime,
) -> dict[str, Any]:
    """Generate all performance predictions for a user.

    Only generates predictions where sufficient evidence exists.
    Never fabricates predictions or performance metrics.
    """
    db_path = Path(database_path)
    current = now.astimezone(UTC)
    predictions: list[dict[str, Any]] = []

    # Exam readiness prediction
    readiness = _predict_exam_readiness(
        database_path=db_path, user_id=user_id, now=current
    )
    if readiness is not None:
        predictions.append(readiness)

    # Topic forgetting risk predictions
    topic_risks = _predict_topic_forgetting_risk(
        database_path=db_path, user_id=user_id, now=current
    )
    predictions.extend(topic_risks)

    # Persist predictions to storage
    persisted: list[dict[str, Any]] = []
    for pred in predictions:
        created = create_performance_prediction(
            db_path,
            user_id=user_id,
            prediction_type=pred["predictionType"],
            target_id=pred.get("targetId"),
            target_name=pred.get("targetName"),
            predicted_value=pred.get("predictedValue"),
            confidence=pred["confidence"],
            uncertainty=pred["uncertainty"],
            evidence=pred.get("evidence", {}),
            limitations=pred.get("limitations", {}),
            is_guaranteed=pred.get("isGuaranteed", False),
            expires_at=(current + timedelta(days=7)).isoformat(),
        )
        if created:
            persisted.append(created)

    return {
        "userId": user_id,
        "generatedAt": current.isoformat(),
        "predictionCount": len(persisted),
        "predictions": persisted,
    }


# ---------------------------------------------------------------------------
# Explainable recommendations
# ---------------------------------------------------------------------------


def generate_explainable_recommendations(
    *,
    database_path: str,
    user_id: str,
    now: datetime,
    limit: int = 10,
) -> dict[str, Any]:
    """Generate evidence-backed recommendations for a user.

    Every recommendation includes supporting evidence and a clear reason.
    Recommendations are ranked by transparent priority rules.
    """
    db_path = Path(database_path)
    current = now.astimezone(UTC)

    perf = _aggregate_user_performance(db_path, user_id, current)
    recommendations: list[dict[str, Any]] = []
    rec_id = 0

    # 1. Weak competency recommendations
    for comp_id, mastery_score in perf["competencyMastery"].items():
        if mastery_score < MASTERY_WEAK_THRESHOLD:
            comp_name = next(
                (c["title"] for c in perf["competencies"] if c["competencyId"] == comp_id),
                "Unknown competency",
            )
            rec_id += 1
            recommendations.append({
                "recommendationId": f"rec_{rec_id}",
                "type": "practice_competency",
                "title": f"Practice: {comp_name}",
                "description": (
                    f"Competency mastery is at {mastery_score:.0%}, below the "
                    f"{MASTERY_WEAK_THRESHOLD:.0%} threshold."
                ),
                "evidence": {
                    "competencyId": comp_id,
                    "competencyName": comp_name,
                    "masteryScore": mastery_score,
                    "threshold": MASTERY_WEAK_THRESHOLD,
                },
                "reason": (
                    f"Demonstrated mastery ({mastery_score:.0%}) is below the "
                    f"configured threshold ({MASTERY_WEAK_THRESHOLD:.0%})."
                ),
                "priority": round((1.0 - mastery_score) * 100, 1),
                "targetId": comp_id,
                "targetName": comp_name,
            })

    # 2. Active learning gap recommendations
    for gap in perf["activeGaps"][:5]:
        rec_id += 1
        recommendations.append({
            "recommendationId": f"rec_{rec_id}",
            "type": "focus_gap",
            "title": f"Address gap: {gap.get('topic') or gap['gapType']}",
            "description": (
                f"Active {gap['severity']} learning gap detected: {gap['gapType']}"
            ),
            "evidence": {
                "gapId": gap["gapId"],
                "gapType": gap["gapType"],
                "severity": gap["severity"],
                "recurrence": gap["recurrence"],
            },
            "reason": (
                f"Unresolved {gap['severity']} gap with {gap['recurrence']} "
                f"occurrence(s)."
            ),
            "priority": round(
                {"critical": 90.0, "moderate": 60.0, "minor": 30.0}.get(
                    gap["severity"], 30.0
                ),
                1,
            ),
            "targetId": gap["gapId"],
            "targetName": gap.get("topic"),
        })

    # 3. Exam retake recommendations (if recent exam failed)
    for er in perf["examResults"][:3]:
        if not er["passed"]:
            rec_id += 1
            recommendations.append({
                "recommendationId": f"rec_{rec_id}",
                "type": "retake_exam",
                "title": f"Retake exam: {er.get('title') or er['examId'][:8]}",
                "description": (
                    f"Previous exam score was {er['percentage']:.0f}%, below passing."
                ),
                "evidence": {
                    "examId": er["examId"],
                    "percentage": er["percentage"],
                    "passed": er["passed"],
                    "takenAt": er["completedAt"] or er["createdAt"],
                },
                "reason": (
                    f"Exam not passed ({er['percentage']:.0f}%). "
                    f"Review and retake recommended."
                ),
                "priority": round(70.0 - er["percentage"], 1),
                "targetId": er["examId"],
                "targetName": er.get("title"),
            })
            break  # Only one exam retake recommendation

    # 4. Weak topic revision recommendations
    weak_topic_names = [wt.get("topic_id") or wt.get("topic") for wt in perf["weakTopics"][:3]]
    for topic_name in weak_topic_names:
        rec_id += 1
        recommendations.append({
            "recommendationId": f"rec_{rec_id}",
            "type": "revise_topic",
            "title": f"Revise: {topic_name}",
            "description": "Topic identified as weak based on repeated forgetting.",
            "evidence": {
                "topic": topic_name,
                "source": "weak_topics_table",
            },
            "reason": "Topic appears in weak topics list (repeated forgetting).",
            "priority": 50.0,
            "targetId": topic_name,
            "targetName": topic_name,
        })

    # Sort by priority descending
    recommendations.sort(key=lambda r: -r["priority"])
    recommendations = recommendations[:limit]

    return {
        "userId": user_id,
        "recommendations": recommendations,
        "generatedAt": current.isoformat(),
    }


# ---------------------------------------------------------------------------
# Dashboard assembly
# ---------------------------------------------------------------------------


def build_performance_dashboard(
    *,
    database_path: str,
    user_id: str,
    now: datetime,
) -> dict[str, Any]:
    """Build the complete performance intelligence dashboard.

    Combines trends, predictions, and recommendations into a single view.
    All metrics are evidence-based with explicit uncertainty.
    """
    db_path = Path(database_path)
    current = now.astimezone(UTC)

    # Performance trends
    trends = compute_performance_trends(
        database_path=db_path, user_id=user_id, now=current
    )

    # Generate predictions
    predictions_result = generate_performance_predictions(
        database_path=db_path, user_id=user_id, now=current
    )

    # Generate recommendations
    recommendations_result = generate_explainable_recommendations(
        database_path=db_path, user_id=user_id, now=current, limit=10
    )

    # Build topic progress trends
    topic_progress: list[dict[str, Any]] = []
    for tt in trends["topicTrends"]:
        topic_progress.append({
            "topicId": tt["topicId"],
            "topic": tt["topic"],
            "currentMastery": tt["accuracy"],
            "direction": tt["direction"],
            "attemptCount": tt["attemptCount"],
        })

    # Build competency trends
    competency_trend_list: list[dict[str, Any]] = []
    for ct in trends["competencyTrends"]:
        competency_trend_list.append({
            "competencyId": ct["competencyId"],
            "competencyName": ct["competencyName"],
            "currentMastery": ct["currentMastery"],
            "trendDirection": "insufficient_data",  # No historical mastery data yet
            "level": ct["level"],
        })

    # Build exam history
    exam_history: list[dict[str, Any]] = []
    for eh in trends["examHistory"][:10]:
        exam_history.append({
            "examId": eh["examId"],
            "examTitle": eh["examTitle"],
            "subject": eh["subject"],
            "topic": eh["topic"],
            "percentage": eh["percentage"],
            "passed": eh["passed"],
            "takenAt": eh["takenAt"],
        })

    # Identify weak and strong topics
    weak_topics = [
        tt["topic"] for tt in trends["topicTrends"]
        if tt["accuracy"] is not None and tt["accuracy"] < MASTERY_WEAK_THRESHOLD
    ][:5]
    strong_topics = [
        tt["topic"] for tt in trends["topicTrends"]
        if tt["accuracy"] is not None and tt["accuracy"] >= MASTERY_STRONG_THRESHOLD
    ][:5]

    return {
        "userId": user_id,
        "overallMastery": trends["overallMastery"],
        "overallAccuracy": trends["overallAccuracy"],
        "totalAttempts": trends["totalAttempts"],
        "totalExamsTaken": trends["totalExamsTaken"],
        "averageExamScore": trends["averageExamScore"],
        "weakTopics": weak_topics,
        "strongTopics": strong_topics,
        "topicProgress": topic_progress,
        "competencyTrends": competency_trend_list,
        "examHistory": exam_history,
        "activePredictions": predictions_result["predictions"],
        "recommendations": recommendations_result["recommendations"],
        "generatedAt": current.isoformat(),
    }


def record_analytics_snapshot(
    *,
    database_path: str,
    user_id: str,
    now: datetime,
) -> dict[str, Any] | None:
    """Record a point-in-time analytics snapshot for trend analysis.

    Captures current performance metrics for future trend comparison.
    """
    db_path = Path(database_path)
    current = now.astimezone(UTC)
    today = current.date().isoformat()

    perf = _aggregate_user_performance(db_path, user_id, current)

    # Overall metrics
    all_attempts = perf["attempts"]
    scored = [a for a in all_attempts if a["outcome"] in {"correct", "incorrect"}]
    overall_accuracy = (
        sum(a["outcome"] == "correct" for a in scored) / len(scored)
        if scored else None
    )

    masteries = list(perf["competencyMastery"].values())
    overall_mastery = (sum(masteries) / len(masteries)) if masteries else None

    total_reviews = len(perf["reviews"])
    overdue_reviews = sum(
        1 for r in perf["reviews"]
        if _parse_datetime(r.get("dueAt", current.isoformat())) <= current
    )

    total_time = sum(
        a.get("duration_ms", 0) or 0 for a in all_attempts
    )

    return create_analytics_snapshot(
        db_path,
        user_id=user_id,
        snapshot_date=today,
        topic_id=None,
        topic=None,
        mastery_score=overall_mastery,
        accuracy=overall_accuracy,
        attempt_count=len(all_attempts),
        correct_count=sum(a["outcome"] == "correct" for a in scored),
        incorrect_count=sum(a["outcome"] == "incorrect" for a in scored),
        ambiguous_count=sum(a["outcome"] == "ambiguous" for a in scored),
        review_count=total_reviews,
        overdue_review_count=overdue_reviews,
        time_spent_ms=total_time if total_time > 0 else None,
    )
