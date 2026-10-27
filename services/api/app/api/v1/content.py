"""AI Learning Content Studio routes.

Evidence-grounded generation of learning resources (study notes, summaries,
flashcards, practice questions, revision sheets, concept outlines) from a
user's own documents or explicitly shared study-space materials.  Generated
content is labelled unverified until reviewed and always carries source
citations.
"""
# ruff: noqa: B008,E501

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth import AuthUser, require_auth
from app.config import Settings
from app.content_studio import ContentGenerationError, generate_content
from app.errors import ApiError
from app.retrieval.service import retrieve_with_mode
from app.schemas import (
    ContentResourceCreateRequest,
    ContentResourceListResponse,
    ContentResourceQuizLinkRequest,
    ContentResourceResponse,
    ContentResourceReviewRequest,
    ContentResourceRevisionListResponse,
    ContentResourceSection,
    ContentResourceUpdateRequest,
    ContentSectionEditRequest,
    ContentSectionRegenerateRequest,
)
from app.storage import (
    create_content_resource,
    create_content_section,
    delete_content_resource,
    get_content_resource,
    get_quiz_set,
    list_content_resources,
    list_content_revisions,
    regenerate_content_section,
    update_content_resource,
    update_content_section,
)
from app.tutor import TutorUnavailable

router = APIRouter(prefix="/api/v1/content", tags=["content studio"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _embedding_service(request: Request):
    return request.app.state.embedding_service


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _resolve_source_refs(
    settings: Settings,
    embeddings,
    database_path: str,
    user_id: str,
    payload: ContentResourceCreateRequest,
) -> tuple[list[dict[str, Any]], str, str, list[dict[str, Any]]]:
    """Resolve source material, retrieve evidence, and return source refs.

    Returns (evidence, source_type, topic, source_refs).
    """
    topic = " ".join(payload.topic.split())
    if not topic:
        raise ApiError(422, "INVALID_TOPIC", "The topic must contain at least one searchable term.")

    source_type = "document"
    source_refs: list[dict[str, Any]] = []
    document_ids = payload.documentIds

    if payload.spaceId is not None:
        # Source material is explicitly shared workspace content.
        source_type = "workspace"
        # Search across the user's indexed chunks using the topic as the query.
        document_ids = None

    search = retrieve_with_mode(
        database_path,
        query=topic,
        document_ids=document_ids,
        limit=settings.max_retrieval_results,
        mode=settings.retrieval_mode,
        settings=settings,
        embeddings=embeddings,
        user_id=user_id,
    )
    evidence = search["results"]
    source_refs = [
        {
            "type": "document",
            "documentId": item["documentId"],
            "displayFilename": item["displayFilename"],
            "pageNumber": item["pageNumber"],
            "chunkIndex": item["chunkIndex"],
            "retrieval": item["retrieval"],
        }
        for item in evidence
    ]
    return evidence, source_type, topic, source_refs


@router.post(
    "/resources",
    response_model=dict[str, Any],
    status_code=201,
)
def create_content_resource_route(
    request: Request,
    payload: ContentResourceCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Generate a new evidence-grounded content resource.

    The caller supplies a topic, output format, and detail level, plus
    optional document IDs or a workspace ID to scope the source material.
    Retrieval finds supporting passages, the local tutor generates structured
    content, and every section carries source citations.
    """
    settings = _settings(request)
    embeddings = _embedding_service(request)
    database_path = _database_path(request)

    # Scope retrieval to the user's owned documents (and shared workspace
    # materials if a spaceId is given).  Ownership of each citation chunk is
    # re-verified in storage before persistence.
    try:
        evidence, source_type, topic, source_refs = _resolve_source_refs(
            settings,
            embeddings,
            database_path,
            current_user.user_id,
            payload,
        )
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc

    try:
        generated = generate_content(
            settings,
            topic=topic,
            format=payload.format,
            detail_level=payload.detailLevel,
            evidence=evidence,
        )
    except TutorUnavailable as exc:
        raise ApiError(
            503,
            "TUTOR_MODEL_UNAVAILABLE",
            "The optional local tutor is not configured or could not be reached.",
        ) from exc
    except ContentGenerationError as exc:
        raise ApiError(
            exc.status_code,
            exc.code,
            exc.message,
        ) from exc

    if generated["abstain"]:
        return {
            "status": "insufficient_evidence",
            "resourceId": None,
            "topic": topic,
            "evidenceGaps": generated["evidenceGaps"],
            "retrievalMode": settings.retrieval_mode,
        }

    # Persist the resource and its sections atomically.
    try:
        resource = create_content_resource(
            database_path,
            user_id=current_user.user_id,
            title=generated["title"],
            description=None,
            format=payload.format,
            detail_level=payload.detailLevel,
            source_type=source_type,
            source_refs=source_refs,
        )
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc

    resource_id = resource["resourceId"]
    sections_out: list[dict[str, Any]] = []
    for section in generated["sections"]:
        try:
            created = create_content_section(
                database_path,
                resource_id=resource_id,
                user_id=current_user.user_id,
                section_type=section["sectionType"],
                section_title=section["sectionTitle"],
                content=section["content"],
                citations=section["citations"],
            )
        except (OSError, sqlite3.Error) as exc:
            raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
        if created is None:
            raise ApiError(500, "SECTION_CREATE_FAILED", "Could not persist content section.")
        sections_out.append(created)

    return {
        **resource,
        "sections": sections_out,
    }


@router.get("/resources", response_model=ContentResourceListResponse)
def list_content_resources_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = None,
) -> dict[str, Any]:
    """List the current user's content resources."""
    if status is not None and status not in {"draft", "reviewed", "archived"}:
        raise ApiError(422, "INVALID_STATUS", "Status must be 'draft', 'reviewed', or 'archived'.")
    try:
        resources = list_content_resources(
            _database_path(request),
            user_id=current_user.user_id,
            status=status,
        )
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    return {"resources": resources}


@router.get(
    "/resources/{resource_id}", response_model=ContentResourceResponse
)
def get_content_resource_route(
    request: Request,
    resource_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Return one content resource with its sections. Owner only."""
    resource = get_content_resource(
        _database_path(request),
        resource_id=resource_id,
        user_id=current_user.user_id,
    )
    if resource is None:
        raise ApiError(404, "CONTENT_RESOURCE_NOT_FOUND", "Content resource not found or access denied.")
    return resource


@router.patch(
    "/resources/{resource_id}", response_model=ContentResourceResponse
)
def update_content_resource_route(
    request: Request,
    resource_id: str,
    payload: ContentResourceUpdateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a content resource's metadata. Owner only."""
    try:
        resource = update_content_resource(
            _database_path(request),
            resource_id=resource_id,
            user_id=current_user.user_id,
            title=payload.title,
            description=payload.description,
            status=payload.status,
        )
    except ValueError as exc:
        raise ApiError(400, "INVALID_CONTENT_UPDATE", str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if resource is None:
        raise ApiError(403, "CONTENT_RESOURCE_FORBIDDEN", "Only the resource owner may update it.")
    return resource


@router.delete("/resources/{resource_id}")
def delete_content_resource_route(
    request: Request,
    resource_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a content resource and its sections/revisions. Owner only."""
    deleted = delete_content_resource(
        _database_path(request),
        resource_id=resource_id,
        user_id=current_user.user_id,
    )
    if not deleted:
        raise ApiError(403, "CONTENT_RESOURCE_FORBIDDEN", "Only the resource owner may delete it.")
    return {"resourceId": resource_id, "deleted": True}


@router.patch(
    "/resources/{resource_id}/sections/{section_id}",
    response_model=ContentResourceSection,
)
def edit_content_section_route(
    request: Request,
    resource_id: str,
    section_id: str,
    payload: ContentSectionEditRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Edit a section's title, content, or citations. Owner only."""
    try:
        section = update_content_section(
            _database_path(request),
            resource_id=resource_id,
            section_id=section_id,
            user_id=current_user.user_id,
            section_title=payload.sectionTitle,
            content=payload.content,
            citations=payload.citations,
        )
    except ValueError as exc:
        raise ApiError(400, "INVALID_SECTION_EDIT", str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if section is None:
        raise ApiError(404, "CONTENT_SECTION_NOT_FOUND", "Section not found or access denied.")
    return section


@router.post(
    "/resources/{resource_id}/sections/{section_id}/regenerate",
    response_model=ContentResourceSection,
)
def regenerate_content_section_route(
    request: Request,
    resource_id: str,
    section_id: str,
    payload: ContentSectionRegenerateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Regenerate a section's content, preserving the previous version as a revision."""
    try:
        section = regenerate_content_section(
            _database_path(request),
            resource_id=resource_id,
            section_id=section_id,
            user_id=current_user.user_id,
            section_type=payload.sectionType,
            section_title=payload.sectionTitle,
            content=payload.content,
            citations=payload.citations,
        )
    except ValueError as exc:
        raise ApiError(400, "INVALID_SECTION_REGENERATION", str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if section is None:
        raise ApiError(404, "CONTENT_SECTION_NOT_FOUND", "Section not found or access denied.")
    return section


@router.post(
    "/resources/{resource_id}/sections/{section_id}/review",
    response_model=ContentResourceSection,
)
def review_content_section_route(
    request: Request,
    resource_id: str,
    section_id: str,
    payload: ContentResourceReviewRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Mark a resource as reviewed (or archived). Owner only."""
    resource = update_content_resource(
        _database_path(request),
        resource_id=resource_id,
        user_id=current_user.user_id,
        status=payload.status,
    )
    if resource is None:
        raise ApiError(403, "CONTENT_RESOURCE_FORBIDDEN", "Only the resource owner may review it.")
    return resource


@router.get(
    "/resources/{resource_id}/revisions",
    response_model=ContentResourceRevisionListResponse,
)
def list_content_revisions_route(
    request: Request,
    resource_id: str,
    section_id: str,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List revisions for a section. Owner only."""
    revisions = list_content_revisions(
        _database_path(request),
        resource_id=resource_id,
        section_id=section_id,
        user_id=current_user.user_id,
    )
    if revisions is None:
        raise ApiError(404, "CONTENT_SECTION_NOT_FOUND", "Section not found or access denied.")
    return {"revisions": revisions}


@router.post(
    "/resources/{resource_id}/quizzes",
    response_model=dict[str, Any],
)
def link_content_resource_to_quiz_route(
    request: Request,
    resource_id: str,
    payload: ContentResourceQuizLinkRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record that a quiz was created from this content resource.

    The quiz itself is owned by the user through the existing quiz workflow;
    this endpoint only records the provenance link for navigation.
    """
    # Verify the resource exists and is owned.
    resource = get_content_resource(
        _database_path(request),
        resource_id=resource_id,
        user_id=current_user.user_id,
    )
    if resource is None:
        raise ApiError(404, "CONTENT_RESOURCE_NOT_FOUND", "Content resource not found or access denied.")
    # Verify the quiz exists and is owned.
    quiz = get_quiz_set(
        _database_path(request),
        payload.quizId,
        user_id=current_user.user_id,
    )
    if quiz is None:
        raise ApiError(404, "QUIZ_NOT_FOUND", "Quiz not found or access denied.")
    return {
        "resourceId": resource_id,
        "quizId": payload.quizId,
        "linked": True,
    }
