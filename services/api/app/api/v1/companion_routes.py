"""Personal AI Learning Companion API routes.

Unified conversational interface for grounded Q&A, quiz revision,
progress summaries, recommendations, and study-plan management.
All endpoints require authentication and are user-scoped.
"""
# ruff: noqa: B008,E501

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request

from app.api.v1.routes import _database_path, _embedding_service, _settings
from app.auth import AuthUser, require_auth
from app.companion import run_companion
from app.errors import ApiError
from app.schemas import (
    CompanionConversationCreateRequest,
    CompanionConversationListResponse,
    CompanionConversationResponse,
    CompanionMessageRequest,
    CompanionMessageResponse,
    CompanionMessagesResponse,
)
from app.storage import (
    create_conversation,
    delete_conversation,
    get_conversation,
    get_messages,
    list_conversations,
)

router = APIRouter(prefix="/api/v1/companion")


# ---------------------------------------------------------------------------
# Conversation lifecycle
# ---------------------------------------------------------------------------


@router.get("/conversations", response_model=CompanionConversationListResponse, tags=["companion"])
def list_conversations_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List the authenticated user's conversations, newest first."""
    items, total = list_conversations(
        _database_path(request),
        user_id=current_user.user_id,
        limit=limit,
        offset=offset,
    )
    return {
        "conversations": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "/conversations",
    response_model=CompanionConversationResponse,
    status_code=201,
    tags=["companion"],
)
def create_conversation_route(
    request: Request,
    payload: CompanionConversationCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new conversation for the authenticated user."""
    conv = create_conversation(
        _database_path(request),
        user_id=current_user.user_id,
        title=payload.title,
    )
    return conv


@router.delete("/conversations/{conversation_id}", tags=["companion"])
def delete_conversation_route(
    request: Request,
    conversation_id: Annotated[str, Path(min_length=1, max_length=80)],
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a conversation and all its messages (owner only)."""
    ok = delete_conversation(
        _database_path(request),
        conversation_id=conversation_id,
        user_id=current_user.user_id,
    )
    if not ok:
        raise ApiError(404, "CONVERSATION_NOT_FOUND", "The conversation was not found.")
    return {"conversationId": conversation_id, "deleted": True}


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=CompanionMessagesResponse,
    tags=["companion"],
)
def get_conversation_messages(
    request: Request,
    conversation_id: Annotated[str, Path(min_length=1, max_length=80)],
    current_user: AuthUser = Depends(require_auth),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return messages for a conversation, oldest first."""
    # Verify ownership.
    conv = get_conversation(
        _database_path(request),
        conversation_id=conversation_id,
        user_id=current_user.user_id,
    )
    if conv is None:
        raise ApiError(404, "CONVERSATION_NOT_FOUND", "The conversation was not found.")
    items, total = get_messages(
        _database_path(request),
        conversation_id=conversation_id,
        user_id=current_user.user_id,
        limit=limit,
        offset=offset,
    )
    return {
        "messages": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# Companion message
# ---------------------------------------------------------------------------


@router.post("/message", response_model=CompanionMessageResponse, tags=["companion"])
async def companion_message(
    request: Request,
    payload: CompanionMessageRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Send a message to the companion and receive a grounded response.

    The companion detects the user's intent (ask, quiz, revision, progress,
    recommend, manage) and routes to the existing WonderLearn services:
    grounded tutor answers, quiz generation, revision queue, learning
    analytics, adaptive recommendations, and learning paths.

    All actions are user-scoped and enforced server-side.  Destructive or
    significant actions require explicit confirmation.
    """
    settings = _settings(request)
    database_path = settings.database_path
    embeddings = _embedding_service(request)

    try:
        result = await run_companion(
            settings,
            database_path,
            current_user.user_id,
            message=payload.message,
            conversation_id=payload.conversation_id,
            mode=payload.mode,
            document_ids=payload.document_ids,
            limit=payload.limit,
            embeddings=embeddings,
        )
    except ApiError:
        raise
    except ValueError as exc:
        raise ApiError(422, "INVALID_REQUEST", str(exc)) from exc
    except (OSError, Exception) as exc:  # noqa: BLE001 - surface as 503
        raise ApiError(
            503,
            "COMPANION_ERROR",
            "The companion could not process the request.",
        ) from exc

    return result
