"""Shared study-space routes: private collaboration workspaces with explicit consent."""
# ruff: noqa: B008,E501

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth import AuthUser, require_auth
from app.errors import ApiError
from app.schemas import (
    StudySpaceCreateRequest,
    StudySpaceListResponse,
    StudySpaceResponse,
    StudySpaceUpdateRequest,
    WorkspaceDiscussion,
    WorkspaceDiscussionCreateRequest,
    WorkspaceDiscussionListResponse,
    WorkspaceDiscussionReply,
    WorkspaceDiscussionReplyCreateRequest,
    WorkspaceDiscussionReplyListResponse,
    WorkspaceDocument,
    WorkspaceDocumentListResponse,
    WorkspaceDocumentShareRequest,
    WorkspaceMember,
    WorkspaceMemberInviteRequest,
    WorkspaceQuiz,
    WorkspaceQuizListResponse,
    WorkspaceQuizShareRequest,
)
from app.storage import (
    add_workspace_member,
    create_study_space,
    create_workspace_discussion,
    create_workspace_discussion_reply,
    delete_study_space,
    get_study_space,
    list_study_spaces,
    list_workspace_discussion_replies,
    list_workspace_discussions,
    list_workspace_documents,
    list_workspace_members,
    list_workspace_quizzes,
    remove_workspace_document,
    remove_workspace_member,
    share_document_with_workspace,
    share_quiz_with_workspace,
    update_study_space,
)

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _check_membership_or_404(
    database_path, space_id: str, user_id: str, detail: str = "Workspace not found or access denied."
) -> None:
    result = get_study_space(database_path, space_id=space_id, user_id=user_id)
    if result is None:
        raise ApiError(404, "WORKSPACE_NOT_FOUND", detail)


@router.post(
    "", response_model=StudySpaceResponse, status_code=201
)
def create_workspace_route(
    request: Request,
    payload: StudySpaceCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a private study space owned by the authenticated user."""
    try:
        space = create_study_space(
            _database_path(request),
            user_id=current_user.user_id,
            title=payload.title,
            description=payload.description,
            learning_objective=payload.learningObjective,
        )
    except ValueError as exc:
        raise ApiError(400, "INVALID_WORKSPACE", str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    return space


@router.get("", response_model=StudySpaceListResponse)
def list_workspaces_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = None,
) -> dict[str, Any]:
    """List study spaces the current user owns or is a member of."""
    if status is not None and status not in {"active", "archived"}:
        raise ApiError(422, "INVALID_STATUS", "Status must be 'active' or 'archived'.")
    try:
        spaces = list_study_spaces(_database_path(request), user_id=current_user.user_id, status=status)
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    return {"spaces": spaces}


@router.get(
    "/{space_id}", response_model=dict[str, Any]
)
def get_workspace_route(
    request: Request,
    space_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one workspace if the current user is a member."""
    _check_membership_or_404(
        _database_path(request), space_id, current_user.user_id
    )
    space = get_study_space(
        _database_path(request), space_id=space_id, user_id=current_user.user_id
    )
    if space is None:
        raise ApiError(404, "WORKSPACE_NOT_FOUND", "Workspace was not found.")
    return space


@router.patch(
    "/{space_id}", response_model=StudySpaceResponse
)
def update_workspace_route(
    request: Request,
    space_id: str,
    payload: StudySpaceUpdateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a workspace. Only the owner may update."""
    try:
        space = update_study_space(
            _database_path(request),
            space_id=space_id,
            user_id=current_user.user_id,
            title=payload.title,
            description=payload.description,
            learning_objective=payload.learningObjective,
            status=payload.status,
        )
    except ValueError as exc:
        raise ApiError(400, "INVALID_WORKSPACE_UPDATE", str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if space is None:
        raise ApiError(403, "WORKSPACE_FORBIDDEN", "Only the workspace owner may update it.")
    return space


@router.delete("/{space_id}")
def delete_workspace_route(
    request: Request,
    space_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a workspace. Only the owner may delete."""
    deleted = delete_study_space(
        _database_path(request), space_id=space_id, user_id=current_user.user_id
    )
    if not deleted:
        raise ApiError(
            403, "WORKSPACE_FORBIDDEN", "Only the workspace owner may delete it."
        )
    return {"spaceId": space_id, "deleted": True}


@router.get(
    "/{space_id}/members", response_model=list[WorkspaceMember]
)
def list_workspace_members_route(
    request: Request,
    space_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> Any:
    """List members of a workspace. Caller must be a member."""
    try:
        members = list_workspace_members(
            _database_path(request), space_id=space_id, user_id=current_user.user_id
        )
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if members is None:
        raise ApiError(403, "WORKSPACE_FORBIDDEN", "You are not a member of this workspace.")
    return members


@router.post(
    "/{space_id}/members", response_model=WorkspaceMember, status_code=201
)
def invite_workspace_member_route(
    request: Request,
    space_id: str,
    payload: WorkspaceMemberInviteRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Invite a user to a workspace by username. Only the owner may invite."""
    member = add_workspace_member(
        _database_path(request),
        space_id=space_id,
        inviter_user_id=current_user.user_id,
        target_username=payload.username,
    )
    if member is None:
        raise ApiError(
            403,
            "WORKSPACE_FORBIDDEN",
            "Only the workspace owner may invite, or the user was not found or is already a member.",
        )
    return member


@router.delete(
    "/{space_id}/members/{target_user_id}"
)
def remove_workspace_member_route(
    request: Request,
    space_id: str,
    target_user_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Remove a member from a workspace. Only the owner may remove."""
    removed = remove_workspace_member(
        _database_path(request),
        space_id=space_id,
        actor_user_id=current_user.user_id,
        target_user_id=target_user_id,
    )
    if not removed:
        raise ApiError(
            403,
            "WORKSPACE_FORBIDDEN",
            "Only the workspace owner may remove members, or the target is not a member.",
        )
    return {"userId": target_user_id, "removed": True}


@router.get(
    "/{space_id}/documents", response_model=WorkspaceDocumentListResponse
)
def list_workspace_documents_route(
    request: Request,
    space_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List documents shared with a workspace. Caller must be a member."""
    _check_membership_or_404(
        _database_path(request), space_id, current_user.user_id
    )
    try:
        documents = list_workspace_documents(
            _database_path(request), space_id=space_id, user_id=current_user.user_id
        )
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if documents is None:
        raise ApiError(403, "WORKSPACE_FORBIDDEN", "You are not a member of this workspace.")
    return {"documents": documents}


@router.post(
    "/{space_id}/documents", response_model=WorkspaceDocument, status_code=201
)
def share_workspace_document_route(
    request: Request,
    space_id: str,
    payload: WorkspaceDocumentShareRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Share an owned document with a workspace. Caller must be a member and document owner."""
    shared = share_document_with_workspace(
        _database_path(request),
        space_id=space_id,
        actor_user_id=current_user.user_id,
        document_id=payload.documentId,
    )
    if shared is None:
        raise ApiError(
            403,
            "WORKSPACE_FORBIDDEN",
            "You are not a member of this workspace, do not own this document, or it is already shared.",
        )
    return shared


@router.delete(
    "/{space_id}/documents/{document_id}"
)
def unshare_workspace_document_route(
    request: Request,
    space_id: str,
    document_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Unshare a document from a workspace. Sharer or owner may unshare."""
    removed = remove_workspace_document(
        _database_path(request),
        space_id=space_id,
        actor_user_id=current_user.user_id,
        document_id=document_id,
    )
    if not removed:
        raise ApiError(
            403,
            "WORKSPACE_FORBIDDEN",
            "You are not a member, or you do not own this document/share.",
        )
    return {"documentId": document_id, "removed": True}


@router.get(
    "/{space_id}/quizzes", response_model=WorkspaceQuizListResponse
)
def list_workspace_quizzes_route(
    request: Request,
    space_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List quizzes shared with a workspace. Caller must be a member."""
    _check_membership_or_404(
        _database_path(request), space_id, current_user.user_id
    )
    try:
        quizzes = list_workspace_quizzes(
            _database_path(request), space_id=space_id, user_id=current_user.user_id
        )
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if quizzes is None:
        raise ApiError(403, "WORKSPACE_FORBIDDEN", "You are not a member of this workspace.")
    return {"quizzes": quizzes}


@router.post(
    "/{space_id}/quizzes", response_model=WorkspaceQuiz
)
def share_workspace_quiz_route(
    request: Request,
    space_id: str,
    payload: WorkspaceQuizShareRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Share an owned quiz with a workspace. Caller must be a member and quiz owner."""
    shared = share_quiz_with_workspace(
        _database_path(request),
        space_id=space_id,
        actor_user_id=current_user.user_id,
        quiz_id=payload.quizId,
    )
    if shared is None:
        raise ApiError(
            403,
            "WORKSPACE_FORBIDDEN",
            "You are not a member of this workspace, do not own this quiz, or it is already shared.",
        )
    return shared


@router.get(
    "/{space_id}/discussions", response_model=WorkspaceDiscussionListResponse
)
def list_workspace_discussions_route(
    request: Request,
    space_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List discussion threads in a workspace. Caller must be a member."""
    _check_membership_or_404(
        _database_path(request), space_id, current_user.user_id
    )
    try:
        discussions = list_workspace_discussions(
            _database_path(request), space_id=space_id, user_id=current_user.user_id
        )
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if discussions is None:
        raise ApiError(403, "WORKSPACE_FORBIDDEN", "You are not a member of this workspace.")
    return {"discussions": discussions}


@router.post(
    "/{space_id}/discussions", response_model=WorkspaceDiscussion, status_code=201
)
def create_workspace_discussion_route(
    request: Request,
    space_id: str,
    payload: WorkspaceDiscussionCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a discussion thread in a workspace. Caller must be a member."""
    try:
        discussion = create_workspace_discussion(
            _database_path(request),
            space_id=space_id,
            user_id=current_user.user_id,
            content=payload.content,
        )
    except ValueError as exc:
        raise ApiError(400, "INVALID_DISCUSSION", str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if discussion is None:
        raise ApiError(403, "WORKSPACE_FORBIDDEN", "You are not a member of this workspace.")
    return discussion


@router.get(
    "/{space_id}/discussions/{discussion_id}/replies",
    response_model=WorkspaceDiscussionReplyListResponse,
)
def list_workspace_discussion_replies_route(
    request: Request,
    space_id: str,
    discussion_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List replies to a discussion thread. Caller must be a member."""
    _check_membership_or_404(
        _database_path(request), space_id, current_user.user_id
    )
    try:
        replies = list_workspace_discussion_replies(
            _database_path(request),
            space_id=space_id,
            discussion_id=discussion_id,
            user_id=current_user.user_id,
        )
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if replies is None:
        raise ApiError(403, "WORKSPACE_FORBIDDEN", "You are not a member of this workspace.")
    return {"replies": replies}


@router.post(
    "/{space_id}/discussions/{discussion_id}/replies",
    response_model=WorkspaceDiscussionReply,
    status_code=201,
)
def create_workspace_discussion_reply_route(
    request: Request,
    space_id: str,
    discussion_id: str,
    payload: WorkspaceDiscussionReplyCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Reply to a discussion thread. Caller must be a member."""
    try:
        reply = create_workspace_discussion_reply(
            _database_path(request),
            space_id=space_id,
            discussion_id=discussion_id,
            user_id=current_user.user_id,
            content=payload.content,
        )
    except ValueError as exc:
        raise ApiError(400, "INVALID_REPLY", str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if reply is None:
        raise ApiError(403, "WORKSPACE_FORBIDDEN", "You are not a member of this workspace or discussion not found.")
    return reply
