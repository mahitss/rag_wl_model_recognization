"""Rule-based daily study recommendations from persisted learner activity."""

import hashlib
from datetime import UTC, datetime
from typing import Any


def build_daily_recommendations(
    *,
    analytics: dict[str, Any],
    now: datetime,
    limit: int = 3,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Rank topics from stored reviews, quiz outcomes, sessions, and graph links."""
    current = now.astimezone(UTC)
    current_day = current.date().isoformat()
    relationships = analytics.get("relationships", [])
    concepts_by_id = {item["conceptId"]: item["name"] for item in analytics.get("concepts", [])}
    sessions_today: dict[str, int] = {}
    for session in analytics.get("sessions", []):
        completed_at = session.get("completedAt")
        if completed_at and completed_at[:10] == current_day:
            sessions_today[session["topicId"]] = sessions_today.get(session["topicId"], 0) + 1

    candidates: list[dict[str, Any]] = []
    for topic in analytics.get("topics", []):
        citations = analytics.get("topicSources", {}).get(topic["topicId"], [])
        # A study recommendation must point to an existing source passage.
        if not citations:
            continue
        concept_id = topic.get("graphConceptId")
        related_status: dict[str, set[str]] = {}
        if concept_id:
            for edge in relationships:
                if edge["sourceConceptId"] == concept_id:
                    related_status.setdefault(edge["targetConceptId"], set()).add(edge["status"])
                elif edge["targetConceptId"] == concept_id:
                    related_status.setdefault(edge["sourceConceptId"], set()).add(edge["status"])
        related_ids = set(related_status)

        overdue = int(topic.get("overdueReviewCount", 0))
        incorrect = int(topic.get("incorrectQuestions", 0))
        ambiguous = int(topic.get("ambiguousQuestions", 0))
        score = overdue * 100 + (40 if topic.get("weak") else 0)
        score += min(incorrect, 3) * 10 + min(ambiguous, 3) * 5
        score += min(len(related_ids), 5)
        prior_sessions = sessions_today.get(topic["topicId"], 0)
        score -= min(prior_sessions, 2) * 10

        reasons: list[str] = []
        if overdue:
            reasons.append(f"{overdue} review question(s) for this topic are due or overdue.")
        if topic.get("weak") and topic.get("mastery") is not None:
            reasons.append(
                "Its recency-weighted quiz score is below the configured review prompt threshold."
            )
        if incorrect:
            reasons.append(f"Your latest results include {incorrect} incorrect question(s).")
        if ambiguous:
            reasons.append(f"Your latest results include {ambiguous} uncertain question(s).")
        if prior_sessions:
            reasons.append(f"You completed {prior_sessions} study session(s) on this topic today.")
        if related_ids:
            names = [concepts_by_id[item] for item in sorted(related_ids) if item in concepts_by_id]
            if names:
                verified = sum("human_verified" in statuses for statuses in related_status.values())
                candidates_count = sum(
                    "candidate" in statuses for statuses in related_status.values()
                )
                labels = []
                if verified:
                    labels.append(f"{verified} human-verified")
                if candidates_count:
                    labels.append(f"{candidates_count} candidate")
                reasons.append(
                    "The knowledge graph connects it to "
                    + ", ".join(names[:5])
                    + f" ({' and '.join(labels)} relationship(s))."
                )
        if not reasons:
            reasons.append(
                f"You have stored quiz and source material for this topic across "
                f"{topic.get('attemptedQuestions', topic.get('attemptCount', 0))} "
                "attempted question(s)."
            )

        recommendation_id = hashlib.sha256(
            f"{user_id or ''}:{current_day}:{topic['topicId']}".encode()
        ).hexdigest()[:24]
        candidates.append(
            {
                "recommendationId": f"coach_{recommendation_id}",
                "topicId": topic["topicId"],
                "topic": topic["topic"],
                "title": f"Review {topic['topic']}",
                "priority": score,
                "reasons": reasons,
                "graphConceptId": concept_id,
                "relatedConceptIds": sorted(related_ids),
                "estimatedMinutes": 20,
                "citations": citations,
                "quizTopic": topic["topic"],
            }
        )
    candidates.sort(key=lambda item: (-item["priority"], item["topic"].casefold()))
    return candidates[: max(0, limit)]
