"""M89: Social Learning Network & Peer Knowledge Exchange API routes.

Endpoints:
- POST   /api/v1/social/profiles                      - create/get profile
- GET    /api/v1/social/profiles/me                    - own profile
- PATCH  /api/v1/social/profiles/me                    - update profile
- GET    /api/v1/social/profiles/{user_id}             - view profile
- POST   /api/v1/social/discover                       - discover learners
- POST   /api/v1/social/connections                    - follow user
- GET    /api/v1/social/connections                    - list connections
- POST   /api/v1/social/connections/{id}/respond       - accept/reject/block
- POST   /api/v1/social/discussions                    - create discussion
- GET    /api/v1/social/discussions                    - list discussions
- GET    /api/v1/social/discussions/{id}               - get discussion
- POST   /api/v1/social/discussions/{id}/replies       - create reply
- GET    /api/v1/social/discussions/{id}/replies       - list replies
- POST   /api/v1/social/reports                        - report contribution
- GET    /api/v1/social/reports                        - list reports (moderation)
- POST   /api/v1/social/reports/{id}/moderate          - moderate report
- GET    /api/v1/social/notifications                  - list notifications
- POST   /api/v1/social/notifications/{id}/read        - mark read
- GET    /api/v1/social/notifications/preferences      - get preferences
- PATCH  /api/v1/social/notifications/preferences      - update preferences
- GET    /api/v1/social/intelligence                   - social summary
"""
# ruff: noqa: B008

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.auth import AuthUser, require_auth
from app.errors import ApiError
from app.schemas import (
    ConnectionListResponse,
    ConnectionRequest,
    ConnectionRespondRequest,
    ConnectionResponse,
    ContributionReportListResponse,
    ContributionReportRequest,
    ContributionReportResponse,
    DiscussionCreateRequest,
    DiscussionListResponse,
    DiscussionReplyCreateRequest,
    DiscussionReplyListResponse,
    DiscussionReplyResponse,
    DiscussionResponse,
    LearnerDiscoveryRequest,
    LearnerDiscoveryResponse,
    LearnerProfileCreateRequest,
    LearnerProfileResponse,
    LearnerProfileUpdateRequest,
    ModerateReportRequest,
    SocialIntelligenceResponse,
    SocialNotificationListResponse,
    SocialNotificationPreferenceRequest,
    SocialNotificationPreferenceResponse,
)
from app.social import (
    create_discussion_contribution,
    create_discussion_reply_contribution,
    create_or_get_learner_profile,
    discover_learners,
    follow_user,
    get_discussion_contribution,
    get_social_intelligence,
    get_social_notification_preferences,
    list_discussion_contributions,
    list_discussion_replies_service,
    list_reports,
    list_social_notifications,
    list_user_connections,
    mark_social_notification_read,
    moderate_contribution_report,
    report_contribution_content,
    respond_connection,
    update_learner_profile_service,
    update_social_notification_preferences,
)
from app.social import (
    get_learner_profile as _get_social_profile,
)

router = APIRouter(prefix="/api/v1")


def _database_path(request: Request):
    return request.app.state.settings.database_path


# ---------------------------------------------------------------------------
# Learner profiles
# ---------------------------------------------------------------------------


@router.post(
    "/social/profiles",
    response_model=LearnerProfileResponse,
    tags=["social"],
    status_code=201,
)
def create_or_get_social_profile_route(
    request: Request,
    payload: LearnerProfileCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get an existing learner profile or create one."""
    database_path = _database_path(request)
    profile = create_or_get_learner_profile(
        database_path,
        user_id=current_user.user_id,
        display_name=payload.displayName,
        biography=payload.biography,
        learning_interests=payload.learningInterests,
        subjects=payload.subjects,
        skills=payload.skills,
        learning_goals=payload.learningGoals,
        selected_portfolio_entry_ids=payload.selectedPortfolioEntryIds,
        discovery_enabled=payload.discoveryEnabled,
        show_skills=payload.showSkills,
        show_certificates=payload.showCertificates,
        show_portfolio=payload.showPortfolio,
    )
    return profile


@router.get(
    "/social/profiles/me",
    response_model=LearnerProfileResponse,
    tags=["social"],
)
def get_own_social_profile_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the authenticated user's own profile."""
    database_path = _database_path(request)

    profile = _get_social_profile(
        database_path, user_id=current_user.user_id, viewer_id=current_user.user_id
    )
    if profile is None:
        raise ApiError(404, "PROFILE_NOT_FOUND", "Learner profile was not found.")
    return profile


@router.patch(
    "/social/profiles/me",
    response_model=LearnerProfileResponse,
    tags=["social"],
)
def update_own_social_profile_route(
    request: Request,
    payload: LearnerProfileUpdateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update the authenticated user's own profile."""
    database_path = _database_path(request)
    profile = update_learner_profile_service(
        database_path,
        user_id=current_user.user_id,
        display_name=payload.displayName,
        biography=payload.biography,
        learning_interests=payload.learningInterests,
        subjects=payload.subjects,
        skills=payload.skills,
        learning_goals=payload.learningGoals,
        selected_portfolio_entry_ids=payload.selectedPortfolioEntryIds,
        discovery_enabled=payload.discoveryEnabled,
        show_skills=payload.showSkills,
        show_certificates=payload.showCertificates,
        show_portfolio=payload.showPortfolio,
    )
    return profile


@router.get(
    "/social/profiles/{user_id}",
    response_model=LearnerProfileResponse,
    tags=["social"],
)
def view_social_profile_route(
    request: Request,
    user_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """View another learner's public profile."""
    database_path = _database_path(request)

    profile = _get_social_profile(
        database_path, user_id=user_id, viewer_id=current_user.user_id
    )
    if profile is None:
        raise ApiError(404, "PROFILE_NOT_FOUND", "Learner profile was not found.")
    return profile


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@router.post(
    "/social/discover",
    response_model=LearnerDiscoveryResponse,
    tags=["social"],
)
def discover_learners_route(
    request: Request,
    payload: LearnerDiscoveryRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Discover learners by shared interests/subjects."""
    database_path = _database_path(request)
    profiles = discover_learners(
        database_path,
        current_user_id=current_user.user_id,
        interest=payload.interest,
        subject=payload.subject,
        limit=payload.limit,
        offset=payload.offset,
    )
    return {"profiles": profiles}


# ---------------------------------------------------------------------------
# Connections / follow
# ---------------------------------------------------------------------------


@router.post(
    "/social/connections",
    response_model=ConnectionResponse,
    tags=["social"],
    status_code=201,
)
def follow_user_route(
    request: Request,
    payload: ConnectionRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Send a follow/connection request to another learner."""
    database_path = _database_path(request)
    if payload.followingId == current_user.user_id:
        raise ApiError(400, "CANNOT_FOLLOW_SELF", "You cannot follow yourself.")
    connection = follow_user(
        database_path,
        follower_id=current_user.user_id,
        following_id=payload.followingId,
    )
    return connection


@router.get(
    "/social/connections",
    response_model=ConnectionListResponse,
    tags=["social"],
)
def list_user_connections_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    direction: str = Query(default="following"),
    status: str = Query(default="accepted"),
) -> dict[str, Any]:
    """List the authenticated user's connections."""
    database_path = _database_path(request)
    connections = list_user_connections(
        database_path,
        user_id=current_user.user_id,
        direction=direction,
        status=status,
    )
    return {"connections": connections}


@router.post(
    "/social/connections/{connection_id}/respond",
    response_model=ConnectionResponse,
    tags=["social"],
)
def respond_connection_route(
    request: Request,
    connection_id: str = Path(min_length=1, max_length=80),
    payload: ConnectionRespondRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Accept, reject, or block a connection request."""
    database_path = _database_path(request)
    connection = respond_connection(
        database_path,
        connection_id=connection_id,
        user_id=current_user.user_id,
        action=payload.action,
    )
    return connection


# ---------------------------------------------------------------------------
# Discussions & replies
# ---------------------------------------------------------------------------


@router.post(
    "/social/discussions",
    response_model=DiscussionResponse,
    tags=["social"],
    status_code=201,
)
def create_discussion_route(
    request: Request,
    payload: DiscussionCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a topic-based discussion contribution."""
    database_path = _database_path(request)
    discussion = create_discussion_contribution(
        database_path,
        author_id=current_user.user_id,
        topic=payload.topic,
        title=payload.title,
        content=payload.content,
        discussion_type=payload.discussionType,
        visibility=payload.visibility,
        ai_generated=payload.aiGenerated,
    )
    return discussion


@router.get(
    "/social/discussions",
    response_model=DiscussionListResponse,
    tags=["social"],
)
def list_discussions_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    topic: str | None = Query(default=None),
    discussion_type: str | None = Query(default=None),
    visibility: str | None = Query(default=None),
    author_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List discussion contributions."""
    database_path = _database_path(request)
    discussions = list_discussion_contributions(
        database_path,
        topic=topic,
        discussion_type=discussion_type,
        visibility=visibility,
        author_id=author_id,
        viewer_id=current_user.user_id,
        limit=limit,
        offset=offset,
    )
    return {"discussions": discussions}


@router.get(
    "/social/discussions/{discussion_id}",
    response_model=DiscussionResponse,
    tags=["social"],
)
def get_discussion_route(
    request: Request,
    discussion_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return a single discussion."""
    database_path = _database_path(request)
    discussion = get_discussion_contribution(
        database_path, discussion_id=discussion_id, viewer_id=current_user.user_id
    )
    if discussion is None:
        raise ApiError(404, "DISCUSSION_NOT_FOUND", "Discussion was not found.")
    return discussion


@router.post(
    "/social/discussions/{discussion_id}/replies",
    response_model=DiscussionReplyResponse,
    tags=["social"],
    status_code=201,
)
def create_discussion_reply_route(
    request: Request,
    discussion_id: str = Path(min_length=1, max_length=80),
    payload: DiscussionReplyCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a reply to a discussion."""
    database_path = _database_path(request)
    reply = create_discussion_reply_contribution(
        database_path,
        discussion_id=discussion_id,
        author_id=current_user.user_id,
        content=payload.content,
        ai_generated=payload.aiGenerated,
    )
    return reply


@router.get(
    "/social/discussions/{discussion_id}/replies",
    response_model=DiscussionReplyListResponse,
    tags=["social"],
)
def list_discussion_replies_route(
    request: Request,
    discussion_id: str = Path(min_length=1, max_length=80),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List replies to a discussion."""
    database_path = _database_path(request)
    replies = list_discussion_replies_service(
        database_path, discussion_id=discussion_id, limit=limit, offset=offset
    )
    return {"replies": replies}


# ---------------------------------------------------------------------------
# Moderation / reports
# ---------------------------------------------------------------------------


@router.post(
    "/social/reports",
    response_model=ContributionReportResponse,
    tags=["social"],
    status_code=201,
)
def report_contribution_route(
    request: Request,
    payload: ContributionReportRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Report a discussion or reply for moderation."""
    database_path = _database_path(request)
    report = report_contribution_content(
        database_path,
        reporter_id=current_user.user_id,
        reason=payload.reason,
        discussion_id=payload.discussionId,
        reply_id=payload.replyId,
        description=payload.description,
    )
    return report


@router.get(
    "/social/reports",
    response_model=ContributionReportListResponse,
    tags=["social"],
)
def list_reports_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List contribution reports for moderation."""
    database_path = _database_path(request)
    reports = list_reports(
        database_path, moderator_id=current_user.user_id, status=status,
        limit=limit, offset=offset,
    )
    return {"reports": reports}


@router.post(
    "/social/reports/{report_id}/moderate",
    tags=["social"],
)
def moderate_report_route(
    request: Request,
    report_id: str = Path(min_length=1, max_length=80),
    payload: ModerateReportRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Moderate a contribution report."""
    database_path = _database_path(request)
    return moderate_contribution_report(
        database_path,
        report_id=report_id,
        moderator_id=current_user.user_id,
        action=payload.action,
    )


# ---------------------------------------------------------------------------
# Notifications (M72 reuse)
# ---------------------------------------------------------------------------


@router.get(
    "/social/notifications",
    response_model=SocialNotificationListResponse,
    tags=["social"],
)
def list_social_notifications_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List social notifications for the authenticated user."""
    database_path = _database_path(request)
    notifications = list_social_notifications(
        database_path,
        user_id=current_user.user_id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )
    return {"notifications": notifications}


@router.post(
    "/social/notifications/{notification_id}/read",
    tags=["social"],
)
def mark_social_notification_read_route(
    request: Request,
    notification_id: str = Path(min_length=1, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Mark a social notification as read."""
    database_path = _database_path(request)
    result = mark_social_notification_read(
        database_path,
        notification_id=notification_id,
        user_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(404, "NOTIFICATION_NOT_FOUND", "Notification was not found.")
    return result


@router.get(
    "/social/notifications/preferences",
    response_model=SocialNotificationPreferenceResponse,
    tags=["social"],
)
def get_social_notification_preferences_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get social notification preferences."""
    database_path = _database_path(request)
    prefs = get_social_notification_preferences(
        database_path, user_id=current_user.user_id
    )
    if prefs is None:
        raise ApiError(404, "PREFERENCES_NOT_FOUND", "Notification preferences were not found.")
    return prefs


@router.patch(
    "/social/notifications/preferences",
    response_model=SocialNotificationPreferenceResponse,
    tags=["social"],
)
def update_social_notification_preferences_route(
    request: Request,
    payload: SocialNotificationPreferenceRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update social notification preferences."""
    database_path = _database_path(request)
    prefs = update_social_notification_preferences(
        database_path,
        user_id=current_user.user_id,
        email_enabled=payload.emailEnabled,
        in_app_enabled=payload.inAppEnabled,
        announcement_enabled=payload.announcementEnabled,
        assignment_enabled=payload.assignmentEnabled,
        feedback_enabled=payload.feedbackEnabled,
        assessment_enabled=payload.assessmentEnabled,
        progress_update_enabled=payload.progressUpdateEnabled,
    )
    if prefs is None:
        raise ApiError(400, "PREFERENCES_UPDATE_FAILED", "Preferences could not be updated.")
    return prefs


# ---------------------------------------------------------------------------
# Social intelligence
# ---------------------------------------------------------------------------


@router.get(
    "/social/intelligence",
    response_model=SocialIntelligenceResponse,
    tags=["social"],
)
def social_intelligence_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Read-only social summary for the authenticated user."""
    database_path = _database_path(request)
    return get_social_intelligence(
        database_path, user_id=current_user.user_id
    )
