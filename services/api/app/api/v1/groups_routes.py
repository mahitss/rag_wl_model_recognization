"""M61: Collaborative Learning & Study Groups API routes.

Endpoints:

* POST   /api/v1/groups                                      - Create a study group
* GET    /api/v1/groups                                      - List user's study groups
* GET    /api/v1/groups/{group_id}                           - Get one study group
* PATCH  /api/v1/groups/{group_id}                           - Update a study group (owner only)
* DELETE /api/v1/groups/{group_id}                           - Delete a study group (owner only)
* POST   /api/v1/groups/{group_id}/join-code/regenerate      - Regenerate join code
* GET    /api/v1/groups/{group_id}/join-code                 - Get join code
* POST   /api/v1/groups/{group_id}/join                      - Join by join code
* POST   /api/v1/groups/{group_id}/invitations               - Create invitation
* GET    /api/v1/groups/{group_id}/invitations               - List invitations
* POST   /api/v1/groups/{group_id}/invitations/{invitation_id}/revoke - Revoke invitation
* POST   /api/v1/invitations/{invitation_id}/accept          - Accept invitation
* GET    /api/v1/groups/{group_id}/members                   - List members
* PATCH  /api/v1/groups/{group_id}/members/{user_id}/role    - Set member role
* DELETE /api/v1/groups/{group_id}/members/{user_id}         - Remove member

* POST   /api/v1/groups/{group_id}/sessions                  - Create study session
* GET    /api/v1/groups/{group_id}/sessions                  - List study sessions
* GET    /api/v1/groups/{group_id}/sessions/{session_id}     - Get one session
* PATCH  /api/v1/groups/{group_id}/sessions/{session_id}     - Update session status
* POST   /api/v1/groups/{group_id}/sessions/{session_id}/join - Join session
* POST   /api/v1/groups/{group_id}/sessions/{session_id}/leave - Leave session
* GET    /api/v1/groups/{group_id}/sessions/{session_id}/participants - List participants

* POST   /api/v1/groups/{group_id}/notes                     - Create shared note
* GET    /api/v1/groups/{group_id}/notes                     - List shared notes
* GET    /api/v1/groups/{group_id}/notes/{note_id}           - Get one note
* PATCH  /api/v1/groups/{group_id}/notes/{note_id}           - Update note
* DELETE /api/v1/groups/{group_id}/notes/{note_id}           - Delete note

* POST   /api/v1/groups/{group_id}/goals                     - Create group goal
* GET    /api/v1/groups/{group_id}/goals                     - List group goals
* PATCH  /api/v1/groups/{group_id}/goals/{goal_id}/status    - Update goal status

* POST   /api/v1/groups/{group_id}/revision                  - Create revision item
* GET    /api/v1/groups/{group_id}/revision                  - List revision items
* PATCH  /api/v1/groups/{group_id}/revision/{item_id}        - Update revision item

All endpoints require authentication and group membership (where applicable).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.api.v1.routes import _database_path, _settings
from app.auth import AuthUser, require_auth
from app.config import Settings
from app.errors import ApiError
from app.storage import (
    accept_study_group_invitation,
    add_workspace_member,
    create_group_revision_item,
    create_group_study_goal,
    create_shared_note,
    create_study_group_invitation,
    create_study_session,
    create_study_space,
    delete_shared_note,
    get_shared_note,
    get_study_session,
    get_study_space,
    get_study_space_join_code,
    list_group_revision_items,
    list_group_study_goals,
    list_shared_notes,
    list_study_sessions,
    list_study_spaces,
    list_workspace_members,
    regenerate_study_space_join_code,
    join_study_space_by_code,
    leave_study_session,
    list_study_session_participants,
    revoke_study_group_invitation,
    set_workspace_member_role,
    update_group_revision_item,
    update_group_study_goal_status,
    update_shared_note,
    update_study_session_status,
)

router = APIRouter(prefix="/api/v1/groups", tags=["study groups"])


def _check_membership_or_404(
    database_path: str, group_id: str, user_id: str
) -> None:
    result = get_study_space(database_path, space_id=group_id, user_id=user_id)
    if result is None:
        raise ApiError(404, "GROUP_NOT_FOUND", "Study group not found or access denied.")


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=dict[str, Any], status_code=201)
def create_group_route(
    request: Request,
    title: str = Body(..., min_length=1, max_length=200),
    description: str = Body("", max_length=4000),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new study group owned by the authenticated user."""
    try:
        group = create_study_space(
            _database_path(request),
            user_id=current_user.user_id,
            title=title,
            description=description,
        )
    except ValueError as exc:
        raise ApiError(400, "INVALID_GROUP", str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    return group


@router.get("", response_model=dict[str, Any])
def list_groups_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = Query(None),
) -> dict[str, Any]:
    """List study groups the current user owns or is a member of."""
    if status is not None and status not in {"active", "archived"}:
        raise ApiError(422, "INVALID_STATUS", "Status must be 'active' or 'archived'.")
    try:
        groups = list_study_spaces(_database_path(request), user_id=current_user.user_id, status=status)
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    return {"groups": groups}


@router.get("/{group_id}", response_model=dict[str, Any])
def get_group_route(
    request: Request,
    group_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one study group if the current user is a member."""
    _check_membership_or_404(_database_path(request), group_id, current_user.user_id)
    group = get_study_space(_database_path(request), space_id=group_id, user_id=current_user.user_id)
    if group is None:
        raise ApiError(404, "GROUP_NOT_FOUND", "Study group was not found.")
    return group


@router.patch("/{group_id}", response_model=dict[str, Any])
def update_group_route(
    request: Request,
    group_id: str,
    title: str | None = Body(None, min_length=1, max_length=200),
    description: str | None = Body(None, max_length=4000),
    status: str | None = Body(None),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a study group. Only the owner may update."""
    try:
        group = create_study_space.__wrapped__ if hasattr(create_study_space, "__wrapped__") else create_study_space
        from app.storage import update_study_space
        updated = update_study_space(
            _database_path(request),
            space_id=group_id,
            user_id=current_user.user_id,
            title=title,
            description=description,
            status=status,
        )
    except ValueError as exc:
        raise ApiError(400, "INVALID_GROUP_UPDATE", str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if updated is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "Only the group owner may update it.")
    return updated


@router.delete("/{group_id}")
def delete_group_route(
    request: Request,
    group_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a study group. Only the owner may delete."""
    from app.storage import delete_study_space
    deleted = delete_study_space(_database_path(request), space_id=group_id, user_id=current_user.user_id)
    if not deleted:
        raise ApiError(403, "GROUP_FORBIDDEN", "Only the group owner may delete it.")
    return {"groupId": group_id, "deleted": True}


# ---------------------------------------------------------------------------
# Join codes
# ---------------------------------------------------------------------------


@router.post("/{group_id}/join-code/regenerate")
def regenerate_join_code_route(
    request: Request,
    group_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Regenerate the join code for a study group. Owner/moderator only."""
    code = regenerate_study_space_join_code(
        _database_path(request), space_id=group_id, actor_user_id=current_user.user_id
    )
    if code is None:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "Only group owners and moderators can regenerate the join code.",
        )
    return {"joinCode": code}


@router.get("/{group_id}/join-code")
def get_join_code_route(
    request: Request,
    group_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return the join code for a study group. Members only."""
    code = get_study_space_join_code(
        _database_path(request), space_id=group_id, user_id=current_user.user_id
    )
    if code is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    return {"joinCode": code}


@router.post("/{group_id}/join")
def join_group_route(
    request: Request,
    group_id: str,
    join_code: str = Body(..., min_length=1, max_length=128),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Join a study group using a join code."""
    member = join_study_space_by_code(
        _database_path(request),
        space_id=group_id,
        join_code=join_code,
        user_id=current_user.user_id,
    )
    if member is None:
        raise ApiError(
            400,
            "JOIN_FAILED",
            "Invalid join code, or you are already a member of this group.",
        )
    return member


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


@router.post("/{group_id}/invitations", response_model=dict[str, Any], status_code=201)
def create_invitation_route(
    request: Request,
    group_id: str,
    target_username: str = Body(..., min_length=3, max_length=64),
    expires_in_days: int = Body(7, ge=1, le=90),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create an invitation to join a study group. Owner/moderator only."""
    invitation = create_study_group_invitation(
        _database_path(request),
        space_id=group_id,
        inviter_user_id=current_user.user_id,
        target_username=target_username,
        expires_in_days=expires_in_days,
    )
    if invitation is None:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "Only group owners and moderators can invite, or the user was not found or is already a member.",
        )
    return invitation


@router.get("/{group_id}/invitations", response_model=dict[str, Any])
def list_invitations_route(
    request: Request,
    group_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List invitations for a study group. Members only."""
    invitations = list_study_group_invitations(
        _database_path(request), space_id=group_id, user_id=current_user.user_id
    )
    if invitations is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    return {"invitations": invitations}


@router.post("/{group_id}/invitations/{invitation_id}/revoke")
def revoke_invitation_route(
    request: Request,
    group_id: str,
    invitation_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Revoke a pending invitation. Owner/moderator only."""
    ok = revoke_study_group_invitation(
        _database_path(request), invitation_id=invitation_id, actor_user_id=current_user.user_id
    )
    if not ok:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "Invitation not found, already processed, or you lack permission to revoke it.",
        )
    return {"invitationId": invitation_id, "revoked": True}


@router.post("/invitations/{invitation_id}/accept")
def accept_invitation_route(
    request: Request,
    invitation_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Accept a study group invitation."""
    result = accept_study_group_invitation(
        _database_path(request), invitation_id=invitation_id, user_id=current_user.user_id
    )
    if result is None:
        raise ApiError(
            400,
            "INVITATION_FAILED",
            "Invitation not found, expired, revoked, or already accepted.",
        )
    return result


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/{group_id}/members", response_model=list[dict[str, Any]])
def list_members_route(
    request: Request,
    group_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> Any:
    """List members of a study group. Caller must be a member."""
    members = list_workspace_members(
        _database_path(request), space_id=group_id, user_id=current_user.user_id
    )
    if members is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    return members


@router.patch("/{group_id}/members/{target_user_id}/role")
def set_member_role_route(
    request: Request,
    group_id: str,
    target_user_id: str,
    role: str = Body(..., embed=True),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Set a member's role. Owner only."""
    if role not in {"owner", "moderator", "member"}:
        raise ApiError(422, "INVALID_ROLE", "Role must be 'owner', 'moderator', or 'member'.")
    ok = set_workspace_member_role(
        _database_path(request),
        space_id=group_id,
        actor_user_id=current_user.user_id,
        target_user_id=target_user_id,
        role=role,
    )
    if not ok:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "Only the group owner can change roles, or the target is not a member.",
        )
    return {"userId": target_user_id, "role": role}


@router.delete("/{group_id}/members/{target_user_id}")
def remove_member_route(
    request: Request,
    group_id: str,
    target_user_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Remove a member from a study group. Owner only."""
    from app.storage import remove_workspace_member
    removed = remove_workspace_member(
        _database_path(request),
        space_id=group_id,
        actor_user_id=current_user.user_id,
        target_user_id=target_user_id,
    )
    if not removed:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "Only the group owner can remove members, or the target is not a member.",
        )
    return {"userId": target_user_id, "removed": True}


# ---------------------------------------------------------------------------
# Study sessions
# ---------------------------------------------------------------------------


@router.post("/{group_id}/sessions", response_model=dict[str, Any], status_code=201)
def create_session_route(
    request: Request,
    group_id: str,
    title: str = Body(..., min_length=1, max_length=200),
    topic: str = Body(..., min_length=1, max_length=160),
    status: str = Body("scheduled", embed=True),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a collaborative study session. Caller must be a group member."""
    if status not in {"scheduled", "active", "completed", "cancelled"}:
        raise ApiError(422, "INVALID_STATUS", "Invalid session status.")
    session = create_study_session(
        _database_path(request),
        space_id=group_id,
        created_by=current_user.user_id,
        title=title,
        topic=topic,
        status=status,
    )
    if session is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    return session


@router.get("/{group_id}/sessions", response_model=dict[str, Any])
def list_sessions_route(
    request: Request,
    group_id: str,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List study sessions for a group. Caller must be a member."""
    result = list_study_sessions(
        _database_path(request),
        space_id=group_id,
        user_id=current_user.user_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    if result is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    sessions, total = result
    return {"sessions": sessions, "total": total, "limit": limit, "offset": offset}


@router.get("/{group_id}/sessions/{session_id}", response_model=dict[str, Any])
def get_session_route(
    request: Request,
    group_id: str,
    session_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one study session."""
    session = get_study_session(
        _database_path(request), session_id=session_id, user_id=current_user.user_id
    )
    if session is None:
        raise ApiError(404, "SESSION_NOT_FOUND", "Study session was not found.")
    if session["spaceId"] != group_id:
        raise ApiError(404, "SESSION_NOT_FOUND", "Session does not belong to this group.")
    return session


@router.patch("/{group_id}/sessions/{session_id}")
def update_session_route(
    request: Request,
    group_id: str,
    session_id: str,
    status: str = Body(..., embed=True),
    outcome_note: str | None = Body(None, max_length=1000),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a study session's status. Host or owner/moderator only."""
    if status not in {"scheduled", "active", "completed", "cancelled"}:
        raise ApiError(422, "INVALID_STATUS", "Invalid session status.")
    result = update_study_session_status(
        _database_path(request),
        session_id=session_id,
        actor_user_id=current_user.user_id,
        status=status,
        outcome_note=outcome_note,
    )
    if result is None:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "Session not found, or you lack permission to update it.",
        )
    return result


@router.post("/{group_id}/sessions/{session_id}/join")
def join_session_route(
    request: Request,
    group_id: str,
    session_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Join an active study session."""
    ok = join_study_session(
        _database_path(request), session_id=session_id, user_id=current_user.user_id
    )
    if not ok:
        raise ApiError(
            400,
            "JOIN_FAILED",
            "Session not found, not active, or you are not a group member.",
        )
    return {"sessionId": session_id, "joined": True}


@router.post("/{group_id}/sessions/{session_id}/leave")
def leave_session_route(
    request: Request,
    group_id: str,
    session_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Leave a study session."""
    ok = leave_study_session(
        _database_path(request), session_id=session_id, user_id=current_user.user_id
    )
    if not ok:
        raise ApiError(
            400,
            "LEAVE_FAILED",
            "Session not found, you are not a participant, or you are the host.",
        )
    return {"sessionId": session_id, "left": True}


@router.get("/{group_id}/sessions/{session_id}/participants", response_model=list[dict[str, Any]])
def list_participants_route(
    request: Request,
    group_id: str,
    session_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> Any:
    """List participants of a study session."""
    participants = list_study_session_participants(
        _database_path(request), session_id=session_id, user_id=current_user.user_id
    )
    if participants is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    return participants


# ---------------------------------------------------------------------------
# Shared notes
# ---------------------------------------------------------------------------


@router.post("/{group_id}/notes", response_model=dict[str, Any], status_code=201)
def create_note_route(
    request: Request,
    group_id: str,
    title: str = Body(..., min_length=1, max_length=200),
    content: str = Body(..., min_length=1),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a shared note. Caller must be a group member."""
    note = create_shared_note(
        _database_path(request),
        space_id=group_id,
        created_by=current_user.user_id,
        title=title,
        content=content,
    )
    if note is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    return note


@router.get("/{group_id}/notes", response_model=dict[str, Any])
def list_notes_route(
    request: Request,
    group_id: str,
    current_user: AuthUser = Depends(require_auth),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List shared notes for a group."""
    result = list_shared_notes(
        _database_path(request), space_id=group_id, user_id=current_user.user_id,
        limit=limit, offset=offset,
    )
    if result is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    notes, total = result
    return {"notes": notes, "total": total, "limit": limit, "offset": offset}


@router.get("/{group_id}/notes/{note_id}", response_model=dict[str, Any])
def get_note_route(
    request: Request,
    group_id: str,
    note_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one shared note."""
    note = get_shared_note(_database_path(request), note_id=note_id, user_id=current_user.user_id)
    if note is None:
        raise ApiError(404, "NOTE_NOT_FOUND", "Shared note was not found.")
    if note["spaceId"] != group_id:
        raise ApiError(404, "NOTE_NOT_FOUND", "Note does not belong to this group.")
    return note


@router.patch("/{group_id}/notes/{note_id}")
def update_note_route(
    request: Request,
    group_id: str,
    note_id: str,
    title: str | None = Body(None, min_length=1, max_length=200),
    content: str | None = Body(None, min_length=1),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a shared note. Creator or moderator/owner only."""
    note = update_shared_note(
        _database_path(request),
        note_id=note_id,
        actor_user_id=current_user.user_id,
        title=title,
        content=content,
    )
    if note is None:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "Note not found, you are not a group member, or you lack permission to edit it.",
        )
    if note["spaceId"] != group_id:
        raise ApiError(404, "NOTE_NOT_FOUND", "Note does not belong to this group.")
    return note


@router.delete("/{group_id}/notes/{note_id}")
def delete_note_route(
    request: Request,
    group_id: str,
    note_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a shared note. Creator or owner only."""
    ok = delete_shared_note(
        _database_path(request), note_id=note_id, actor_user_id=current_user.user_id
    )
    if not ok:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "Note not found, you are not a group member, or you lack permission to delete it.",
        )
    return {"noteId": note_id, "deleted": True}


# ---------------------------------------------------------------------------
# Group goals
# ---------------------------------------------------------------------------


@router.post("/{group_id}/goals", response_model=dict[str, Any], status_code=201)
def create_goal_route(
    request: Request,
    group_id: str,
    title: str = Body(..., min_length=1, max_length=200),
    target_topic: str | None = Body(None, max_length=160),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a group study goal. Caller must be a group member."""
    goal = create_group_study_goal(
        _database_path(request),
        space_id=group_id,
        created_by=current_user.user_id,
        title=title,
        target_topic=target_topic,
    )
    if goal is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    return goal


@router.get("/{group_id}/goals", response_model=dict[str, Any])
def list_goals_route(
    request: Request,
    group_id: str,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = Query(None),
) -> dict[str, Any]:
    """List group study goals."""
    goals = list_group_study_goals(
        _database_path(request), space_id=group_id, user_id=current_user.user_id, status=status
    )
    if goals is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    return {"goals": goals}


@router.patch("/{group_id}/goals/{goal_id}/status")
def update_goal_status_route(
    request: Request,
    group_id: str,
    goal_id: str,
    status: str = Body(..., embed=True),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a group study goal's status."""
    if status not in {"active", "completed", "abandoned"}:
        raise ApiError(422, "INVALID_STATUS", "Invalid goal status.")
    ok = update_group_study_goal_status(
        _database_path(request),
        goal_id=goal_id,
        actor_user_id=current_user.user_id,
        status=status,
    )
    if not ok:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "Goal not found, or you lack permission to update it.",
        )
    return {"goalId": goal_id, "status": status}


# ---------------------------------------------------------------------------
# Group revision items
# ---------------------------------------------------------------------------


@router.post("/{group_id}/revision", response_model=dict[str, Any], status_code=201)
def create_revision_item_route(
    request: Request,
    group_id: str,
    item_type: str = Body(..., embed=True),
    item_id: str = Body(..., embed=True),
    topic: str | None = Body(None, max_length=160),
    next_due_date: str | None = Body(None),
    assigned_to: str | None = Body(None, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a group revision item. Caller must be a group member."""
    if item_type not in {"quiz", "flashcard", "topic"}:
        raise ApiError(422, "INVALID_ITEM_TYPE", "Item type must be 'quiz', 'flashcard', or 'topic'.")
    item = create_group_revision_item(
        _database_path(request),
        space_id=group_id,
        created_by=current_user.user_id,
        item_type=item_type,
        item_id=item_id,
        topic=topic,
        next_due_date=next_due_date,
        assigned_to=assigned_to,
    )
    if item is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    return item


@router.get("/{group_id}/revision", response_model=dict[str, Any])
def list_revision_items_route(
    request: Request,
    group_id: str,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = Query(None),
) -> dict[str, Any]:
    """List group revision items."""
    items = list_group_revision_items(
        _database_path(request), space_id=group_id, user_id=current_user.user_id, status=status
    )
    if items is None:
        raise ApiError(403, "GROUP_FORBIDDEN", "You are not a member of this group.")
    return {"items": items}


@router.patch("/{group_id}/revision/{item_id}")
def update_revision_item_route(
    request: Request,
    group_id: str,
    item_id: str,
    status: str | None = Body(None, embed=True),
    next_due_date: str | None = Body(None),
    assigned_to: str | None = Body(None, max_length=80),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a group revision item. Caller must be a group member."""
    if status is not None and status not in {"pending", "completed", "skipped"}:
        raise ApiError(422, "INVALID_STATUS", "Invalid revision status.")
    ok = update_group_revision_item(
        _database_path(request),
        item_id=item_id,
        actor_user_id=current_user.user_id,
        status=status,
        next_due_date=next_due_date,
        assigned_to=assigned_to,
    )
    if not ok:
        raise ApiError(
            403,
            "GROUP_FORBIDDEN",
            "Revision item not found, or you are not a group member.",
        )
    return {"itemId": item_id, "updated": True}
