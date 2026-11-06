# ruff: noqa: B008,E501
"""Unified learning journey and experience orchestration for M60.

Aggregates all persisted learner subsystems (quiz attempts, study sessions,
revision queue, study coach plan, learning paths, documents, images, knowledge
graph concepts, content resources) into a coherent journey view covering all
ten stages:

1. Onboarding & subject selection
2. Material upload & organization
3. Initial knowledge assessment
4. Personalized learning path creation
5. Topic learning through grounded explanations
6. Notes, flashcards, diagrams & quizzes
7. Knowledge gap identification
8. Adaptive difficulty adjustment
9. Scheduled revision & active recall
10. Progress tracking & mastery review

Every metric comes from persisted data or is explicitly labelled as an estimate.
All data is user-scoped; cross-user isolation is enforced by storage layer.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.learning_analytics import build_analytics
from app.storage import (
    get_learning_analytics_data,
    get_overdue_topics,
    get_revision_queue,
    get_revision_schedule,
    get_revision_stats,
    get_weak_topics,
    list_content_resources,
    list_documents,
    list_knowledge_concepts,
    list_learning_images,
    list_learning_paths,
)

# Ten journey stages in order.  Each stage has a unique id, human label, and
# description.  Stage completion is derived from persisted activity.
_JOURNEY_STAGES: list[dict[str, str]] = [
    {
        "stageId": "onboarding",
        "label": "Onboarding & subject selection",
        "description": "Create your account and choose the subjects you want to study.",
    },
    {
        "stageId": "material_upload",
        "label": "Learning material upload",
        "description": "Upload documents and images as source material for grounded study.",
    },
    {
        "stageId": "initial_assessment",
        "label": "Initial knowledge assessment",
        "description": "Take your first quiz to establish a baseline for adaptive recommendations.",
    },
    {
        "stageId": "learning_path",
        "label": "Personalized learning path",
        "description": "Create a learning path with objectives to structure your study.",
    },
    {
        "stageId": "topic_learning",
        "label": "Topic learning",
        "description": "Study topics through grounded AI explanations and source-backed answers.",
    },
    {
        "stageId": "study_materials",
        "label": "Notes, flashcards, diagrams & quizzes",
        "description": "Create and review study materials linked to your source evidence.",
    },
    {
        "stageId": "gap_identification",
        "label": "Knowledge gap identification",
        "description": "Identify weak topics and overdue reviews that need attention.",
    },
    {
        "stageId": "adaptive_difficulty",
        "label": "Adaptive difficulty adjustment",
        "description": "Receive difficulty-appropriate practice based on your demonstrated mastery.",
    },
    {
        "stageId": "scheduled_revision",
        "label": "Scheduled revision & active recall",
        "description": "Review due items using spaced repetition to reinforce retention.",
    },
    {
        "stageId": "progress_tracking",
        "label": "Progress tracking & mastery review",
        "description": "Monitor your mastery scores, quiz accuracy, and study activity over time.",
    },
]


def _stage_index(stage_id: str) -> int:
    for index, stage in enumerate(_JOURNEY_STAGES):
        if stage["stageId"] == stage_id:
            return index
    return -1


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _topic_id(topic: str) -> str:
    return hashlib.sha256(topic.casefold().encode("utf-8")).hexdigest()[:20]


def build_journey_dashboard(
    *,
    settings: Settings,
    database_path: str,
    user_id: str,
    now: datetime,
) -> dict[str, Any]:
    """Build the unified learner journey dashboard for ``user_id``."""
    current = now.astimezone(UTC)
    today = current.date().isoformat()
    db_path = Path(database_path)

    # --- Persisted data from each subsystem ---------------------------------
    data = get_learning_analytics_data(db_path, user_id=user_id)
    analytics = build_analytics(
        attempts=data["attempts"],
        sessions=data["sessions"],
        reviews=data["reviews"],
        topic_sources=data["topicSources"],
        concepts=data["concepts"],
        now=current,
    )

    try:
        coach_plan = get_or_create_study_coach_plan_internal(
            db_path, user_id, current, analytics, data
        )
    except Exception:
        coach_plan = {
            "planDate": today,
            "recommendations": [],
            "emptyReason": None,
        }

    revision_items, revision_total = get_revision_queue(
        db_path,
        user_id=user_id,
        limit=10,
        offset=0,
    )
    weak_topics = get_weak_topics(db_path, user_id=user_id)
    overdue_topics = get_overdue_topics(db_path, user_id=user_id)
    revision_stats = get_revision_stats(db_path, user_id=user_id)
    paths = list_learning_paths(db_path, user_id=user_id)
    documents = list_documents(db_path, user_id=user_id)
    images = list_learning_images(db_path, user_id=user_id)
    concepts = list_knowledge_concepts(db_path, user_id=user_id)
    content_resources = list_content_resources(db_path, user_id=user_id)

    # --- Journey stage completion ------------------------------------------
    stage_completion = _compute_stage_completion(
        analytics=analytics,
        documents=documents,
        images=images,
        paths=paths,
        concepts=concepts,
        content_resources=content_resources,
        revision_stats=revision_stats,
        weak_topics=weak_topics,
        now=current,
    )

    completed_stages = sum(1 for s in stage_completion if s["completed"])
    total_stages = len(stage_completion)
    completion_pct = round((completed_stages / total_stages) * 100) if total_stages else 0

    # Current stage = first incomplete stage, or the last stage if all complete.
    current_stage_index = 0
    for index, stage in enumerate(stage_completion):
        if not stage["completed"]:
            current_stage_index = index
            break
    else:
        current_stage_index = total_stages - 1
    current_stage = stage_completion[current_stage_index] if stage_completion else None

    # --- Dashboard widgets --------------------------------------------------
    weak_topic_list = [t for t in analytics["topics"] if t.get("weak")]
    upcoming_items, upcoming_total = get_revision_schedule(
        db_path, user_id=user_id, limit=7, offset=0
    )

    current_goals = _build_current_goals(
        analytics=analytics,
        coach_plan=coach_plan,
        weak_topics=weak_topic_list,
        overdue_topics=overdue_topics,
    )
    continue_learning = _build_continue_learning(
        analytics=analytics,
        sessions=data["sessions"],
        quizzes=data.get("quizzes", []),
    )
    todays_tasks = _build_todays_tasks(
        coach_plan=coach_plan,
        revision_items=revision_items,
        now=current,
    )
    due_revisions = _build_due_revisions(
        revision_items=revision_items,
        revision_stats=revision_stats,
    )
    recent_activity = _build_recent_activity(
        analytics=analytics,
        limit=10,
    )
    recommended_next_action = _build_recommended_next_action(
        stage_completion=stage_completion,
        current_stage_index=current_stage_index,
        analytics=analytics,
        coach_plan=coach_plan,
        revision_items=revision_items,
        weak_topics=weak_topic_list,
        now=current,
    )

    return {
        "generatedAt": _now_iso(),
        "userId": user_id,
        "journey": {
            "completionPercent": completion_pct,
            "completedStages": completed_stages,
            "totalStages": total_stages,
            "currentStage": current_stage,
            "stages": stage_completion,
        },
        "dashboard": {
            "currentGoals": current_goals,
            "continueLearning": continue_learning,
            "todaysTasks": todays_tasks,
            "dueRevisions": due_revisions,
            "weakTopics": weak_topics,
            "recentActivity": recent_activity,
            "upcomingAssessments": upcoming_items[:7],
            "learningPathProgress": _build_path_progress(
                paths=paths, analytics=analytics
            ),
            "recommendedNextAction": recommended_next_action,
        },
        "analytics": analytics,
        "studyCoachPlan": coach_plan,
        "revisionQueue": {
            "items": revision_items,
            "total": revision_total,
            "limit": 10,
            "offset": 0,
        },
        "learningPaths": paths,
        "documents": documents,
        "images": images,
        "knowledgeConcepts": concepts,
        "contentResources": content_resources,
    }


def get_or_create_study_coach_plan_internal(
    database_path: str,
    user_id: str,
    now: datetime,
    analytics: dict[str, Any],
    data: dict[str, Any],
) -> dict[str, Any]:
    """Get or create today's study coach plan, returning the stored result."""
    from app.storage import get_or_create_study_coach_plan

    database_path = Path(database_path)
    plan_date = now.date().isoformat()
    analytics["topicSources"] = data["topicSources"]
    candidates = build_daily_recommendations_internal(
        analytics=data | analytics,
        now=now,
        user_id=user_id,
    )
    stored = get_or_create_study_coach_plan(
        database_path,
        plan_date=plan_date,
        recommendations=candidates,
        updated_at=now.isoformat(),
        user_id=user_id,
    )
    live_topics = {topic["topicId"]: topic for topic in analytics["topics"]}
    recommendations = []
    for item in stored:
        topic = live_topics.get(item["topicId"])
        citations = data["topicSources"].get(item["topicId"], [])
        if topic is None or not citations:
            continue
        recommendations.append(
            {
                "recommendationId": item["recommendationId"],
                "planDate": item["planDate"],
                "topicId": item["topicId"],
                "topic": item["topic"],
                "title": item["title"],
                "priority": item["priority"],
                "reasons": item["reasons"],
                "graphConceptId": item["graphConceptId"],
                "relatedConceptIds": item["relatedConceptIds"],
                "status": item["status"],
                "estimatedMinutes": item["estimatedMinutes"],
                "updatedAt": item["updatedAt"],
                "citations": citations,
                "quizTopic": topic["topic"],
            }
        )
    return {
        "planDate": plan_date,
        "recommendations": recommendations,
        "emptyReason": (
            "No quiz attempts are recorded yet. Complete a cited quiz to get a personalized plan."
            if not data["attempts"]
            else "No current source-backed topics are available for a study recommendation."
        ),
    }


def build_daily_recommendations_internal(
    *,
    analytics: dict[str, Any],
    now: datetime,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Wrapper around study_coach.build_daily_recommendations."""
    from app.study_coach import build_daily_recommendations

    return build_daily_recommendations(
        analytics=analytics,
        now=now,
        limit=3,
        user_id=user_id,
    )


def _compute_stage_completion(
    *,
    analytics: dict[str, Any],
    documents: list[dict[str, Any]],
    images: list[dict[str, Any]],
    paths: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    content_resources: list[dict[str, Any]],
    revision_stats: dict[str, Any],
    weak_topics: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """Compute completion status for each journey stage from persisted data."""
    totals = analytics["totals"]
    today = now.date().isoformat()

    stages: list[dict[str, Any]] = []
    for stage in _JOURNEY_STAGES:
        stage_id = stage["stageId"]
        completed, evidence = _stage_completed(
            stage_id=stage_id,
            totals=totals,
            documents=documents,
            images=images,
            paths=paths,
            concepts=concepts,
            content_resources=content_resources,
            revision_stats=revision_stats,
            weak_topics=weak_topics,
            today=today,
        )
        stages.append(
            {
                **stage,
                "completed": completed,
                "evidence": evidence,
                "stageOrder": _stage_index(stage_id),
            }
        )
    return stages


def _stage_completed(
    *,
    stage_id: str,
    totals: dict[str, Any],
    documents: list[dict[str, Any]],
    images: list[dict[str, Any]],
    paths: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    content_resources: list[dict[str, Any]],
    revision_stats: dict[str, Any],
    weak_topics: list[dict[str, Any]],
    today: str,
) -> tuple[bool, dict[str, Any]]:
    """Determine whether a journey stage is complete from persisted data."""
    if stage_id == "onboarding":
        # Onboarding is complete once the user exists (auth) — always true here
        # since all dashboard requests require authentication.
        return True, {"userAuthenticated": True}

    if stage_id == "material_upload":
        has_material = len(documents) > 0 or len(images) > 0
        return has_material, {
            "documentCount": len(documents),
            "imageCount": len(images),
        }

    if stage_id == "initial_assessment":
        has_assessment = totals["attemptCount"] > 0
        return has_assessment, {
            "attemptCount": totals["attemptCount"],
            "attemptedQuestions": totals["attemptedQuestions"],
        }

    if stage_id == "learning_path":
        active_paths = [p for p in paths if p.get("status") == "active"]
        return len(active_paths) > 0, {"activePathCount": len(active_paths)}

    if stage_id == "topic_learning":
        has_sessions = totals["completedStudySessions"] > 0
        return has_sessions, {
            "completedStudySessions": totals["completedStudySessions"],
            "activeStudySessions": totals["activeStudySessions"],
        }

    if stage_id == "study_materials":
        has_materials = (
            len(content_resources) > 0
            or revision_stats.get("totalItems", 0) > 0
        )
        return has_materials, {
            "contentResourceCount": len(content_resources),
            "revisionItemCount": revision_stats.get("totalItems", 0),
        }

    if stage_id == "gap_identification":
        # Gap identification is "complete" once the system has identified
        # weak topics or overdue reviews (i.e. there is something to act on).
        # Having zero weak topics is also a valid completed state (no gaps).
        has_evaluated = totals["attemptCount"] > 0 or len(weak_topics) > 0
        return has_evaluated, {
            "weakTopicCount": len(weak_topics),
            "overdueReviewCount": totals["overdueReviews"],
            "assessed": has_evaluated,
        }

    if stage_id == "adaptive_difficulty":
        # Adaptive difficulty is available once there are quiz attempts.
        return totals["attemptCount"] > 0, {
            "attemptCount": totals["attemptCount"],
            "quizAccuracy": totals["quizAccuracy"],
        }

    if stage_id == "scheduled_revision":
        # Revision stage is complete once there are items in the schedule.
        total_items = revision_stats.get("totalItems", 0)
        return total_items > 0, {"totalRevisionItems": total_items}

    if stage_id == "progress_tracking":
        # Progress tracking is meaningful once there is some learner activity.
        return totals["attemptCount"] > 0, {
            "quizAccuracy": totals["quizAccuracy"],
        }

    return False, {}


def _build_current_goals(
    *,
    analytics: dict[str, Any],
    coach_plan: dict[str, Any],
    weak_topics: list[dict[str, Any]],
    overdue_topics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build current learning goals from analytics and coach plan."""
    goals: list[dict[str, Any]] = []
    totals = analytics["totals"]

    if totals["overdueReviews"] > 0:
        goals.append(
            {
                "goalId": "complete_overdue_reviews",
                "title": "Complete overdue reviews",
                "description": f"{totals['overdueReviews']} review item(s) are due or overdue.",
                "type": "revision",
                "priority": 100,
                "count": totals["overdueReviews"],
            }
        )

    for topic in weak_topics[:3]:
        goals.append(
            {
                "goalId": f"strengthen_{topic['topicId']}",
                "title": f"Strengthen: {topic['topic']}",
                "description": (
                    f"Mastery is below threshold ({topic.get('mastery', 0):.0%}). "
                    f"{topic.get('incorrectQuestions', 0)} incorrect question(s)."
                ),
                "type": "topic_mastery",
                "priority": 80,
                "topicId": topic["topicId"],
                "topic": topic["topic"],
                "mastery": topic.get("mastery"),
            }
        )

    for rec in coach_plan.get("recommendations", [])[:2]:
        goals.append(
            {
                "goalId": f"coach_{rec['recommendationId']}",
                "title": rec["title"],
                "description": "; ".join(rec.get("reasons", [])[:2]),
                "type": "study_coach",
                "priority": rec.get("priority", 50),
                "topicId": rec["topicId"],
                "topic": rec["topic"],
                "estimatedMinutes": rec.get("estimatedMinutes", 20),
            }
        )

    if not goals:
        goals.append(
            {
                "goalId": "start_first_quiz",
                "title": "Take your first quiz",
                "description": "Upload material and generate a quiz to begin your learning journey.",
                "type": "onboarding",
                "priority": 10,
            }
        )

    return goals


def _build_continue_learning(
    *,
    analytics: dict[str, Any],
    sessions: list[dict[str, Any]],
    quizzes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build 'continue learning' items from recent activity."""
    items: list[dict[str, Any]] = []

    # Most recent incomplete study session.
    for session in sessions:
        if session.get("completedAt") is None:
            items.append(
                {
                    "itemId": session["sessionId"],
                    "title": f"Continue: {session['topic']}",
                    "type": "study_session",
                    "topicId": session["topicId"],
                    "topic": session["topic"],
                    "startedAt": session["startedAt"],
                }
            )
            break

    # Most recent quiz with remaining questions.
    recent_topics = {}
    for attempt in analytics.get("recentActivity", []):
        if attempt["kind"] == "quiz_attempt":
            topic_id = attempt.get("topicId")
            topic = attempt.get("topic", "")
            if topic_id not in recent_topics:
                recent_topics[topic_id] = {
                    "itemId": topic_id,
                    "title": f"Review: {topic}",
                    "type": "quiz_topic",
                    "topicId": topic_id,
                    "topic": topic,
                    "lastOutcome": attempt.get("outcome"),
                    "occurredAt": attempt.get("occurredAt"),
                }
    items.extend(list(recent_topics.values())[:3])

    return items[:5]


def _build_todays_tasks(
    *,
    coach_plan: dict[str, Any],
    revision_items: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """Build today's study tasks from coach plan and revision queue."""
    tasks: list[dict[str, Any]] = []

    for rec in coach_plan.get("recommendations", []):
        tasks.append(
            {
                "taskId": f"coach_{rec['recommendationId']}",
                "title": rec["title"],
                "type": "study_coach",
                "topicId": rec["topicId"],
                "topic": rec["topic"],
                "priority": rec.get("priority", 50),
                "estimatedMinutes": rec.get("estimatedMinutes", 20),
                "status": rec.get("status", "planned"),
                "reasons": rec.get("reasons", []),
                "dueToday": True,
            }
        )

    for item in revision_items[:5]:
        tasks.append(
            {
                "taskId": f"revision_{item['itemId']}",
                "title": item.get("title", item.get("topic", "Review item")),
                "type": "revision",
                "topicId": item.get("topicId"),
                "topic": item.get("topic", ""),
                "priority": 90,
                "estimatedMinutes": 10,
                "status": "due",
                "dueAt": item.get("dueAt"),
                "lastResult": item.get("lastResult"),
            }
        )

    tasks.sort(key=lambda t: -t["priority"])
    return tasks[:8]


def _build_due_revisions(
    *,
    revision_items: list[dict[str, Any]],
    revision_stats: dict[str, Any],
) -> dict[str, Any]:
    """Build due revisions summary."""
    due_count = sum(
        1 for item in revision_items if item.get("lastResult") in {"forgotten", "partially"}
    )
    return {
        "totalDue": revision_stats.get("totalItems", 0),
        "dueCount": len(revision_items),
        "needsAttentionCount": due_count,
        "items": revision_items[:5],
    }


def _build_recent_activity(
    *,
    analytics: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """Build recent activity feed from analytics."""
    activity = []
    for row in analytics.get("recentActivity", [])[:limit]:
        activity.append(
            {
                "activityId": row["activityId"],
                "kind": row["kind"],
                "topic": row.get("topic", ""),
                "outcome": row.get("outcome"),
                "occurredAt": row.get("occurredAt"),
                "prompt": row.get("prompt"),
            }
        )
    return activity


def _build_path_progress(
    *,
    paths: list[dict[str, Any]],
    analytics: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build learning path progress summary."""
    progress = []
    for path in paths:
        progress.append(
            {
                "pathId": path["pathId"],
                "title": path["title"],
                "status": path["status"],
                "createdAt": path["createdAt"],
                "updatedAt": path["updatedAt"],
            }
        )
    return progress


def _build_recommended_next_action(
    *,
    stage_completion: list[dict[str, Any]],
    current_stage_index: int,
    analytics: dict[str, Any],
    coach_plan: dict[str, Any],
    revision_items: list[dict[str, Any]],
    weak_topics: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any] | None:
    """Determine the single recommended next action for the learner."""
    current = stage_completion[current_stage_index] if stage_completion else None
    if current is None:
        return None

    stage_id = current["stageId"]
    totals = analytics["totals"]

    # Priority 1: Overdue reviews
    if totals["overdueReviews"] > 0:
        return {
            "action": "start_revision",
            "title": "Complete overdue reviews",
            "description": f"{totals['overdueReviews']} review item(s) are due.",
            "stageId": "scheduled_revision",
            "priority": 100,
            "requiresConfirmation": False,
        }

    # Priority 2: Weak topics
    if weak_topics:
        topic = weak_topics[0]
        return {
            "action": "study_topic",
            "title": f"Strengthen: {topic['topic']}",
            "description": (
                f"Mastery below threshold ({topic.get('mastery', 0):.0%}). "
                "Focused practice recommended."
            ),
            "stageId": "gap_identification",
            "priority": 80,
            "topicId": topic["topicId"],
            "topic": topic["topic"],
            "requiresConfirmation": False,
        }

    # Priority 3: Current stage recommendation
    recommendations = coach_plan.get("recommendations", [])
    if recommendations:
        rec = recommendations[0]
        return {
            "action": "accept_recommendation",
            "title": rec["title"],
            "description": "; ".join(rec.get("reasons", [])[:2]),
            "stageId": current["stageId"],
            "priority": rec.get("priority", 50),
            "topicId": rec["topicId"],
            "topic": rec["topic"],
            "requiresConfirmation": True,
        }

    # Priority 4: Stage-based suggestion
    stage_suggestions = {
        "onboarding": ("start_quiz", "Take your first quiz", "Generate a quiz from your uploaded material."),
        "material_upload": ("generate_quiz", "Generate a quiz", "Create a quiz from your source material to assess your knowledge."),
        "initial_assessment": ("create_path", "Create a learning path", "Organize your study objectives into a structured path."),
        "learning_path": ("study_topic", "Study a topic", "Begin learning through grounded explanations from your source material."),
        "topic_learning": ("create_content", "Create study materials", "Generate notes, flashcards, or quizzes from your evidence."),
        "study_materials": ("review_due", "Review due items", "Complete your spaced-repetition review queue."),
        "gap_identification": ("targeted_quiz", "Take a targeted quiz", "Focus on weak topics identified by the system."),
        "adaptive_difficulty": ("adaptive_quiz", "Take an adaptive quiz", "Practice at your recommended difficulty level."),
        "scheduled_revision": ("complete_revision", "Complete revision", "Work through your scheduled revision items."),
        "progress_tracking": ("review_progress", "Review your progress", "Examine your mastery scores and learning analytics."),
    }
    suggestion = stage_suggestions.get(stage_id)
    if suggestion:
        action, title, description = suggestion
        return {
            "action": action,
            "title": title,
            "description": description,
            "stageId": stage_id,
            "priority": 50,
            "requiresConfirmation": False,
        }

    return None


def build_journey_overview(
    *,
    settings: Settings,
    database_path: str,
    user_id: str,
    now: datetime,
) -> dict[str, Any]:
    """Build a lightweight journey overview (no full subsystem details)."""
    db_path = Path(database_path)
    data = get_learning_analytics_data(db_path, user_id=user_id)
    analytics = build_analytics(
        attempts=data["attempts"],
        sessions=data["sessions"],
        reviews=data["reviews"],
        topic_sources=data["topicSources"],
        concepts=data["concepts"],
        now=now,
    )
    totals = analytics["totals"]
    weak_topics = [t for t in analytics["topics"] if t.get("weak")]
    revision_stats = get_revision_stats(db_path, user_id=user_id)
    paths = list_learning_paths(db_path, user_id=user_id)
    documents = list_documents(db_path, user_id=user_id)

    return {
        "generatedAt": now.isoformat(),
        "userId": user_id,
        "totals": {
            "attemptCount": totals["attemptCount"],
            "quizAccuracy": totals["quizAccuracy"],
            "completedStudySessions": totals["completedStudySessions"],
            "overdueReviews": totals["overdueReviews"],
            "activeStudySessions": totals["activeStudySessions"],
        },
        "weakTopicCount": len(weak_topics),
        "activePathCount": sum(1 for p in paths if p.get("status") == "active"),
        "documentCount": len(documents),
        "totalRevisionItems": revision_stats.get("totalItems", 0),
    }


def build_next_action(
    *,
    settings: Settings,
    database_path: str,
    user_id: str,
    now: datetime,
) -> dict[str, Any] | None:
    """Return the single recommended next action for the learner."""
    db_path = Path(database_path)
    data = get_learning_analytics_data(db_path, user_id=user_id)
    analytics = build_analytics(
        attempts=data["attempts"],
        sessions=data["sessions"],
        reviews=data["reviews"],
        topic_sources=data["topicSources"],
        concepts=data["concepts"],
        now=now,
    )
    coach_plan = get_or_create_study_coach_plan_internal(
        db_path, user_id, now, analytics, data
    )
    revision_items, _ = get_revision_queue(
        db_path, user_id=user_id, limit=10, offset=0
    )
    weak_topics = get_weak_topics(db_path, user_id=user_id)

    # Build minimal stage completion for next-action logic.
    stage_completion = _compute_stage_completion(
        analytics=analytics,
        documents=list_documents(db_path, user_id=user_id),
        images=list_learning_images(db_path, user_id=user_id),
        paths=list_learning_paths(db_path, user_id=user_id),
        concepts=list_knowledge_concepts(db_path, user_id=user_id),
        content_resources=list_content_resources(db_path, user_id=user_id),
        revision_stats=get_revision_stats(db_path, user_id=user_id),
        weak_topics=weak_topics,
        now=now,
    )
    current_index = 0
    for index, stage in enumerate(stage_completion):
        if not stage["completed"]:
            current_index = index
            break
    else:
        current_index = len(stage_completion) - 1

    return _build_recommended_next_action(
        stage_completion=stage_completion,
        current_stage_index=current_index,
        analytics=analytics,
        coach_plan=coach_plan,
        revision_items=revision_items,
        weak_topics=weak_topics,
        now=now,
    )
