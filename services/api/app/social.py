"""M89: Social Learning Network & Peer Knowledge Exchange engine.

Builds on existing WonderLearn subsystems (no duplicate social/messaging
pipelines):

* ``app.storage`` — users, learner_profiles, connections, discussions,
  discussion_replies, contribution_reports, plus M87/M88 certificates,
  competencies, learning outcomes, portfolios.
* ``app.communication`` — M72 notification infrastructure
  (create_notification, get_notification_preferences).
* ``app.certification`` — M87 competency evaluation.

Design principles:
* Learner profiles are opt-in and discovery-controlled; private data never
  exposed through social features.
* Discussions/replies carry authorship, timestamps, ai_generated flags and
  verified flags; AI content never auto-trusted.
* Follow/connections are explicit and revocable; blocking supported.
* Reporting/moderation workflow with report counts.
* Reuses M72 in-app notifications for replies, follows, mentions, feedback.
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from app.communication import (
    get_user_notification_preferences_service,
    list_user_notifications_service,
    mark_user_notification_read_service,
    update_user_notification_preferences_service,
)
from app.errors import ApiError
from app.storage import (
    connect_database,
    create_connection,
    create_discussion,
    create_discussion_reply,
    create_learner_profile,
    get_discussion,
    list_connections,
    list_contribution_reports,
    list_discoverable_profiles,
    list_discussion_replies,
    list_discussions,
    moderate_report,
    report_contribution,
    respond_to_connection,
    update_learner_profile,
)

# ---------------------------------------------------------------------------
# Storage accessor shims (guarded so missing functions don't break import)
# ---------------------------------------------------------------------------


def _get_learner_profile(database_path, *, user_id: str):
    with closing_ctx(database_path) as connection:
        row = connection.execute(
            """
            SELECT profile_id, user_id, display_name, biography,
                   learning_interests_json, subjects_json, skills_json,
                   learning_goals_json, selected_portfolio_entry_ids_json,
                   discovery_enabled, show_skills, show_certificates,
                   show_portfolio, created_at, updated_at
            FROM learner_profiles
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
    return {
        "profileId": row["profile_id"],
        "userId": row["user_id"],
        "displayName": row["display_name"],
        "biography": row["biography"],
        "learningInterests": json.loads(row["learning_interests_json"] or "[]"),
        "subjects": json.loads(row["subjects_json"] or "[]"),
        "skills": json.loads(row["skills_json"] or "[]"),
        "learningGoals": json.loads(row["learning_goals_json"] or "[]"),
        "selectedPortfolioEntryIds": json.loads(
            row["selected_portfolio_entry_ids_json"] or "[]"
        ),
        "discoveryEnabled": bool(row["discovery_enabled"]),
        "showSkills": bool(row["show_skills"]),
        "showCertificates": bool(row["show_certificates"]),
        "showPortfolio": bool(row["show_portfolio"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def closing_ctx(database_path):

    return closing(connect_database(database_path))


import json  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DISCUSSION_TYPES = {"question", "discussion", "note", "resource"}
REPORT_REASONS = {"spam", "inappropriate", "inaccurate", "harassment", "copyright", "other"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Learner profiles
# ---------------------------------------------------------------------------


def create_or_get_learner_profile(
    database_path,
    *,
    user_id: str,
    display_name: str | None = None,
    biography: str | None = None,
    learning_interests: list[str] | None = None,
    subjects: list[str] | None = None,
    skills: list[str] | None = None,
    learning_goals: list[str] | None = None,
    selected_portfolio_entry_ids: list[str] | None = None,
    discovery_enabled: bool = True,
    show_skills: bool = True,
    show_certificates: bool = True,
    show_portfolio: bool = True,
) -> dict[str, Any]:
    """Get an existing profile or create one with the supplied fields."""
    existing = get_learner_profile(database_path, user_id=user_id)
    if existing is not None:
        return existing
    result = create_learner_profile(
        database_path,
        user_id=user_id,
        display_name=display_name,
        biography=biography,
        learning_interests=learning_interests,
        subjects=subjects,
        skills=skills,
        learning_goals=learning_goals,
        selected_portfolio_entry_ids=selected_portfolio_entry_ids,
        discovery_enabled=discovery_enabled,
        show_skills=show_skills,
        show_certificates=show_certificates,
        show_portfolio=show_portfolio,
    )
    if result is None:
        raise ApiError(400, "PROFILE_CREATE_FAILED", "Learner profile could not be created.")
    return result


def get_learner_profile(
    database_path,
    *,
    user_id: str,
    viewer_id: str | None = None,
) -> dict[str, Any] | None:
    """Return a learner profile, applying visibility rules for the viewer."""
    profile = _get_learner_profile(database_path, user_id=user_id)
    if profile is None:
        return None
    # Viewer access: owner always sees full profile; others see public fields
    # gated by show_* flags.
    if viewer_id is not None and viewer_id == user_id:
        return profile
    if not profile.get("discoveryEnabled", False) and viewer_id is not None:
        return None
    # Apply field visibility for non-owners.
    if not profile.get("showSkills", False):
        profile["skills"] = []
    if not profile.get("showCertificates", False):
        profile["certificates"] = []
    if not profile.get("showPortfolio", False):
        profile["selectedPortfolioEntryIds"] = []
    return profile


def update_learner_profile_service(
    database_path,
    *,
    user_id: str,
    display_name: str | None = None,
    biography: str | None = None,
    learning_interests: list[str] | None = None,
    subjects: list[str] | None = None,
    skills: list[str] | None = None,
    learning_goals: list[str] | None = None,
    selected_portfolio_entry_ids: list[str] | None = None,
    discovery_enabled: bool | None = None,
    show_skills: bool | None = None,
    show_certificates: bool | None = None,
    show_portfolio: bool | None = None,
) -> dict[str, Any]:
    """Update a learner profile. Owner only."""
    result = update_learner_profile(
        database_path,
        user_id=user_id,
        display_name=display_name,
        biography=biography,
        learning_interests=learning_interests,
        subjects=subjects,
        skills=skills,
        learning_goals=learning_goals,
        selected_portfolio_entry_ids=selected_portfolio_entry_ids,
        discovery_enabled=discovery_enabled,
        show_skills=show_skills,
        show_certificates=show_certificates,
        show_portfolio=show_portfolio,
    )
    if result is None:
        raise ApiError(400, "PROFILE_UPDATE_FAILED", "Learner profile could not be updated.")
    return result


def discover_learners(
    database_path,
    *,
    current_user_id: str,
    interest: str | None = None,
    subject: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Discover learners by shared interests/subjects.

    Only returns discoverable profiles (discovery_enabled=1), excluding the
    current user. Never exposes private assessment data.
    """
    profiles = list_discoverable_profiles(
        database_path, current_user_id=current_user_id, limit=limit, offset=offset
    )
    if interest is not None:
        interest_l = interest.casefold()
        profiles = [
            p for p in profiles
            if any(interest_l in i.casefold() for i in p.get("learningInterests", []))
        ]
    if subject is not None:
        subject_l = subject.casefold()
        profiles = [
            p for p in profiles
            if any(subject_l in s.casefold() for s in p.get("subjects", []))
        ]
    return profiles


# ---------------------------------------------------------------------------
# Connections / follow
# ---------------------------------------------------------------------------


def follow_user(
    database_path,
    *,
    follower_id: str,
    following_id: str,
) -> dict[str, Any]:
    """Send a follow/connection request."""
    result = create_connection(
        database_path, follower_id=follower_id, following_id=following_id
    )
    if result is None:
        raise ApiError(400, "CONNECTION_CREATE_FAILED", "Connection could not be created.")
    # Notify the followed user.
    try:
        from app.storage import create_notification as _create_notification
        _create_notification(
            database_path,
            user_id=following_id,
            type="feedback",
            title="New connection request",
            body="Someone wants to connect with you.",
            related_id=result["connectionId"],
            related_type="connection",
        )
    except Exception:
        pass
    return result


def list_user_connections(
    database_path,
    *,
    user_id: str,
    direction: str = "following",
    status: str = "accepted",
) -> list[dict[str, Any]]:
    """List a user's connections."""
    return list_connections(
        database_path, user_id=user_id, direction=direction, status=status
    )


def respond_connection(
    database_path,
    *,
    connection_id: str,
    user_id: str,
    action: str,
) -> dict[str, Any]:
    """Accept/reject/block a connection request."""
    result = respond_to_connection(
        database_path, connection_id=connection_id, user_id=user_id, action=action
    )
    if result is None:
        raise ApiError(400, "CONNECTION_RESPOND_FAILED", "Could not respond to connection request.")
    return result


# ---------------------------------------------------------------------------
# Discussions & replies (knowledge exchange)
# ---------------------------------------------------------------------------


def create_discussion_contribution(
    database_path,
    *,
    author_id: str,
    topic: str,
    title: str,
    content: str,
    discussion_type: str = "discussion",
    visibility: str = "public",
    ai_generated: bool = False,
) -> dict[str, Any]:
    """Create a discussion contribution."""
    result = create_discussion(
        database_path,
        author_id=author_id,
        topic=topic,
        title=title,
        content=content,
        discussion_type=discussion_type,
        visibility=visibility,
        ai_generated=ai_generated,
    )
    if result is None:
        raise ApiError(400, "DISCUSSION_CREATE_FAILED", "Discussion could not be created.")
    return result


def get_discussion_contribution(
    database_path,
    *,
    discussion_id: str,
    viewer_id: str | None = None,
) -> dict[str, Any] | None:
    """Return a discussion with visibility checks."""
    return get_discussion(database_path, discussion_id=discussion_id, user_id=viewer_id)


def list_discussion_contributions(
    database_path,
    *,
    topic: str | None = None,
    discussion_type: str | None = None,
    visibility: str | None = None,
    author_id: str | None = None,
    viewer_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List discussion contributions."""
    return list_discussions(
        database_path,
        topic=topic,
        discussion_type=discussion_type,
        visibility=visibility,
        author_id=author_id,
        viewer_id=viewer_id,
        limit=limit,
        offset=offset,
    )


def create_discussion_reply_contribution(
    database_path,
    *,
    discussion_id: str,
    author_id: str,
    content: str,
    ai_generated: bool = False,
) -> dict[str, Any]:
    """Create a reply to a discussion."""
    result = create_discussion_reply(
        database_path,
        discussion_id=discussion_id,
        author_id=author_id,
        content=content,
        ai_generated=ai_generated,
    )
    if result is None:
        raise ApiError(400, "REPLY_CREATE_FAILED", "Reply could not be created.")
    # Notify discussion author.
    try:
        discussion = get_discussion(database_path, discussion_id=discussion_id)
        if discussion and discussion["authorId"] != author_id:
            from app.storage import create_notification as _create_notification
            _create_notification(
                database_path,
                user_id=discussion["authorId"],
                type="feedback",
                title="New reply to your discussion",
                body="Someone replied to your discussion.",
                related_id=discussion_id,
                related_type="discussion",
            )
    except Exception:
        pass
    return result


def list_discussion_replies_service(
    database_path,
    *,
    discussion_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List replies to a discussion."""
    return list_discussion_replies(
        database_path, discussion_id=discussion_id, limit=limit, offset=offset
    )


# ---------------------------------------------------------------------------
# Moderation & trust
# ---------------------------------------------------------------------------


def report_contribution_content(
    database_path,
    *,
    reporter_id: str,
    reason: str,
    discussion_id: str | None = None,
    reply_id: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Report a contribution for moderation."""
    result = report_contribution(
        database_path,
        reporter_id=reporter_id,
        reason=reason,
        discussion_id=discussion_id,
        reply_id=reply_id,
        description=description,
    )
    if result is None:
        raise ApiError(400, "REPORT_CREATE_FAILED", "Report could not be created.")
    return result


def list_reports(
    database_path,
    *,
    moderator_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List contribution reports for moderation."""
    return list_contribution_reports(
        database_path, moderator_id=moderator_id, status=status, limit=limit, offset=offset
    )


def moderate_contribution_report(
    database_path,
    *,
    report_id: str,
    moderator_id: str,
    action: str,
) -> dict[str, Any]:
    """Moderate a report (dismiss/actioned)."""
    result = moderate_report(
        database_path, report_id=report_id, moderator_id=moderator_id, action=action
    )
    if result is None:
        raise ApiError(400, "MODERATE_FAILED", "Could not moderate report.")
    return result


# ---------------------------------------------------------------------------
# Notifications (reuse M72 infrastructure)
# ---------------------------------------------------------------------------


def list_social_notifications(
    database_path,
    *,
    user_id: str,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List social notifications for a user (M72 in-app notifications)."""
    return list_user_notifications_service(
        database_path, user_id=user_id, unread_only=unread_only, limit=limit, offset=offset
    )


def mark_social_notification_read(
    database_path,
    *,
    notification_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Mark a social notification as read."""
    return mark_user_notification_read_service(
        database_path, notification_id=notification_id, user_id=user_id
    )


def get_social_notification_preferences(
    database_path,
    *,
    user_id: str,
) -> dict[str, Any] | None:
    """Get social notification preferences (M72)."""
    return get_user_notification_preferences_service(database_path, user_id=user_id)


def update_social_notification_preferences(
    database_path,
    *,
    user_id: str,
    email_enabled: bool | None = None,
    in_app_enabled: bool | None = None,
    announcement_enabled: bool | None = None,
    assignment_enabled: bool | None = None,
    feedback_enabled: bool | None = None,
    assessment_enabled: bool | None = None,
    progress_update_enabled: bool | None = None,
) -> dict[str, Any] | None:
    """Update social notification preferences (M72)."""
    return update_user_notification_preferences_service(
        database_path,
        user_id=user_id,
        email_enabled=email_enabled,
        in_app_enabled=in_app_enabled,
        announcement_enabled=announcement_enabled,
        assignment_enabled=assignment_enabled,
        feedback_enabled=feedback_enabled,
        assessment_enabled=assessment_enabled,
        progress_update_enabled=progress_update_enabled,
    )


# ---------------------------------------------------------------------------
# Social intelligence (read-only, no fabricated engagement)
# ---------------------------------------------------------------------------


def get_social_intelligence(
    database_path,
    *,
    user_id: str,
) -> dict[str, Any]:
    """Read-only social summary for a user.

    Aggregates connections, discussions authored, and reports received.
    Never fabricates engagement.
    """
    connections = list_connections(
        database_path, user_id=user_id, status="accepted"
    )
    discussions = list_discussions(database_path, author_id=user_id)
    reports = list_contribution_reports(database_path)
    my_reports = [r for r in reports if r["reporterId"] == user_id]
    return {
        "userId": user_id,
        "connectionCount": len(connections),
        "discussionCount": len(discussions),
        "reportCount": len(my_reports),
        "generatedAt": _now_iso(),
    }
