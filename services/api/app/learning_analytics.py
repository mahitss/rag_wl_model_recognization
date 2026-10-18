"""Deterministic reporting over persisted quiz, session, and review records."""

import unicodedata
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

MASTERY_HALF_LIFE_DAYS = 30
WEAK_TOPIC_THRESHOLD = 0.60
REVIEW_INTERVAL_DAYS = {"incorrect": 1, "ambiguous": 1, "correct": 7}


def _parse_datetime(value: str | datetime) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def calculate_mastery(
    attempts: list[dict[str, Any]],
    *,
    now: datetime,
    half_life_days: int = MASTERY_HALF_LIFE_DAYS,
) -> float | None:
    """Weighted accuracy over each question's latest scored result; ambiguous is unscored."""
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    current = _parse_datetime(now)
    latest: dict[str, dict[str, Any]] = {}
    for position, attempt in enumerate(attempts):
        question_id = str(attempt["questionId"])
        previous = latest.get(question_id)
        attempted_at = _parse_datetime(attempt["createdAt"])
        sequence = int(attempt.get("sequence", position))
        previous_sequence = int(previous.get("sequence", -1)) if previous else -1
        if previous is None or (attempted_at, sequence) > (
            _parse_datetime(previous["createdAt"]),
            previous_sequence,
        ):
            latest[question_id] = attempt

    weighted_correct = 0.0
    weighted_scored = 0.0
    for attempt in latest.values():
        outcome = attempt["outcome"]
        if outcome not in {"correct", "incorrect"}:
            continue
        age_days = max(
            0.0, (current - _parse_datetime(attempt["createdAt"])).total_seconds() / 86_400
        )
        weight = 2 ** (-age_days / half_life_days)
        weighted_scored += weight
        if outcome == "correct":
            weighted_correct += weight
    if weighted_scored == 0:
        return None
    return weighted_correct / weighted_scored


def build_analytics(
    *,
    attempts: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    topic_sources: dict[str, list[dict[str, Any]]],
    concepts: list[dict[str, Any]],
    now: datetime,
    daily_window: int = 14,
) -> dict[str, Any]:
    current = _parse_datetime(now)
    by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    latest_question: dict[str, dict[str, Any]] = {}
    for position, attempt in enumerate(attempts):
        by_topic[attempt["topicId"]].append(attempt)
        previous = latest_question.get(attempt["questionId"])
        sequence = int(attempt.get("sequence", position))
        previous_sequence = int(previous.get("sequence", -1)) if previous else -1
        if previous is None or (
            _parse_datetime(attempt["createdAt"]),
            sequence,
        ) > (
            _parse_datetime(previous["createdAt"]),
            previous_sequence,
        ):
            latest_question[attempt["questionId"]] = attempt

    concept_aliases: dict[str, set[str]] = defaultdict(set)
    for concept in concepts:
        for label in [concept["name"], *concept["aliases"]]:
            concept_aliases[_normalize(label)].add(concept["conceptId"])

    topic_rows = []
    for topic_id, items in by_topic.items():
        latest = [row for row in latest_question.values() if row["topicId"] == topic_id]
        correct = sum(row["outcome"] == "correct" for row in latest)
        incorrect = sum(row["outcome"] == "incorrect" for row in latest)
        ambiguous = sum(row["outcome"] == "ambiguous" for row in latest)
        accuracy = correct / (correct + incorrect) if correct + incorrect else None
        mastery = calculate_mastery(items, now=current)
        topic = items[-1]["topic"]
        matching_concepts = concept_aliases.get(_normalize(topic), set())
        concept_id = next(iter(matching_concepts)) if len(matching_concepts) == 1 else None
        topic_reviews = [item for item in reviews if item["topicId"] == topic_id]
        overdue = [item for item in topic_reviews if _parse_datetime(item["dueAt"]) <= current]
        topic_rows.append(
            {
                "topicId": topic_id,
                "topic": topic,
                "attemptCount": len(items),
                "attemptedQuestions": len({item["questionId"] for item in items}),
                "correctQuestions": correct,
                "incorrectQuestions": incorrect,
                "ambiguousQuestions": ambiguous,
                "accuracy": accuracy,
                "mastery": mastery,
                "weak": mastery is not None and mastery < WEAK_TOPIC_THRESHOLD,
                "overdueReviewCount": len(overdue),
                "nextReviewAt": min((item["dueAt"] for item in topic_reviews), default=None),
                "graphConceptId": concept_id,
                "citations": topic_sources.get(topic_id, []),
            }
        )
    topic_rows.sort(
        key=lambda row: (row["mastery"] is not None, row["mastery"] or 0, row["topic"].casefold())
    )

    scored = [row for row in latest_question.values() if row["outcome"] in {"correct", "incorrect"}]
    overall_accuracy = (
        sum(row["outcome"] == "correct" for row in scored) / len(scored) if scored else None
    )
    today = current.date()
    first_day = today - timedelta(days=daily_window - 1)
    daily = {
        first_day + timedelta(days=offset): {
            "attempts": 0,
            "correct": 0,
            "completedStudySessions": 0,
        }
        for offset in range(daily_window)
    }
    for attempt in attempts:
        day = _parse_datetime(attempt["createdAt"]).date()
        if day in daily:
            daily[day]["attempts"] += 1
            daily[day]["correct"] += attempt["outcome"] == "correct"
    for session in sessions:
        if session["completedAt"]:
            day = _parse_datetime(session["completedAt"]).date()
            if day in daily:
                daily[day]["completedStudySessions"] += 1
    recent_activity = [
        {
            "activityId": row["attemptId"],
            "kind": "quiz_attempt",
            "topic": row["topic"],
            "outcome": row["outcome"],
            "occurredAt": row["createdAt"],
            "prompt": row["prompt"],
        }
        for row in attempts
    ]
    recent_activity.extend(
        {
            "activityId": row["sessionId"],
            "kind": "study_session",
            "topic": row["topic"],
            "outcome": None,
            "occurredAt": row["completedAt"],
            "prompt": None,
        }
        for row in sessions
        if row["completedAt"]
    )
    recent_activity.sort(key=lambda row: _parse_datetime(row["occurredAt"]), reverse=True)
    review_history = [
        {
            **item,
            "status": "overdue" if _parse_datetime(item["dueAt"]) <= current else "upcoming",
        }
        for item in sorted(reviews, key=lambda item: _parse_datetime(item["dueAt"]))[:100]
    ]
    completed_sessions = sum(session["completedAt"] is not None for session in sessions)
    return {
        "generatedAt": current.isoformat(),
        "totals": {
            "attemptCount": len(attempts),
            "attemptedQuestions": len({row["questionId"] for row in attempts}),
            "correctQuestions": sum(
                row["outcome"] == "correct" for row in latest_question.values()
            ),
            "incorrectQuestions": sum(
                row["outcome"] == "incorrect" for row in latest_question.values()
            ),
            "ambiguousQuestions": sum(
                row["outcome"] == "ambiguous" for row in latest_question.values()
            ),
            "quizAccuracy": overall_accuracy,
            "completedStudySessions": completed_sessions,
            "activeStudySessions": sum(session["completedAt"] is None for session in sessions),
            "overdueReviews": sum(_parse_datetime(item["dueAt"]) <= current for item in reviews),
        },
        "topics": topic_rows,
        "dailyActivity": [{"date": day.isoformat(), **values} for day, values in daily.items()],
        "recentActivity": recent_activity[:50],
        "reviewHistory": review_history,
        "activeSessions": [session for session in sessions if session["completedAt"] is None],
        "methodology": {
            "mastery": (
                "Latest scored result per question, weighted by recency; "
                "weight = 2^(-ageDays/30). Ambiguous outcomes are excluded from the score."
            ),
            "recencyHalfLifeDays": MASTERY_HALF_LIFE_DAYS,
            "weakTopicThreshold": WEAK_TOPIC_THRESHOLD,
            "reviewIntervalsDays": REVIEW_INTERVAL_DAYS,
            "reviewDueBoundary": "dueAt <= current UTC time",
        },
    }
