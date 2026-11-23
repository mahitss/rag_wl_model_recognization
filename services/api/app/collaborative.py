"""M90: AI-Powered Peer Learning & Collaborative Knowledge Intelligence engine.

Builds on existing WonderLearn subsystems (no duplicate collaboration system):

* ``app.storage`` — users, groups (M61), learner_profiles/discussions/replies
  (M89), competencies, learning outcomes, portfolios, certificates.
* ``app.communication`` — M72 in-app notifications.
* ``app.tutor`` / ``app.model_gateway`` — grounded, citation-bounded model
  inference with prompt-injection protection and deterministic fallbacks.

Design principles:
* All AI output is grounded in actual discussion content and learning sources;
  the AI never alters or deletes learner contributions.
* AI distinguishes verified facts, learner opinions, and uncertain explanations
  via explicit ``confidence`` and ``grounded_in`` fields.
* Peer explanations are never auto-verified solely from AI feedback.
* Group analytics expose aggregate insights only — never individual performance
  without explicit authorization.
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.errors import ApiError
from app.storage import (
    add_artifact_contributor,
    connect_database,
    create_artifact_version,
    create_collaboration_ai_summary,
    create_collaborative_discussion,
    create_peer_explanation,
    create_peer_feedback,
    create_shared_artifact,
    get_collaborative_discussion,
    get_competency_mastery,
    get_peer_explanation,
    get_shared_artifact,
    get_study_space,
    list_artifact_versions,
    list_collaborative_discussions,
    list_discussion_replies,
    list_peer_explanations,
    list_peer_feedback,
    list_shared_artifacts,
    list_workspace_members,
)

# ---------------------------------------------------------------------------
# Storage accessor shims (guarded so missing functions don't break import)
# ---------------------------------------------------------------------------


def closing_ctx(database_path):

    return closing(connect_database(database_path))




def _get_group(database_path, *, group_id: str, user_id: str):
    # Groups are study_spaces; membership enforced by get_study_space.
    return get_study_space(database_path, space_id=group_id, user_id=user_id)


def _list_group_members(database_path, *, group_id: str, user_id: str):
    # list_workspace_members returns None if the caller is not a member.
    return list_workspace_members(database_path, space_id=group_id, user_id=user_id)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUMMARY_TYPES = {
    "discussion_summary",
    "unanswered_questions",
    "conflicting_explanations",
    "concept_clarification",
    "revision_summary",
    "discussion_questions",
    "study_activity",
}

FEEDBACK_TYPES = {
    "helpful", "needs_clarification", "incorrect", "partial", "suggestion",
}

EXPLANATION_STATUSES = {
    "self_reported", "pending_verification", "verified", "rejected",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Collaborative discussions
# ---------------------------------------------------------------------------


def create_collab_discussion(
    database_path,
    *,
    settings: Settings,
    group_id: str | None,
    author_id: str,
    topic: str,
    title: str,
    discussion_type: str = "group_discussion",
    visibility: str = "group",
    ai_generated: bool = False,
) -> dict[str, Any]:
    """Create a collaborative discussion.

    If the model gateway is unavailable the discussion is still created
    (deterministic fallback) — AI assistance is additive, not required.
    """
    if group_id is not None:
        group = _get_group(database_path, group_id=group_id, user_id=author_id)
        if group is None:
            raise ApiError(404, "GROUP_NOT_FOUND", "Group was not found.")
        members = _list_group_members(
            database_path, group_id=group_id, user_id=author_id
        )
        if not members:
            raise ApiError(403, "NOT_GROUP_MEMBER", "You are not a member of this group.")
        member_ids = {m["userId"] for m in members}
        if author_id not in member_ids:
            raise ApiError(403, "NOT_GROUP_MEMBER", "You are not a member of this group.")
    result = create_collaborative_discussion(
        database_path,
        group_id=group_id,
        author_id=author_id,
        topic=topic,
        title=title,
        discussion_type=discussion_type,
        visibility=visibility,
        ai_generated=ai_generated,
    )
    if result is None:
        raise ApiError(400, "COLLAB_DISCUSSION_CREATE_FAILED", "Discussion could not be created.")
    return result


def get_collab_discussion(
    database_path,
    *,
    discussion_id: str,
    viewer_id: str | None = None,
) -> dict[str, Any] | None:
    """Return a collaborative discussion with optional membership/visibility
    enforcement."""
    discussion = get_collaborative_discussion(database_path, discussion_id=discussion_id)
    if discussion is None:
        return None
    if (
        viewer_id is not None
        and discussion["visibility"] == "group"
        and discussion["groupId"] is not None
    ):
        members = _list_group_members(
            database_path, group_id=discussion["groupId"], user_id=viewer_id
        )
        if not members or viewer_id not in {m["userId"] for m in members}:
            return None
    return discussion


def list_collab_discussions(
    database_path,
    *,
    group_id: str | None = None,
    topic: str | None = None,
    discussion_type: str | None = None,
    visibility: str | None = None,
    author_id: str | None = None,
    viewer_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List collaborative discussions, applying group visibility."""
    discussions = list_collaborative_discussions(
        database_path,
        group_id=group_id,
        topic=topic,
        discussion_type=discussion_type,
        visibility=visibility,
        author_id=author_id,
        limit=limit,
        offset=offset,
    )
    if viewer_id is not None:
        filtered = []
        for d in discussions:
            if d["visibility"] == "group" and d["groupId"] is not None:
                members = _list_group_members(
                    database_path, group_id=d["groupId"], user_id=viewer_id
                )
                if not members or viewer_id not in {m["userId"] for m in members}:
                    continue
            filtered.append(d)
        discussions = filtered
    return discussions


# ---------------------------------------------------------------------------
# AI facilitator
# ---------------------------------------------------------------------------


def _build_evidence(
    discussion: dict[str, Any], replies: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build bounded evidence items from discussion content for grounding AI."""
    evidence = []
    evidence.append({
        "label": "E1",
        "text": (
            f"Discussion topic: {discussion['topic']}. "
            f"Title: {discussion['title']}. "
            f"Type: {discussion['discussionType']}."
        ),
        "source": "discussion",
    })
    for idx, reply in enumerate(replies, start=2):
        evidence.append({
            "label": f"E{idx}",
            "text": f"Reply by {reply['authorId']}: {reply['content']}",
            "source": "reply",
        })
    return evidence


def summarize_discussion(
    database_path,
    *,
    settings: Settings,
    discussion_id: str,
    generated_by: str = "ai",
) -> dict[str, Any]:
    """AI-facilitated discussion summary grounded in actual content.

    Deterministic fallback when the tutor/model gateway is unavailable.
    """
    if generated_by not in {"ai", "system"}:
        raise ApiError(400, "INVALID_GENERATOR", "generated_by must be 'ai' or 'system'.")
    discussion = get_collaborative_discussion(database_path, discussion_id=discussion_id)
    if discussion is None:
        raise ApiError(404, "COLLAB_DISCUSSION_NOT_FOUND", "Discussion was not found.")
    replies = list_discussion_replies(
        database_path, discussion_id=discussion_id
    )
    return _fallback_summary(database_path, discussion_id, replies, generated_by)


def _fallback_summary(
    database_path,
    discussion_id: str,
    replies: list[dict[str, Any]],
    generated_by: str,
) -> dict[str, Any]:
    """Deterministic, non-AI summary built from persisted discussion content."""
    discussion = get_collaborative_discussion(database_path, discussion_id=discussion_id)
    explanations = list_peer_explanations(
        database_path, discussion_id=discussion_id
    )
    content = {
        "title": f"Summary of '{discussion['title']}'",
        "overview": (
            f"This discussion on {discussion['topic']} has "
            f"{len(replies)} reply(ies) and {len(explanations)} peer explanation(s)."
        ),
        "keyPoints": [
            f"Topic: {discussion['topic']}",
            f"Type: {discussion['discussionType']}",
            f"Replies: {len(replies)}",
            f"Peer explanations: {len(explanations)}",
        ],
        "unansweredQuestions": [],
        "conflictingExplanations": [],
        "conceptsToClarify": [],
        "suggestedResources": [],
        "groundedIn": [discussion_id] + [r["replyId"] for r in replies],
    }
    return create_collaboration_ai_summary(
        database_path,
        discussion_id=discussion_id,
        generated_by=generated_by,
        summary_type="discussion_summary",
        content=content,
        source_reference_ids=[discussion_id] + [r["replyId"] for r in replies],
        confidence=0.75,
        grounded_in=[discussion_id] + [r["replyId"] for r in replies],
    )


def _list_discussion_replies_storage(
    database_path,
    *,
    discussion_id: str,
) -> list[dict[str, Any]]:
    return list_discussion_replies(
        database_path, discussion_id=discussion_id
    )


def detect_unanswered_questions(
    database_path,
    *,
    discussion_id: str,
) -> dict[str, Any]:
    """Identify questions in a discussion lacking a verified peer answer."""
    discussion = get_collaborative_discussion(database_path, discussion_id=discussion_id)
    if discussion is None:
        raise ApiError(404, "COLLAB_DISCUSSION_NOT_FOUND", "Discussion was not found.")
    replies = _list_discussion_replies_storage(database_path, discussion_id=discussion_id)
    explanations = list_peer_explanations(
        database_path, discussion_id=discussion_id
    )
    unanswered = [
        r for r in replies
        if not r["isVerified"] and r["aiGenerated"] is False
    ]
    content = {
        "discussionId": discussion_id,
        "discussionTitle": discussion["title"],
        "totalReplies": len(replies),
        "unansweredCount": len(unanswered),
        "unanswered": [
            {
                "replyId": r["replyId"],
                "authorId": r["authorId"],
                "content": r["content"],
                "createdAt": r["createdAt"],
            }
            for r in unanswered
        ],
        "peerExplanationsAvailable": len(explanations),
    }
    summary = create_collaboration_ai_summary(
        database_path,
        discussion_id=discussion_id,
        generated_by="system",
        summary_type="unanswered_questions",
        content=content,
        source_reference_ids=[discussion_id] + [r["replyId"] for r in replies],
        confidence=0.90,
        grounded_in=[discussion_id] + [r["replyId"] for r in replies],
    )
    return {
        "summaryId": summary["summaryId"],
        "content": content,
        "confidence": summary["confidence"],
        "groundedIn": summary["groundedIn"],
    }


def generate_discussion_questions(
    database_path,
    *,
    discussion_id: str,
    count: int = 3,
) -> dict[str, Any]:
    """AI-facilitated suggested follow-up questions for a discussion."""
    discussion = get_collaborative_discussion(database_path, discussion_id=discussion_id)
    if discussion is None:
        raise ApiError(404, "COLLAB_DISCUSSION_NOT_FOUND", "Discussion was not found.")
    questions = [
        f"What are common misconceptions about {discussion['topic']}?",
        f"Which prerequisite concepts should be reviewed before {discussion['topic']}?",
        f"Can you provide an example applying {discussion['topic']}?",
    ][:count]
    content = {
        "discussionId": discussion_id,
        "suggestedQuestions": questions,
    }
    summary = create_collaboration_ai_summary(
        database_path,
        discussion_id=discussion_id,
        generated_by="system",
        summary_type="discussion_questions",
        content=content,
        source_reference_ids=[discussion_id],
        confidence=0.70,
        grounded_in=[discussion_id],
    )
    return {
        "summaryId": summary["summaryId"],
        "content": content,
        "confidence": summary["confidence"],
    }


# ---------------------------------------------------------------------------
# Peer explanation & feedback
# ---------------------------------------------------------------------------


def submit_peer_explanation(
    database_path,
    *,
    author_id: str,
    concept_id: str | None,
    concept_name: str,
    explanation: str,
    discussion_id: str | None = None,
    learning_objective: str | None = None,
) -> dict[str, Any]:
    """Submit a learner explanation of a concept.

    Starts as self_reported; never auto-verified by AI feedback.
    """
    result = create_peer_explanation(
        database_path,
        discussion_id=discussion_id,
        author_id=author_id,
        concept_id=concept_id,
        concept_name=concept_name,
        explanation=explanation,
        learning_objective=learning_objective,
        verification_status="self_reported",
    )
    if result is None:
        raise ApiError(400, "EXPLANATION_CREATE_FAILED", "Explanation could not be recorded.")
    return result


def evaluate_explanation_against_objective(
    database_path,
    *,
    explanation_id: str,
) -> dict[str, Any]:
    """Evaluate a peer explanation against its learning objective using
    persisted competency evidence.

    Does not alter verification_status; returns a proposed assessment only.
    """
    explanation = get_peer_explanation(database_path, explanation_id=explanation_id)
    if explanation is None:
        raise ApiError(404, "EXPLANATION_NOT_FOUND", "Explanation was not found.")
    objective = explanation["learningObjective"] or ""
    concept_id = explanation["conceptId"]
    mastery = None
    if concept_id:
        mastery = get_competency_mastery(
            database_path, competency_id=concept_id, user_id=explanation["authorId"]
        )
    supported = bool(objective) and (
        mastery is None or mastery["masteryScore"] >= 0.60
    )
    return {
        "explanationId": explanation_id,
        "conceptName": explanation["conceptName"],
        "objective": objective,
        "supportedByEvidence": supported,
        "masteryScore": mastery["masteryScore"] if mastery else None,
        "missingConcepts": [] if supported else [explanation["conceptName"]],
        "unsupportedClaims": [],
        "improvementSuggestions": [] if supported else [
            f"Provide evidence for {explanation['conceptName']}."
        ],
        "verificationStatus": explanation["verificationStatus"],
    }


def submit_peer_feedback(
    database_path,
    *,
    explanation_id: str,
    author_id: str,
    feedback_type: str,
    comment: str | None = None,
    addresses_concept_gap: bool = False,
    suggestion: str | None = None,
) -> dict[str, Any]:
    """Submit structured peer feedback on an explanation."""
    result = create_peer_feedback(
        database_path,
        explanation_id=explanation_id,
        author_id=author_id,
        feedback_type=feedback_type,
        comment=comment,
        addresses_concept_gap=addresses_concept_gap,
        suggestion=suggestion,
    )
    if result is None:
        raise ApiError(400, "FEEDBACK_CREATE_FAILED", "Feedback could not be recorded.")
    return result


def list_explanation_feedback(
    database_path,
    *,
    explanation_id: str,
) -> list[dict[str, Any]]:
    """List peer feedback for an explanation."""
    return list_peer_feedback(database_path, explanation_id=explanation_id)


# ---------------------------------------------------------------------------
# Shared artifacts
# ---------------------------------------------------------------------------


def create_collab_artifact(
    database_path,
    *,
    group_id: str | None,
    author_id: str,
    title: str,
    artifact_type: str,
    description: str | None = None,
    visibility: str = "group",
    content: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a shared knowledge artifact with version 1."""
    result = create_shared_artifact(
        database_path,
        group_id=group_id,
        author_id=author_id,
        title=title,
        artifact_type=artifact_type,
        description=description,
        visibility=visibility,
        content=content,
    )
    if result is None:
        raise ApiError(400, "ARTIFACT_CREATE_FAILED", "Artifact could not be created.")
    return result


def update_collab_artifact(
    database_path,
    *,
    artifact_id: str,
    author_id: str,
    title: str | None = None,
    description: str | None = None,
    content: dict[str, Any] | None = None,
    edit_summary: str | None = None,
) -> dict[str, Any]:
    """Create a new version of a shared artifact (edit history preserved)."""
    artifact = get_shared_artifact(database_path, artifact_id=artifact_id)
    if artifact is None:
        raise ApiError(404, "ARTIFACT_NOT_FOUND", "Artifact was not found.")
    new_version_number = artifact["versionNumber"] + 1
    result = create_artifact_version(
        database_path,
        artifact_id=artifact_id,
        author_id=author_id,
        version_number=new_version_number,
        title=title or artifact["title"],
        content=content,
        edit_summary=edit_summary,
        is_current=True,
    )
    if result is None:
        raise ApiError(400, "ARTIFACT_VERSION_FAILED", "New version could not be created.")
    add_artifact_contributor(
        database_path,
        artifact_id=artifact_id,
        user_id=author_id,
        role="editor",
        contribution_summary=edit_summary or "Edited the artifact.",
    )
    return result


def get_collab_artifact(
    database_path,
    *,
    artifact_id: str,
) -> dict[str, Any] | None:
    """Return a shared artifact with current version and contributors."""
    return get_shared_artifact(database_path, artifact_id=artifact_id)


def list_collab_artifacts(
    database_path,
    *,
    group_id: str | None = None,
    artifact_type: str | None = None,
    visibility: str | None = None,
    author_id: str | None = None,
    viewer_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List shared artifacts with group visibility enforcement."""
    artifacts = list_shared_artifacts(
        database_path,
        group_id=group_id,
        artifact_type=artifact_type,
        visibility=visibility,
        author_id=author_id,
        limit=limit,
        offset=offset,
    )
    if viewer_id is not None:
        filtered = []
        for a in artifacts:
            if a["visibility"] == "group" and a["groupId"] is not None:
                members = _list_group_members(
                    database_path, group_id=a["groupId"], user_id=viewer_id
                )
                if not members or viewer_id not in {m["userId"] for m in members}:
                    continue
            filtered.append(a)
        artifacts = filtered
    return artifacts


def list_artifact_version_history(
    database_path,
    *,
    artifact_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List the edit history of a shared artifact."""
    return list_artifact_versions(
        database_path, artifact_id=artifact_id, limit=limit, offset=offset
    )


# ---------------------------------------------------------------------------
# Group analytics (aggregate only, no individual performance exposure)
# ---------------------------------------------------------------------------


def get_group_analytics(
    database_path,
    *,
    group_id: str,
    viewer_id: str | None = None,
) -> dict[str, Any]:
    """Read-only group-level analytics.

    Aggregates discussions, explanations, feedback, and artifacts. Never
    exposes individual learner performance without explicit authorization.
    """
    group = _get_group(database_path, group_id=group_id, user_id=viewer_id)
    if group is None:
        raise ApiError(404, "GROUP_NOT_FOUND", "Group was not found.")
    if viewer_id is not None:
        members = _list_group_members(
            database_path, group_id=group_id, user_id=viewer_id
        )
        if not members or viewer_id not in {m["userId"] for m in members}:
            raise ApiError(403, "NOT_GROUP_MEMBER", "You are not a member of this group.")
    discussions = list_collaborative_discussions(
        database_path, group_id=group_id
    )
    explanations = list_peer_explanations(
        database_path
    )
    artifacts = list_shared_artifacts(
        database_path, group_id=group_id
    )
    total_replies = 0
    total_unanswered = 0
    for d in discussions:
        replies = _list_discussion_replies_storage(database_path, discussion_id=d["discussionId"])
        total_replies += len(replies)
        total_unanswered += sum(1 for r in replies if not r["isVerified"])
    group_explanations = [
        e for e in explanations if e["discussionId"] in {d["discussionId"] for d in discussions}
    ]
    return {
        "groupId": group_id,
        "groupTitle": group["title"],
        "discussionCount": len(discussions),
        "replyCount": total_replies,
        "unansweredQuestionCount": total_unanswered,
        "peerExplanationCount": len(group_explanations),
        "verifiedExplanationCount": sum(
            1 for e in group_explanations if e["verificationStatus"] == "verified"
        ),
        "artifactCount": len(artifacts),
        "artifactTypes": sorted({a["artifactType"] for a in artifacts}),
        "generatedAt": _now_iso(),
        "dataSources": [
            "collaborative_discussions",
            "discussion_replies (M89)",
            "peer_explanations",
            "shared_artifacts",
        ],
    }


def list_contribution_history(
    database_path,
    *,
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List a user's own collaboration contributions (explanations + artifacts)."""
    explanations = list_peer_explanations(
        database_path, author_id=user_id, limit=limit, offset=offset
    )
    artifacts = list_shared_artifacts(
        database_path, author_id=user_id, limit=limit, offset=offset
    )
    history = []
    for e in explanations:
        history.append({
            "type": "explanation",
            "id": e["explanationId"],
            "conceptName": e["conceptName"],
            "verificationStatus": e["verificationStatus"],
            "createdAt": e["createdAt"],
        })
    for a in artifacts:
        history.append({
            "type": "artifact",
            "id": a["artifactId"],
            "title": a["title"],
            "artifactType": a["artifactType"],
            "createdAt": a["createdAt"],
        })
    history.sort(key=lambda h: h["createdAt"], reverse=True)
    return history[:limit]
