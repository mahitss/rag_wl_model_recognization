"""Versioned system, document, retrieval, and evidence-only answer routes."""

import sqlite3
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, File, Request, UploadFile
from fastapi.responses import JSONResponse

from app.config import Settings
from app.diagrams.catalog import DIAGRAMS, get_diagram, resolve_region
from app.embeddings.service import EmbeddingService, ModelUnavailable
from app.errors import ApiError
from app.ingestion.pdf import ingest_pdf
from app.retrieval.service import answer_from_evidence, retrieve_with_mode
from app.schemas import (
    AnswerRequest,
    AnswerResponse,
    DeleteDocumentResponse,
    DiagramConceptResponse,
    DiagramEvidenceRequest,
    DiagramEvidenceResponse,
    DiagramGeometry,
    DiagramRegionResponse,
    DiagramResponse,
    DiagramsResponse,
    DocumentResponse,
    DocumentsResponse,
    EmbeddingStatusResponse,
    HealthResponse,
    ReadinessResponse,
    RebuildEmbeddingsRequest,
    RebuildEmbeddingsResponse,
    RetrievalRequest,
    RetrievalResponse,
)
from app.storage import delete_document, get_document, list_documents

router = APIRouter(prefix="/api/v1")


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _database_path(request: Request) -> Path:
    return _settings(request).database_path


def _embedding_service(request: Request) -> EmbeddingService:
    return request.app.state.embedding_service


def _safe_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "documentId": document["document_id"],
        "displayFilename": document["display_name"],
        "sha256": document["sha256"],
        "byteSize": document["byte_size"],
        "pageCount": document["page_count"],
        "chunkCount": document["chunk_count"],
        "createdAt": document["created_at"],
        "parserVersion": document["parser_version"],
        "status": "indexed",
    }


def _database_call(callable_, *args):
    try:
        return callable_(*args)
    except ApiError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "wonderlearn-api",
        "capabilities": {
            "document_ingestion": True,
            "retrieval": True,
            "model_inference": False,
        },
    }


@router.get("/ready", response_model=ReadinessResponse, tags=["system"])
def readiness(request: Request) -> JSONResponse:
    from app.storage import check_storage

    checks = check_storage(_database_path(request))
    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@router.post("/documents", status_code=201, response_model=DocumentResponse, tags=["documents"])
def upload_document(request: Request, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    settings = _settings(request)
    metadata = ingest_pdf(
        file.file,
        filename=file.filename,
        database_path=settings.database_path,
        settings=settings,
    )
    response = _safe_document(metadata)  # type: ignore[arg-type]
    response["embeddingStatus"] = "not_requested"
    if settings.retrieval_mode != "lexical":
        try:
            result = _embedding_service(request).rebuild(settings.database_path)
            response["embeddingStatus"] = "indexed" if result["complete"] else "incomplete"
        except ModelUnavailable:
            response["embeddingStatus"] = "unavailable"
        except (OSError, sqlite3.Error, ValueError):
            response["embeddingStatus"] = "incomplete"
    return response


@router.get("/documents", response_model=DocumentsResponse, tags=["documents"])
def documents(request: Request) -> dict[str, Any]:
    rows = _database_call(list_documents, _database_path(request))
    return {"documents": [_safe_document(row) for row in rows]}


@router.get("/documents/{document_id}", response_model=DocumentResponse, tags=["documents"])
def document(request: Request, document_id: str) -> dict[str, Any]:
    row = _database_call(get_document, _database_path(request), document_id)
    if row is None:
        raise ApiError(404, "DOCUMENT_NOT_FOUND", "The requested document was not found.")
    return _safe_document(row)


@router.delete(
    "/documents/{document_id}", response_model=DeleteDocumentResponse, tags=["documents"]
)
def remove_document(request: Request, document_id: str) -> dict[str, Any]:
    deleted = _database_call(delete_document, _database_path(request), document_id)
    if not deleted:
        raise ApiError(404, "DOCUMENT_NOT_FOUND", "The requested document was not found.")
    return {"documentId": document_id, "deleted": True}


@router.post("/retrieve", response_model=RetrievalResponse, tags=["retrieval"])
def retrieve_evidence(request: Request, payload: RetrievalRequest) -> dict[str, Any]:
    settings = _settings(request)
    if payload.limit > settings.max_retrieval_results:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"The result limit cannot exceed {settings.max_retrieval_results}.",
        )
    return retrieve_with_mode(
        settings.database_path,
        query=payload.query,
        document_ids=payload.document_ids,
        limit=payload.limit,
        mode=payload.mode or settings.retrieval_mode,
        settings=settings,
        embeddings=_embedding_service(request),
    )


@router.post("/answers", response_model=AnswerResponse, tags=["retrieval"])
def answer(request: Request, payload: AnswerRequest) -> dict[str, Any]:
    settings = _settings(request)
    if payload.limit > settings.max_retrieval_results:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"The result limit cannot exceed {settings.max_retrieval_results}.",
        )
    search = retrieve_with_mode(
        settings.database_path,
        query=payload.question,
        document_ids=payload.document_ids,
        limit=payload.limit,
        mode=payload.mode or settings.retrieval_mode,
        settings=settings,
        embeddings=_embedding_service(request),
    )
    response = answer_from_evidence(payload.question, search["results"])
    response["retrievalMode"] = search["retrievalMode"]
    response["fallbackReason"] = search["fallbackReason"]
    return response


@router.get("/diagrams", response_model=DiagramsResponse, tags=["diagrams"])
def diagrams() -> dict[str, Any]:
    response = []
    for diagram in DIAGRAMS.values():
        concepts = {concept.concept_id: concept for concept in diagram.concepts}
        response.append(
            DiagramResponse(
                diagram_id=diagram.diagram_id,
                title=diagram.title,
                description=diagram.description,
                image_url=diagram.image_url,
                image_aspect_ratio=diagram.image_aspect_ratio,
                regions=[
                    DiagramRegionResponse(
                        region_id=region.region_id,
                        concept_id=region.concept_id,
                        display_name=concepts[region.concept_id].display_name,
                        aliases=list(concepts[region.concept_id].aliases),
                        geometry=DiagramGeometry(
                            x=region.x,
                            y=region.y,
                            width=region.width,
                            height=region.height,
                        ),
                    )
                    for region in diagram.regions
                ],
            )
        )
    return {"diagrams": response}


@router.post(
    "/diagrams/{diagram_id}/regions/{region_id}/evidence",
    response_model=DiagramEvidenceResponse,
    tags=["diagrams"],
)
def diagram_region_evidence(
    request: Request,
    diagram_id: str,
    region_id: str,
    payload: DiagramEvidenceRequest,
) -> dict[str, Any]:
    diagram = get_diagram(diagram_id)
    if diagram is None:
        raise ApiError(404, "DIAGRAM_NOT_FOUND", "The requested diagram is not supported.")
    resolved = resolve_region(diagram_id, region_id)
    if resolved is None:
        raise ApiError(
            404,
            "DIAGRAM_REGION_NOT_FOUND",
            "The selected diagram region was not found.",
        )

    _, _, concept = resolved
    settings = _settings(request)
    if payload.limit > settings.max_retrieval_results:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"The result limit cannot exceed {settings.max_retrieval_results}.",
        )
    result = retrieve_with_mode(
        settings.database_path,
        query=concept.retrieval_query,
        document_ids=None,
        limit=payload.limit,
        mode=settings.retrieval_mode,
        settings=settings,
        embeddings=_embedding_service(request),
    )
    citations = result["results"]
    return {
        "status": "evidence_found" if citations else "insufficient_evidence",
        "diagramId": diagram_id,
        "regionId": region_id,
        "concept": DiagramConceptResponse(
            concept_id=concept.concept_id,
            display_name=concept.display_name,
            aliases=list(concept.aliases),
        ),
        "results": citations,
        "retrievalMode": result["retrievalMode"],
        "fallbackReason": result["fallbackReason"],
    }


@router.get("/embeddings/status", response_model=EmbeddingStatusResponse, tags=["embeddings"])
def embedding_status(request: Request) -> dict[str, Any]:
    service = _embedding_service(request)
    try:
        service.load_model()
    except ModelUnavailable:
        pass
    try:
        return service.status(_database_path(request))
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503, "STORAGE_UNAVAILABLE", "Local embedding storage is unavailable."
        ) from exc


@router.post(
    "/embeddings/rebuild",
    response_model=RebuildEmbeddingsResponse,
    tags=["embeddings"],
)
def rebuild_embeddings(
    request: Request,
    payload: Annotated[RebuildEmbeddingsRequest | None, Body()] = None,
) -> dict[str, Any]:
    try:
        return _embedding_service(request).rebuild(
            _database_path(request), force=payload.force if payload else False
        )
    except ModelUnavailable as exc:
        raise ApiError(
            503,
            "EMBEDDING_MODEL_UNAVAILABLE",
            "The local embedding model is unavailable. Install and cache the configured model.",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503, "STORAGE_UNAVAILABLE", "Local embedding storage is unavailable."
        ) from exc
    except ValueError as exc:
        raise ApiError(
            503, "EMBEDDING_FAILED", "The embedding index could not be rebuilt."
        ) from exc


def make_error_response(status_code: int, code: str, message: str, details: object = None):
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "requestId": uuid.uuid4().hex,
                "details": details,
            }
        },
    )
