"""Personal AI Learning Companion.

Unified conversational interface that helps users decide what to study
and take action using existing WonderLearn capabilities (tutor, quiz,
revision queue, learning analytics, study coach, learning paths).

All logic is deterministic and user-scoped; no paid APIs or external
model training is used.  Grounded answers reuse the existing RAG pipeline
(tutor.generate_grounded_answer + retrieval.retrieve_with_mode) and
abstain when evidence is insufficient.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.errors import ApiError
from app.learning_analytics import build_analytics
from app.quiz import generate_quiz_questions
from app.retrieval.service import answer_from_evidence, retrieve_with_mode
from app.schemas import CompanionAction
from app.storage import (
    add_message,
    compute_adaptive_recommendations,
    create_conversation,
    create_quiz_set,
    get_conversation,
    get_learning_analytics_data,
    get_revision_queue,
    list_learning_paths,
)
from app.tutor import TutorUnavailable, generate_grounded_answer

# ---------------------------------------------------------------------------
# Intent detection (deterministic, keyword-based)
# ---------------------------------------------------------------------------

_QUIZ_KEYWORDS = frozenset(
    {
        "quiz",
        "test me",
        "questions",
        "trivia",
        "assess",
        "ask me",
        "practice questions",
    }
)
_REVISION_KEYWORDS = frozenset(
    {"review", "revision", "flashcard", "spaced", "due", "overdue", "recall"}
)
_PROGRESS_KEYWORDS = frozenset(
    {
        "progress",
        "summary",
        "how am i",
        "how am i doing",
        "stats",
        "statistics",
        "performance",
    }
)
_RECOMMEND_KEYWORDS = frozenset(
    {
        "recommend",
        "what should",
        "next study",
        "what next",
        "study plan",
        "what to study",
        "focus on",
    }
)
_PLAN_KEYWORDS = frozenset(
    {"learning path", "study path", "objective", "curriculum", "plan my"}
)


def detect_intent(message: str) -> str:
    """Deterministically classify the user's message into a companion intent.

    Falls back to ``"ask"`` (grounded Q&A) when no keyword matches.  Keyword
    matching is case-insensitive and substring-based so short utterances still
    route correctly.
    """
    msg = message.casefold()
    for kw in _QUIZ_KEYWORDS:
        if kw in msg:
            return "quiz"
    for kw in _REVISION_KEYWORDS:
        if kw in msg:
            return "revision"
    for kw in _PROGRESS_KEYWORDS:
        if kw in msg:
            return "progress"
    for kw in _RECOMMEND_KEYWORDS:
        if kw in msg:
            return "recommend"
    for kw in _PLAN_KEYWORDS:
        if kw in msg:
            return "manage"
    return "ask"


# ---------------------------------------------------------------------------
# Intent handlers
# ---------------------------------------------------------------------------


async def _handle_ask(
    settings: Settings,
    database_path: str,
    user_id: str,
    message: str,
    document_ids: list[str] | None,
    limit: int,
    embeddings,
) -> dict[str, Any]:
    """Answer a question using grounded retrieval + tutor.

    Retrieves evidence via the existing RAG pipeline, then generates a
    grounded answer.  Abstains when evidence is insufficient or the tutor
    is unavailable.
    """
    search = retrieve_with_mode(
        database_path,
        query=message,
        document_ids=document_ids,
        limit=limit,
        mode="hybrid",
        settings=settings,
        embeddings=embeddings,
        user_id=user_id,
    )
    evidence = search["results"]
    if not evidence:
        return {
            "status": "insufficient_evidence",
            "content": "I don't have any indexed source material that covers "
            "that question yet. Upload a document or create some study "
            "content first, then I can answer from it.",
            "citations": [],
            "action": CompanionAction(
                action="none",
                description="No actionable follow-up.",
                payload={},
                requires_confirmation=False,
            ).model_dump(),
            "retrieval_mode": search["retrievalMode"],
            "fallback_reason": search.get("fallbackReason"),
        }
    try:
        generated = await asyncio.to_thread(
            generate_grounded_answer,
            settings,
            question=message,
            concept=None,
            evidence=evidence,
        )
    except TutorUnavailable:
        # Fallback to extractive evidence-only answer so the user still gets
        # sourced material even when the model is unavailable.
        extractive = answer_from_evidence(message, evidence)
        return {
            "status": extractive["status"],
            "content": extractive["answer"],
            "citations": extractive["citations"],
            "action": CompanionAction(
                action="none",
                description="Tutor unavailable; showing retrieved passages only.",
                payload={},
                requires_confirmation=False,
            ).model_dump(),
            "retrieval_mode": search["retrievalMode"],
            "fallback_reason": "tutor_unavailable",
        }
    except Exception:  # invalid tutor response -> treat as abstention
        return {
            "status": "insufficient_evidence",
            "content": "I couldn't form a grounded answer from the available "
            "source material. Try rephrasing or uploading more relevant "
            "content.",
            "citations": [],
            "action": CompanionAction(
                action="none",
                description="No grounded answer available.",
                payload={},
                requires_confirmation=False,
            ).model_dump(),
            "retrieval_mode": search["retrievalMode"],
            "fallback_reason": "invalid_tutor_response",
        }
    citations = [
        {
            "citationId": c["citationId"],
            "documentId": c["documentId"],
            "displayFilename": c["displayFilename"],
            "pageNumber": c["pageNumber"],
            "chunkIndex": c["chunkIndex"],
            "charStart": c["charStart"],
            "charEnd": c["charEnd"],
            "text": c["text"],
            "retrieval": c["retrieval"],
        }
        for c in generated["citations"]
    ]
    return {
        "status": "answered" if generated["answer"] else "insufficient_evidence",
        "content": generated["answer"],
        "citations": citations,
        "action": CompanionAction(
            action="start_quiz",
            description="Turn this into a quiz to test your understanding.",
            payload={"topic": message[:160], "question_count": 5},
            requires_confirmation=False,
        ).model_dump(),
        "retrieval_mode": search["retrievalMode"],
        "fallback_reason": search.get("fallbackReason"),
    }


async def _handle_quiz(
    settings: Settings,
    database_path: str,
    user_id: str,
    message: str,
    document_ids: list[str] | None,
    limit: int,
    embeddings,
) -> dict[str, Any]:
    """Start a quiz from retrieved evidence."""
    topic = " ".join(message.split())
    topic_id = hashlib.sha256(topic.casefold().encode("utf-8")).hexdigest()[:20]
    search = retrieve_with_mode(
        database_path,
        query=topic,
        document_ids=document_ids,
        limit=limit,
        mode="hybrid",
        settings=settings,
        embeddings=embeddings,
        user_id=user_id,
    )
    evidence = search["results"]
    if not evidence:
        return {
            "status": "insufficient_evidence",
            "content": f"I couldn't find enough source material to build a "
            f"quiz on \"{topic}\". Try a different topic or upload content "
            f"first.",
            "citations": [],
            "action": CompanionAction(
                action="none",
                description="No quiz can be generated without evidence.",
                payload={},
                requires_confirmation=False,
            ).model_dump(),
            "retrieval_mode": search["retrievalMode"],
            "fallback_reason": search.get("fallbackReason"),
        }
    try:
        generated = await asyncio.to_thread(
            generate_quiz_questions,
            settings,
            topic=topic,
            count=min(limit, 5),
            evidence=evidence,
        )
    except TutorUnavailable:
        return {
            "status": "model_unavailable",
            "content": "The local tutor is not configured, so I can't "
            "generate quiz questions right now.",
            "citations": [],
            "action": CompanionAction(
                action="none",
                description="Tutor unavailable for quiz generation.",
                payload={},
                requires_confirmation=False,
            ).model_dump(),
            "retrieval_mode": search["retrievalMode"],
            "fallback_reason": "tutor_unavailable",
        }
    if not generated:
        return {
            "status": "insufficient_evidence",
            "content": "The evidence doesn't support quiz questions on that "
            "topic yet.",
            "citations": [],
            "action": CompanionAction(
                action="none",
                description="No quiz questions generated.",
                payload={},
                requires_confirmation=False,
            ).model_dump(),
            "retrieval_mode": search["retrievalMode"],
            "fallback_reason": search.get("fallbackReason"),
        }
    quiz_id = hashlib.sha256(
        f"{user_id}:{topic}:{datetime.now(UTC).isoformat()}".encode()
    ).hexdigest()[:24]
    created_at = datetime.now(UTC).isoformat()
    try:
        create_quiz_set(
            database_path,
            {
                "quiz_id": quiz_id,
                "topic_id": topic_id,
                "topic": topic,
                "created_at": created_at,
                "questions": [
                    {
                        "question_id": item["questionId"],
                        "question_type": item["questionType"],
                        "prompt": item["prompt"],
                        "options": item["options"],
                        "answer_key": item["answerKey"],
                        "accepted_answers": item["acceptedAnswers"],
                        "explanation": item["explanation"],
                        "citations": item["citations"],
                    }
                    for item in generated
                ],
            },
            user_id=user_id,
        )
    except Exception:
        return {
            "status": "error",
            "content": "I couldn't save the quiz. Please try again.",
            "citations": [],
            "action": CompanionAction(
                action="none",
                description="Quiz save failed.",
                payload={},
                requires_confirmation=False,
            ).model_dump(),
            "retrieval_mode": search["retrievalMode"],
            "fallback_reason": "storage_error",
        }
    return {
        "status": "ready",
        "content": f"I've created a {len(generated)}-question quiz on "
        f"\"{topic}\". Ready when you are!",
        "citations": [],
        "action": CompanionAction(
            action="start_quiz",
            description="Start the generated quiz.",
            payload={"quiz_id": quiz_id, "topic": topic},
            requires_confirmation=True,
        ).model_dump(),
        "retrieval_mode": search["retrievalMode"],
        "fallback_reason": search.get("fallbackReason"),
    }


def _handle_revision(
    database_path: str,
    user_id: str,
    limit: int,
) -> dict[str, Any]:
    """Return the due/overdue revision queue."""
    items, total = get_revision_queue(
        database_path,
        user_id=user_id,
        limit=limit,
        offset=0,
    )
    if not items:
        return {
            "status": "answered",
            "content": "Your revision queue is empty. Keep studying to build "
            "your spaced-repetition review list.",
            "citations": [],
            "action": CompanionAction(
                action="none",
                description="No items due for review.",
                payload={},
                requires_confirmation=False,
            ).model_dump(),
            "retrieval_mode": None,
            "fallback_reason": None,
        }
    lines = [f"{total} item(s) due for review:"]
    for item in items[:limit]:
        marker = "📝" if item["itemType"] == "flashcard" else "❓"
        lines.append(
            f"{marker} {item['title']} — due {item['dueAt'][:10]} "
            f"(last: {item['lastResult']})"
        )
    return {
        "status": "answered",
        "content": "\n".join(lines),
        "citations": [],
        "action": CompanionAction(
            action="start_revision",
            description="Open the revision queue to review due items.",
            payload={"limit": limit},
            requires_confirmation=False,
        ).model_dump(),
        "retrieval_mode": None,
        "fallback_reason": None,
    }


def _handle_progress(
    database_path: str,
    user_id: str,
) -> dict[str, Any]:
    """Summarize learning progress from persisted activity."""
    data = get_learning_analytics_data(database_path, user_id=user_id)
    now = datetime.now(UTC)
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
    overdue = totals["overdue_reviews"]
    parts = []
    parts.append(f"Total attempts: {totals['attemptCount']}")
    if totals["quiz_accuracy"] is not None:
        parts.append(f"Quiz accuracy: {totals['quiz_accuracy']:.0%}")
    else:
        parts.append("Quiz accuracy: not scored yet")
    parts.append(f"Completed study sessions: {totals['completedStudySessions']}")
    parts.append(f"Overdue reviews: {overdue}")
    if weak_topics:
        parts.append(
            "Weak topics: " + ", ".join(t["topic"] for t in weak_topics[:5])
        )
    else:
        parts.append("No weak topics detected.")
    return {
        "status": "answered",
        "content": " | ".join(parts),
        "citations": [],
        "action": CompanionAction(
            action="recommend",
            description="Get personalized next-step recommendations.",
            payload={},
            requires_confirmation=False,
        ).model_dump(),
        "retrieval_mode": None,
        "fallback_reason": None,
    }


def _handle_recommend(
    database_path: str,
    user_id: str,
    now: datetime,
    limit: int,
) -> dict[str, Any]:
    """Return adaptive study recommendations."""
    data = get_learning_analytics_data(database_path, user_id=user_id)
    build_analytics(
        attempts=data["attempts"],
        sessions=data["sessions"],
        reviews=data["reviews"],
        topic_sources=data["topicSources"],
        concepts=data["concepts"],
        now=now,
    )
    recommendations = compute_adaptive_recommendations(
        database_path,
        user_id=user_id,
        now=now,
        limit=limit,
    )
    if not recommendations:
        return {
            "status": "answered",
            "content": "No recommendations yet. Complete a few quiz attempts "
            "so I can build a personalized plan for you.",
            "citations": [],
            "action": CompanionAction(
                action="none",
                description="No recommendations available yet.",
                payload={},
                requires_confirmation=False,
            ).model_dump(),
            "retrieval_mode": None,
            "fallback_reason": None,
        }
    lines = ["Here's what I recommend studying next:"]
    for rec in recommendations:
        lines.append(
            f"• {rec['topic']} ({rec['difficultyLevel']}) — "
            f"{rec['reasons'][0] if rec['reasons'] else 'Practice'}"
        )
    return {
        "status": "answered",
        "content": "\n".join(lines),
        "citations": [],
        "action": CompanionAction(
            action="accept_recommendation",
            description="Accept the top recommendation and start studying.",
            payload={
                "topic_id": recommendations[0]["topicId"],
                "topic": recommendations[0]["topic"],
            },
            requires_confirmation=True,
        ).model_dump(),
        "retrieval_mode": None,
        "fallback_reason": None,
    }


def _handle_manage(
    database_path: str,
    user_id: str,
) -> dict[str, Any]:
    """List the user's learning paths."""
    paths = list_learning_paths(database_path, user_id=user_id)
    if not paths:
        return {
            "status": "answered",
            "content": "You don't have any learning paths yet. Create one to "
            "organize your study objectives.",
            "citations": [],
            "action": CompanionAction(
                action="create_path",
                description="Create a new learning path.",
                payload={},
                requires_confirmation=False,
            ).model_dump(),
            "retrieval_mode": None,
            "fallback_reason": None,
        }
    lines = [f"{len(paths)} learning path(s):"]
    for p in paths:
        lines.append(f"• {p['title']} — {p['status']}")
    return {
        "status": "answered",
        "content": "\n".join(lines),
        "citations": [],
        "action": CompanionAction(
            action="create_path",
            description="Create another learning path or add objectives.",
            payload={},
            requires_confirmation=False,
        ).model_dump(),
        "retrieval_mode": None,
        "fallback_reason": None,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_companion(
    settings: Settings,
    database_path: str,
    user_id: str,
    *,
    message: str,
    conversation_id: str | None,
    mode: str | None,
    document_ids: list[str] | None,
    limit: int,
    embeddings,
) -> dict[str, Any]:
    """Process one companion turn: detect intent, route, persist, respond."""
    now = datetime.now(UTC)

    # Resolve conversation (create if needed).
    if conversation_id is None:
        conv = create_conversation(database_path, user_id=user_id, title=message[:60])
        conversation_id = conv["conversationId"]
    else:
        conv = get_conversation(database_path, conversation_id=conversation_id, user_id=user_id)
        if conv is None:
            raise ApiError(404, "CONVERSATION_NOT_FOUND", "The conversation was not found.")

    # Detect intent.
    intent = mode if mode in {
        "ask", "quiz", "revision", "progress", "recommend", "manage"
    } else detect_intent(message)

    # Route to handler.
    if intent == "ask":
        result = await _handle_ask(
            settings,
            database_path,
            user_id,
            message,
            document_ids,
            limit,
            embeddings,
        )
    elif intent == "quiz":
        result = await _handle_quiz(
            settings,
            database_path,
            user_id,
            message,
            document_ids,
            limit,
            embeddings,
        )
    elif intent == "revision":
        result = _handle_revision(database_path, user_id, limit)
    elif intent == "progress":
        result = _handle_progress(database_path, user_id)
    elif intent == "recommend":
        result = _handle_recommend(database_path, user_id, now, limit)
    else:  # manage
        result = _handle_manage(database_path, user_id)

    # Persist user message.
    await asyncio.to_thread(
        add_message,
        database_path,
        conversation_id=conversation_id,
        role="user",
        content=message,
        user_id=user_id,
    )

    # Persist assistant response.
    citations = result.get("citations", [])
    assistant_msg = await asyncio.to_thread(
        add_message,
        database_path,
        conversation_id=conversation_id,
        role="assistant",
        content=result["content"],
        citations=citations,
        tool_calls=result.get("action", {}).get("payload", {}),
        status=result["status"],
        user_id=user_id,
    )

    return {
        "message_id": assistant_msg["messageId"],
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": result["content"],
        "status": result["status"],
        "citations": citations,
        "retrieval_mode": result.get("retrieval_mode"),
        "fallback_reason": result.get("fallback_reason"),
        "action": result.get("action"),
        "requires_confirmation": result.get("action", {}).get("requires_confirmation", False),
        "created_at": assistant_msg["createdAt"],
    }
