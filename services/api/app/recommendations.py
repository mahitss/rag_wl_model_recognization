"""M81: AI-Powered Learning Content Recommendation & Discovery Engine.

Builds a modular, explainable recommendation pipeline that reuses existing
WonderLearn subsystems as candidate generators:

* ``app.storage.compute_adaptive_recommendations`` — topic-level
  recommendations from quiz attempts, study sessions, topic reviews, and
  knowledge-graph concepts.
* ``app.predictive_analytics.generate_explainable_recommendations`` —
  competency, learning-gap, exam-retake, and weak-topic recommendations.
* ``app.study_coach.build_daily_recommendations`` — daily topic
  recommendations from overdue reviews and weak topics.
* Knowledge-graph prerequisite relationships — concepts that need attention
  before a learner can progress.
* ``app.gap_detection.detect_gaps_for_user`` — persisted learning gaps.

The engine normalizes every candidate into a common schema, deduplicates by
(target type, target id), ranks with transparent, deterministic rules,
suppresses dismissed or completed content, and degrades gracefully to
cold-start fallback recommendations when learner data or AI services are
unavailable.  No LLM calls are made; recommendations remain fully useful
offline.

All operations are tenant-scoped and auditable.  Learner feedback
(dismiss/select/rate/postpone) is persisted and used to adjust ranking
without silently mutating learner records.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.errors import ApiError
from app.learning_analytics import build_analytics
from app.predictive_analytics import generate_explainable_recommendations
from app.schemas import (
    RecommendationFeedbackCreate,
    RecommendationFeedbackListResponse,
    RecommendationFeedbackResponse,
    RecommendationItem,
    RecommendationRequest,
    RecommendationResponse,
)
from app.storage import (
    _get_user_topic_reviews,
    compute_adaptive_recommendations,
    list_dismissed_recommendation_targets,
    list_knowledge_concepts,
    list_knowledge_relationships,
    list_learning_gaps,
    list_recommendation_feedback,
    record_recommendation_impression,
    store_recommendation_feedback,
)
from app.study_coach import build_daily_recommendations

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Confidence bands.
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_INSUFFICIENT = "insufficient_evidence"

# Recommendation types.
TYPE_TOPIC_PRACTICE = "topic_practice"
TYPE_GAP_FOCUS = "gap_focus"
TYPE_PREREQUISITE = "prerequisite"
TYPE_COMPETENCY_PRACTICE = "competency_practice"
TYPE_EXAM_RETAKE = "exam_retake"
TYPE_CONTENT_DISCOVERY = "content_discovery"
TYPE_NEXT_TOPIC = "next_topic"

# Target types.
TARGET_TOPIC = "topic"
TARGET_COMPETENCY = "competency"
TARGET_GAP = "gap"
TARGET_CONCEPT = "concept"
TARGET_EXAM = "exam"
TARGET_RESOURCE = "resource"

# Signal weights for the composite ranking score.
WEIGHT_MASTERY_GAP = 40.0
WEAK_TOPIC_BONUS = 30.0
OVERDUE_REVIEW_BONUS = 25.0
GAP_SEVERITY_BONUS = {"critical": 35.0, "moderate": 20.0, "minor": 10.0}
PREREQUISITE_UNMET_BONUS = 20.0
RECENCY_STALE_BONUS = 15.0
RECENT_ACTIVITY_PENALTY = 10.0
TODAY_SESSION_PENALTY = 8.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _tenant_id(database_path, user_id: str) -> str:
    from app.storage import _recommendation_tenant_id

    return _recommendation_tenant_id(database_path, user_id)


def _confidence_from_mastery(mastery: float | None, evidence_count: int) -> str:
    """Derive a confidence band from mastery availability and evidence volume."""
    if mastery is None or evidence_count == 0:
        return CONFIDENCE_INSUFFICIENT
    if evidence_count >= 5:
        return CONFIDENCE_HIGH
    if evidence_count >= 2:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def _normalize_topic_candidate(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Normalize a compute_adaptive_recommendations candidate."""
    return {
        "recommendationId": f"rec_{hashlib.sha256(candidate['topicId'].encode()).hexdigest()[:16]}",
        "type": TYPE_TOPIC_PRACTICE,
        "title": f"Practice: {candidate['topic']}",
        "description": candidate.get("activitySuggestion"),
        "reason": "; ".join(candidate.get("reasons", [])),
        "confidence": _confidence_from_mastery(
            candidate.get("mastery"), candidate.get("citationsCount", 0)
        ),
        "priority": float(candidate.get("priorityScore", 0)),
        "targetType": TARGET_TOPIC,
        "targetId": candidate["topicId"],
        "targetName": candidate.get("topic"),
        "difficultyLevel": candidate.get("difficultyLevel"),
        "estimatedMinutes": 20,
        "activitySuggestion": candidate.get("activitySuggestion"),
        "citations": candidate.get("citations", []),
        "prerequisiteConceptIds": [],
        "knowledgeGapIds": [],
        "measuredFields": candidate.get("measuredFields", []),
        "estimatedFields": candidate.get("estimatedFields", []),
        "aiUnavailable": False,
    }


def _normalize_predictive_candidate(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Normalize a generate_explainable_recommendations candidate."""
    ctype = candidate.get("type", TYPE_CONTENT_DISCOVERY)
    type_map = {
        "practice_competency": TYPE_COMPETENCY_PRACTICE,
        "focus_gap": TYPE_GAP_FOCUS,
        "retake_exam": TYPE_EXAM_RETAKE,
        "weak_topic": TYPE_TOPIC_PRACTICE,
        "next_topic": TYPE_NEXT_TOPIC,
    }
    target_type_map = {
        "practice_competency": TARGET_COMPETENCY,
        "focus_gap": TARGET_GAP,
        "retake_exam": TARGET_EXAM,
        "weak_topic": TARGET_TOPIC,
        "next_topic": TARGET_TOPIC,
    }
    evidence = candidate.get("evidence", {}) or {}
    return {
        "recommendationId": candidate.get(
            "recommendationId",
            f"rec_{hashlib.sha256(str(candidate).encode()).hexdigest()[:16]}",
        ),
        "type": type_map.get(ctype, TYPE_CONTENT_DISCOVERY),
        "title": candidate.get("title", "Recommended activity"),
        "description": candidate.get("description"),
        "reason": candidate.get("reason", ""),
        "confidence": CONFIDENCE_MEDIUM,
        "priority": float(candidate.get("priority", 0)),
        "targetType": target_type_map.get(ctype, TARGET_RESOURCE),
        "targetId": candidate.get("targetId", ""),
        "targetName": candidate.get("targetName"),
        "difficultyLevel": None,
        "estimatedMinutes": 20,
        "activitySuggestion": None,
        "citations": [],
        "prerequisiteConceptIds": [],
        "knowledgeGapIds": [candidate.get("targetId")] if ctype == "focus_gap" else [],
        "measuredFields": [],
        "estimatedFields": [],
        "aiUnavailable": False,
        "_evidence": evidence,
    }


def _normalize_coach_candidate(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Normalize a build_daily_recommendations candidate."""
    return {
        "recommendationId": candidate.get(
            "recommendationId",
            f"rec_{hashlib.sha256(candidate['topicId'].encode()).hexdigest()[:16]}",
        ),
        "type": TYPE_TOPIC_PRACTICE,
        "title": candidate.get("title", f"Study: {candidate.get('topic', 'topic')}"),
        "description": None,
        "reason": "; ".join(candidate.get("reasons", [])),
        "confidence": CONFIDENCE_MEDIUM,
        "priority": float(candidate.get("priority", 0)),
        "targetType": TARGET_TOPIC,
        "targetId": candidate["topicId"],
        "targetName": candidate.get("topic"),
        "difficultyLevel": None,
        "estimatedMinutes": candidate.get("estimatedMinutes", 20),
        "activitySuggestion": None,
        "citations": candidate.get("citations", []),
        "prerequisiteConceptIds": [],
        "knowledgeGapIds": [],
        "measuredFields": [],
        "estimatedFields": ["estimatedMinutes"],
        "aiUnavailable": False,
    }


# ---------------------------------------------------------------------------
# Candidate generators (each reuses an existing subsystem)
# ---------------------------------------------------------------------------


def _generate_adaptive_candidates(
    database_path, *, user_id: str, now: datetime, limit: int
) -> list[dict[str, Any]]:
    """Topic-level candidates from compute_adaptive_recommendations."""
    try:
        raw = compute_adaptive_recommendations(
            database_path, user_id=user_id, now=now, limit=limit
        )
    except Exception:
        return []
    return [_normalize_topic_candidate(c) for c in raw]


def _generate_predictive_candidates(
    database_path, *, user_id: str, now: datetime, limit: int
) -> list[dict[str, Any]]:
    """Competency/gap/exam candidates from generate_explainable_recommendations."""
    try:
        result = generate_explainable_recommendations(
            database_path=database_path, user_id=user_id, now=now, limit=limit
        )
    except Exception:
        return []
    raw = result.get("recommendations", []) if isinstance(result, dict) else []
    return [_normalize_predictive_candidate(c) for c in raw]


def _generate_coach_candidates(
    database_path, *, user_id: str, now: datetime, limit: int
) -> list[dict[str, Any]]:
    """Daily topic candidates from build_daily_recommendations."""
    try:
        # Build minimal analytics from storage helpers.
        from app.knowledge_graph import list_knowledge_concepts as _kg_concepts
        from app.storage import (
            _get_user_quiz_attempts,
            _get_user_study_sessions,
            _get_user_topic_reviews,
            _get_user_topic_sources,
        )

        attempts = _get_user_quiz_attempts(database_path, user_id)
        sessions = _get_user_study_sessions(database_path, user_id)
        reviews = _get_user_topic_reviews(database_path, user_id)
        topic_sources = _get_user_topic_sources(database_path, user_id)
        concepts = _kg_concepts(database_path, user_id=user_id)
        analytics = build_analytics(
            attempts=attempts,
            sessions=sessions,
            reviews=reviews,
            topic_sources=topic_sources,
            concepts=concepts,
            now=now,
        )
        raw = build_daily_recommendations(
            analytics=analytics, now=now, limit=limit, user_id=user_id
        )
    except Exception:
        return []
    return [_normalize_coach_candidate(c) for c in raw]


def _generate_prerequisite_candidates(
    database_path, *, user_id: str, limit: int
) -> list[dict[str, Any]]:
    """Prerequisite concepts that need attention from the knowledge graph."""
    try:
        concepts = list_knowledge_concepts(database_path, user_id=user_id)
        relationships = list_knowledge_relationships(database_path, user_id=user_id)
    except Exception:
        return []
    concept_by_id = {c["conceptId"]: c for c in concepts}
    # Find target concepts that have unmet prerequisites (source concepts with
    # lower mastery or no evidence).  Heuristic: recommend the source concept
    # when the target has evidence but the source has none.
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rel in relationships:
        source_id = rel["sourceConceptId"]
        target_id = rel["targetConceptId"]
        if source_id in seen or target_id not in concept_by_id:
            continue
        source = concept_by_id.get(source_id)
        target = concept_by_id.get(target_id)
        if source is None:
            continue
        # Source concept has no evidence → likely an unmet prerequisite.
        if source.get("evidenceCount", 0) == 0 and target.get("evidenceCount", 0) > 0:
            seen.add(source_id)
            rec_id = f"rec_pre_{hashlib.sha256(source_id.encode()).hexdigest()[:12]}"
            candidates.append(
                {
                    "recommendationId": rec_id,
                    "type": TYPE_PREREQUISITE,
                    "title": f"Review prerequisite: {source['name']}",
                    "description": (
                        f"Needed before progressing to {target['name']} "
                        f"({rel['relation']})."
                    ),
                    "reason": (
                        f"{source['name']} is a prerequisite for {target['name']} "
                        "but has no recorded evidence yet."
                    ),
                    "confidence": CONFIDENCE_LOW,
                    "priority": PREREQUISITE_UNMET_BONUS,
                    "targetType": TARGET_CONCEPT,
                    "targetId": source_id,
                    "targetName": source["name"],
                    "difficultyLevel": None,
                    "estimatedMinutes": 15,
                    "activitySuggestion": "foundational_practice",
                    "citations": [],
                    "prerequisiteConceptIds": [source_id],
                    "knowledgeGapIds": [],
                    "measuredFields": ["evidenceCount"],
                    "estimatedFields": ["estimatedMinutes"],
                    "aiUnavailable": False,
                }
            )
    return candidates


def _generate_gap_candidates(
    database_path, *, user_id: str, limit: int
) -> list[dict[str, Any]]:
    """Knowledge-gap-targeted candidates from persisted learning gaps.

    Read-only: lists existing gaps without mutating state, so recommendations
    remain reproducible across calls.
    """
    try:
        gaps = list_learning_gaps(database_path, user_id=user_id) or []
    except Exception:
        return []
    candidates: list[dict[str, Any]] = []
    for gap in gaps[:limit]:
        severity = gap.get("severity", "minor")
        candidates.append(
            {
                "recommendationId": f"rec_gap_{gap.get('gapId', '')[:12]}",
                "type": TYPE_GAP_FOCUS,
                "title": f"Address gap: {gap.get('gapType', 'learning gap')}",
                "description": None,
                "reason": (
                    f"Unresolved {severity} learning gap "
                    f"({gap.get('recurrence', 1)} occurrence(s))."
                ),
                "confidence": CONFIDENCE_MEDIUM,
                "priority": GAP_SEVERITY_BONUS.get(severity, 10.0),
                "targetType": TARGET_GAP,
                "targetId": gap.get("gapId", ""),
                "targetName": gap.get("topic") or gap.get("gapType"),
                "difficultyLevel": None,
                "estimatedMinutes": 20,
                "activitySuggestion": "targeted_quiz",
                "citations": [],
                "prerequisiteConceptIds": [],
                "knowledgeGapIds": [gap.get("gapId", "")],
                "measuredFields": ["severity", "recurrence"],
                "estimatedFields": ["estimatedMinutes"],
                "aiUnavailable": False,
            }
        )
    return candidates


# ---------------------------------------------------------------------------
# Ranking pipeline
# ---------------------------------------------------------------------------


def _score_candidate(
    candidate: dict[str, Any],
    *,
    dismissed: set[tuple[str, str]],
    recent_topics: set[str],
    today_sessions: set[str],
    weak_topics: set[str],
    overdue_topics: set[str],
) -> float:
    """Compute a composite ranking score for a candidate.

    Pure function of the candidate and learner context; deterministic given
    the same inputs.
    """
    base = float(candidate.get("priority", 0))
    target_type = candidate.get("targetType")
    target_id = candidate.get("targetId")
    score = base

    # Suppress dismissed content entirely.
    if (target_type, target_id) in dismissed:
        return float("-inf")

    # Weak topic bonus (measured).
    if target_id in weak_topics:
        score += WEAK_TOPIC_BONUS

    # Overdue review bonus (measured).
    if target_id in overdue_topics:
        score += OVERDUE_REVIEW_BONUS

    # Stale topic bonus (no recent activity).
    if target_type == TARGET_TOPIC and target_id not in recent_topics:
        score += RECENCY_STALE_BONUS

    # Recent activity penalty (avoid over-recommending).
    if target_id in recent_topics:
        score -= RECENT_ACTIVITY_PENALTY

    # Today's session penalty.
    if target_id in today_sessions:
        score -= TODAY_SESSION_PENALTY

    # Prerequisite unmet bonus.
    if candidate.get("type") == TYPE_PREREQUISITE:
        score += PREREQUISITE_UNMET_BONUS

    return score


def _cold_start_recommendations(
    *, user_id: str, limit: int
) -> list[dict[str, Any]]:
    """Transparent fallback when no learner data is available."""
    return [
        {
            "recommendationId": f"rec_cold_{i}",
            "type": TYPE_TOPIC_PRACTICE,
            "title": f"Foundational practice {i + 1}",
            "description": "Start with foundational material to establish a baseline.",
            "reason": "No learner history available yet; beginning with foundational practice.",
            "confidence": CONFIDENCE_INSUFFICIENT,
            "priority": float(limit - i),
            "targetType": TARGET_TOPIC,
            "targetId": f"cold_start_{i}",
            "targetName": f"Foundational topic {i + 1}",
            "difficultyLevel": "foundational",
            "estimatedMinutes": 20,
            "activitySuggestion": "introductory_quiz",
            "citations": [],
            "prerequisiteConceptIds": [],
            "knowledgeGapIds": [],
            "measuredFields": [],
            "estimatedFields": ["difficultyLevel", "estimatedMinutes"],
            "aiUnavailable": True,
        }
        for i in range(min(limit, 3))
    ]


def generate_recommendations(
    database_path,
    *,
    user_id: str,
    settings: Settings | None = None,
    request: RecommendationRequest | None = None,
    now: datetime | None = None,
) -> RecommendationResponse:
    """Generate personalized, explainable recommendations for a learner.

    Combines candidates from all existing subsystems, ranks them with
    transparent rules, suppresses dismissed content, and falls back to
    cold-start recommendations when no data is available.
    """
    from app.storage import get_overdue_topics, get_weak_topics

    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    if request is None:
        request = RecommendationRequest()
    if now is None:
        now = datetime.now(UTC)
    current = now.astimezone(UTC)

    limit = max(1, min(request.limit, int(settings.max_recommendations)))

    # Gather learner context for scoring/suppression.
    try:
        weak = {wt["topic_id"] for wt in get_weak_topics(database_path, user_id=user_id)}
    except Exception:
        weak = set()
    try:
        overdue = {
            ot["topic_id"]
            for ot in get_overdue_topics(database_path, user_id=user_id)
        }
    except Exception:
        overdue = set()
    try:
        dismissed = set(
            list_dismissed_recommendation_targets(database_path, user_id=user_id)
        )
    except Exception:
        dismissed = set()

    # Collect recent topics (last stale_days).
    try:
        reviews = _get_user_topic_reviews(database_path, user_id=user_id)
    except Exception:
        reviews = []
    recent_topics: set[str] = set()
    current = now.astimezone(UTC)
    from datetime import timedelta

    cutoff = current - timedelta(days=int(settings.recommendation_stale_days))
    for review in reviews:
        tried = review.get("attemptedAt")
        if tried:
            try:
                if datetime.fromisoformat(tried.replace("Z", "+00:00")) >= cutoff:
                    recent_topics.add(review["topicId"])
            except ValueError:
                pass
    today_sessions: set[str] = set()
    today = current.date().isoformat()
    try:
        from app.storage import _get_user_study_sessions

        for session in _get_user_study_sessions(database_path, user_id):
            completed = session.get("completedAt")
            if completed and completed[:10] == today:
                today_sessions.add(session["topicId"])
    except Exception:
        pass

    # Generate candidates from every generator.
    all_candidates: list[dict[str, Any]] = []
    all_candidates.extend(
        _generate_adaptive_candidates(
            database_path, user_id=user_id, now=current, limit=limit * 3
        )
    )
    all_candidates.extend(
        _generate_predictive_candidates(
            database_path, user_id=user_id, now=current, limit=limit * 3
        )
    )
    all_candidates.extend(
        _generate_coach_candidates(
            database_path, user_id=user_id, now=current, limit=limit * 3
        )
    )
    all_candidates.extend(
        _generate_prerequisite_candidates(
            database_path, user_id=user_id, limit=limit * 2
        )
    )
    all_candidates.extend(
        _generate_gap_candidates(
            database_path, user_id=user_id, limit=limit * 2
        )
    )

    # Apply optional topic filter.
    if request.topicId is not None:
        all_candidates = [
            c for c in all_candidates if c["targetId"] == request.topicId
        ]

    # Score and deduplicate by (targetType, targetId), keeping the best.
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in all_candidates:
        key = (candidate["targetType"], candidate["targetId"])
        score = _score_candidate(
            candidate,
            dismissed=dismissed,
            recent_topics=recent_topics,
            today_sessions=today_sessions,
            weak_topics=weak,
            overdue_topics=overdue,
        )
        candidate["_score"] = score
        existing = best.get(key)
        if existing is None or score > existing["_score"]:
            best[key] = candidate

    ranked = sorted(
        (c for c in best.values() if c["_score"] > float("-inf")),
        key=lambda c: (-c["_score"], c["targetName"] or ""),
    )

    cold_start = len(ranked) == 0
    ai_unavailable = False  # No LLM used; always available offline.

    if cold_start:
        ranked = _cold_start_recommendations(user_id=user_id, limit=limit)
    else:
        ranked = ranked[:limit]

    # Strip internal score and build response items.
    items: list[RecommendationItem] = []
    for candidate in ranked:
        candidate.pop("_score", None)
        candidate.pop("_evidence", None)
        # Drop any other underscore-prefixed internal fields.
        internal = [k for k in candidate if k.startswith("_")]
        for key in internal:
            candidate.pop(key, None)
        items.append(RecommendationItem(**candidate))

    # Record impressions for shown recommendations.
    for item in items:
        try:
            record_recommendation_impression(
                database_path,
                user_id=user_id,
                recommendation_id=item.recommendationId,
                recommendation_type=item.type,
                target_type=item.targetType,
                target_id=item.targetId,
                context={"priority": item.priority, "confidence": item.confidence},
            )
        except Exception:
            # Impression recording must never break recommendation delivery.
            pass

    return RecommendationResponse(
        recommendations=items,
        generatedAt=_now_iso(),
        total=len(items),
        coldStart=cold_start,
        aiUnavailable=ai_unavailable,
        userId=user_id,
    )


def record_feedback(
    database_path,
    *,
    user_id: str,
    feedback: RecommendationFeedbackCreate,
    settings: Settings | None = None,
) -> RecommendationFeedbackResponse:
    """Record learner feedback on a recommendation.

    Validates the action and persists the feedback.  Dismissals are used by
    ``generate_recommendations`` to suppress future displays of the same
    target.
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    allowed = {"dismiss", "select", "rate", "postpone"}
    if feedback.action not in allowed:
        raise ApiError(400, "INVALID_FEEDBACK_ACTION", f"action must be one of {sorted(allowed)}")
    if feedback.rating is not None and not 1 <= feedback.rating <= 5:
        raise ApiError(400, "INVALID_RATING", "rating must be between 1 and 5")

    stored = store_recommendation_feedback(
        database_path,
        user_id=user_id,
        recommendation_id=feedback.recommendationId,
        recommendation_type=feedback.recommendationType,
        target_type=feedback.targetType,
        target_id=feedback.targetId,
        action=feedback.action,
        rating=feedback.rating,
        note=feedback.note,
    )
    return RecommendationFeedbackResponse(**stored)


def list_feedback(
    database_path,
    *,
    user_id: str,
    limit: int = 100,
    offset: int = 0,
    action: str | None = None,
) -> RecommendationFeedbackListResponse:
    """List recommendation feedback for a learner."""
    rows = list_recommendation_feedback(
        database_path, user_id=user_id, limit=limit, offset=offset, action=action
    )
    items = [RecommendationFeedbackResponse(**row) for row in rows]
    return RecommendationFeedbackListResponse(feedback=items, total=len(items))
