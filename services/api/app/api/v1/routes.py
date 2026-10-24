"""Versioned system, document, retrieval, and evidence-only answer routes."""
# ruff: noqa: B008,E501

import asyncio
import hashlib
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.auth import AuthUser, get_current_user, require_auth
from app.config import Settings
from app.diagrams.catalog import DIAGRAMS, get_diagram, resolve_region
from app.embeddings.service import EmbeddingService, ModelUnavailable
from app.errors import ApiError
from app.ingestion.image import (
    normalize_image,
    recognize_region,
    recognize_text,
    safe_image_filename,
)
from app.ingestion.pdf import ingest_pdf
from app.knowledge_graph import extract_chunk
from app.learning_analytics import build_analytics
from app.observability import OperationalMetrics
from app.quiz import evaluate_answer, generate_quiz_questions
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
    ImageEvidenceResponse,
    ImageRegionCreateRequest,
    ImageRegionCreateResponse,
    ImageTutorRequest,
    ImageTutorResponse,
    KnowledgeGraphConceptDetail,
    KnowledgeGraphConceptList,
    KnowledgeGraphExtractRequest,
    KnowledgeGraphExtractResponse,
    LearningAnalyticsResponse,
    LearningImageDetailResponse,
    LearningImageListResponse,
    LearningProgressResponse,
    ProductAnalyticsEventRequest,
    ProductAnalyticsSummary,
    QuizAttemptRequest,
    QuizAttemptResponse,
    QuizGenerateRequest,
    QuizSetResponse,
    ReadinessResponse,
    RebuildEmbeddingsRequest,
    RebuildEmbeddingsResponse,
    RetrievalRequest,
    RetrievalResponse,
    StudyCoachActionRequest,
    StudyCoachExplanationResponse,
    StudyCoachPlanResponse,
    StudyCoachRecommendation,
    StudySessionRequest,
    StudySessionResponse,
    TutorAnswerRequest,
    TutorAnswerResponse,
    TutorStatusResponse,
)
from app.storage import (
    complete_study_session,
    create_learning_image_region,
    create_quiz_set,
    delete_document,
    delete_learning_image,
    get_document,
    get_knowledge_concept,
    get_learning_analytics_data,
    get_learning_image,
    get_learning_image_detail,
    get_learning_image_region,
    get_learning_progress,
    get_or_create_study_coach_plan,
    get_product_analytics_snapshot,
    get_quiz_question,
    get_quiz_set,
    knowledge_graph_chunks,
    list_documents,
    list_knowledge_concepts,
    list_learning_images,
    prune_product_events,
    record_product_event,
    record_quiz_attempt,
    replace_image_region_citations,
    start_study_session,
    store_knowledge_extraction,
    store_learning_image,
    update_study_coach_recommendation,
    verify_knowledge_relationship,
)
from app.study_coach import build_daily_recommendations
from app.tutor import InvalidTutorResponse, TutorUnavailable, generate_grounded_answer

router = APIRouter(prefix="/api/v1")


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _database_path(request: Request) -> Path:
    return _settings(request).database_path


def _embedding_service(request: Request) -> EmbeddingService:
    return request.app.state.embedding_service


def _current_user(request: Request) -> AuthUser | None:
    """Return the authenticated user, or None if not authenticated."""
    return get_current_user(request)


def _require_user(request: Request) -> AuthUser:
    """Require an authenticated user, raising 401 if not authenticated."""
    return require_auth(request)


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


def _database_call(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except ApiError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc


async def _grounded_tutor_response(
    settings: Settings,
    *,
    question: str,
    concept: str | None,
    evidence: list[dict[str, Any]],
    retrieval_mode: str,
    fallback_reason: str | None,
) -> dict[str, Any]:
    if not evidence:
        return {
            "status": "insufficient_evidence",
            "answer": None,
            "citations": [],
            "retrievalMode": retrieval_mode,
            "fallbackReason": fallback_reason,
        }
    try:
        generated = await asyncio.to_thread(
            generate_grounded_answer,
            settings,
            question=question,
            concept=concept,
            evidence=evidence,
        )
    except TutorUnavailable as exc:
        raise ApiError(
            503,
            "TUTOR_MODEL_UNAVAILABLE",
            "The optional local tutor is not configured or could not be reached.",
        ) from exc
    except InvalidTutorResponse as exc:
        raise ApiError(
            502,
            "TUTOR_INVALID_RESPONSE",
            "The local tutor returned an invalid response. Try again or check its status.",
        ) from exc
    return {
        "status": "answered" if generated["answer"] is not None else "insufficient_evidence",
        "answer": generated["answer"],
        "citations": generated["citations"],
        "retrievalMode": retrieval_mode,
        "fallbackReason": fallback_reason,
    }


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(request: Request) -> dict[str, Any]:
    settings = _settings(request)
    return {
        "status": "ok",
        "service": "wonderlearn-api",
        "version": "0.1.0",
        "environment": settings.environment,
        "capabilities": {
            "document_ingestion": True,
            "retrieval": True,
            "model_inference": settings.tutor_configured,
        },
    }


@router.get("/product-analytics", response_model=ProductAnalyticsSummary, tags=["analytics"])
def product_analytics(request: Request) -> dict[str, Any]:
    settings = _settings(request)
    current_user = require_auth(request)
    if not settings.analytics_enabled:
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "enabled": False,
            "window_days": settings.product_analytics_retention_days,
            "totals": {
                "event_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "feature_count": 0,
            },
            "features": [],
            "trends": [],
            "privacy": {
                "retention_days": settings.product_analytics_retention_days,
                "raw_content": "not_collected",
                "identifiers": "user_id_only_for_personalized_views",
            },
        }
    prune_product_events(
        _database_path(request), retention_days=settings.product_analytics_retention_days
    )
    snapshot = get_product_analytics_snapshot(
        _database_path(request),
        user_id=current_user.user_id,
        window_days=settings.product_analytics_retention_days,
    )
    snapshot["privacy"]["retention_days"] = settings.product_analytics_retention_days
    snapshot["enabled"] = True
    return snapshot


@router.post("/product-analytics/events", tags=["analytics"])
def record_product_analytics_event(
    request: Request,
    payload: ProductAnalyticsEventRequest,
) -> dict[str, str]:
    current_user = require_auth(request)
    settings = _settings(request)
    if not settings.analytics_enabled:
        return {"status": "disabled"}
    record_product_event(
        _database_path(request),
        user_id=current_user.user_id,
        event_name=payload.event_name,
        feature=payload.feature,
        result=payload.result,
        category="error" if payload.result == "failure" else "feature",
        metadata=payload.metadata,
    )
    return {"status": "recorded"}


@router.get("/ready", response_model=ReadinessResponse, tags=["system"])
def readiness(request: Request) -> JSONResponse:
    from app.storage import check_storage

    checks = check_storage(_database_path(request))
    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@router.get("/metrics", tags=["system"])
def metrics(request: Request) -> dict[str, Any]:
    metrics_state: OperationalMetrics = request.app.state.metrics
    return metrics_state.snapshot()


@router.get("/diagnostics", tags=["system"])
def diagnostics(request: Request) -> JSONResponse:
    settings = _settings(request)
    if not settings.diagnostics_token:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "DIAGNOSTICS_DISABLED",
                    "message": "Diagnostics are not enabled.",
                }
            },
        )
    provided = request.headers.get("X-Diagnostics-Token")
    if not provided or not hasattr(request.app.state, "metrics"):
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "DIAGNOSTICS_UNAUTHORIZED", "message": "Unauthorized."}},
        )
    if provided != settings.diagnostics_token:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "DIAGNOSTICS_UNAUTHORIZED", "message": "Unauthorized."}},
        )
    return JSONResponse(content={"status": "ok", "metrics": request.app.state.metrics.snapshot()})


@router.post("/documents", status_code=201, response_model=DocumentResponse, tags=["documents"])
def upload_document(
    request: Request,
    file: Annotated[UploadFile, File()],
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    settings = _settings(request)
    metadata = ingest_pdf(
        file.file,
        filename=file.filename,
        database_path=settings.database_path,
        settings=settings,
        user_id=current_user.user_id,
    )
    response = _safe_document(metadata)  # type: ignore[arg-type]
    response["embeddingStatus"] = "not_requested"
    if settings.retrieval_mode != "lexical":
        try:
            result = _embedding_service(request).rebuild(
                settings.database_path,
                user_id=current_user.user_id,
            )
            response["embeddingStatus"] = "indexed" if result["complete"] else "incomplete"
        except ModelUnavailable:
            response["embeddingStatus"] = "unavailable"
        except (OSError, sqlite3.Error, ValueError):
            response["embeddingStatus"] = "incomplete"
    return response


@router.get("/documents", response_model=DocumentsResponse, tags=["documents"])
def documents(request: Request, current_user: AuthUser = Depends(require_auth)) -> dict[str, Any]:
    rows = _database_call(list_documents, _database_path(request), user_id=current_user.user_id)
    return {"documents": [_safe_document(row) for row in rows]}


@router.get("/documents/{document_id}", response_model=DocumentResponse, tags=["documents"])
def document(
    request: Request, document_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    row = _database_call(
        get_document, _database_path(request), document_id, user_id=current_user.user_id
    )
    if row is None:
        raise ApiError(404, "DOCUMENT_NOT_FOUND", "The requested document was not found.")
    return _safe_document(row)


@router.delete(
    "/documents/{document_id}", response_model=DeleteDocumentResponse, tags=["documents"]
)
def remove_document(
    request: Request, document_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    deleted = _database_call(
        delete_document, _database_path(request), document_id, user_id=current_user.user_id
    )
    if not deleted:
        raise ApiError(404, "DOCUMENT_NOT_FOUND", "The requested document was not found.")
    return {"documentId": document_id, "deleted": True}


@router.post(
    "/images", response_model=LearningImageDetailResponse, status_code=201, tags=["images"]
)
async def upload_learning_image(
    request: Request,
    file: Annotated[UploadFile, File()],
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    settings = _settings(request)
    content = await file.read(settings.max_image_upload_bytes + 1)
    normalized = await asyncio.to_thread(
        normalize_image,
        content,
        max_bytes=settings.max_image_upload_bytes,
        max_dimension=settings.max_image_dimension,
        max_pixels=settings.max_image_pixels,
    )
    ocr_status, extracted_text = await asyncio.to_thread(recognize_text, normalized.content)
    metadata = {
        "imageId": f"img_{uuid.uuid4().hex}",
        "displayFilename": safe_image_filename(file.filename),
        "contentType": normalized.content_type,
        "width": normalized.width,
        "height": normalized.height,
        "sha256": normalized.sha256,
        "createdAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "ocrStatus": ocr_status,
        "extractedText": extracted_text,
    }
    _database_call(
        store_learning_image,
        settings.database_path,
        metadata,
        normalized.content,
        user_id=current_user.user_id,
    )
    return _database_call(
        get_learning_image_detail,
        settings.database_path,
        metadata["imageId"],
        user_id=current_user.user_id,
    )


@router.get("/images", response_model=LearningImageListResponse, tags=["images"])
def learning_images(
    request: Request, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    return {
        "images": _database_call(
            list_learning_images, _database_path(request), user_id=current_user.user_id
        )
    }


@router.get("/images/{image_id}", response_model=LearningImageDetailResponse, tags=["images"])
def learning_image(
    request: Request, image_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    result = _database_call(
        get_learning_image_detail, _database_path(request), image_id, user_id=current_user.user_id
    )
    if result is None:
        raise ApiError(404, "IMAGE_NOT_FOUND", "The requested image was not found.")
    return result


@router.get("/images/{image_id}/content", tags=["images"])
def learning_image_content(
    request: Request, image_id: str, current_user: AuthUser = Depends(require_auth)
) -> Response:
    image = _database_call(
        get_learning_image, _database_path(request), image_id, user_id=current_user.user_id
    )
    if image is None:
        raise ApiError(404, "IMAGE_NOT_FOUND", "The requested image was not found.")
    return Response(
        image["imageBytes"],
        media_type=image["contentType"],
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/images/{image_id}", tags=["images"])
def remove_learning_image(
    request: Request, image_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    deleted = _database_call(
        delete_learning_image, _database_path(request), image_id, user_id=current_user.user_id
    )
    if not deleted:
        raise ApiError(404, "IMAGE_NOT_FOUND", "The requested image was not found.")
    return {"imageId": image_id, "deleted": True}


@router.post(
    "/images/{image_id}/regions",
    response_model=ImageRegionCreateResponse,
    status_code=201,
    tags=["images"],
)
def create_image_region(
    request: Request,
    image_id: str,
    payload: ImageRegionCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    settings = _settings(request)
    image = _database_call(
        get_learning_image, settings.database_path, image_id, user_id=current_user.user_id
    )
    if image is None:
        raise ApiError(404, "IMAGE_NOT_FOUND", "The requested image was not found.")
    concept = None
    if payload.diagram_id and payload.concept_id:
        diagram = get_diagram(payload.diagram_id)
        if diagram is None:
            raise ApiError(404, "DIAGRAM_NOT_FOUND", "The requested curated diagram was not found.")
        concept = next(
            (item for item in diagram.concepts if item.concept_id == payload.concept_id), None
        )
        if concept is None:
            raise ApiError(404, "CONCEPT_NOT_FOUND", "The selected curated concept was not found.")
    ocr_status, extracted_text = recognize_region(
        image["imageBytes"], payload.geometry.model_dump()
    )
    region = {
        "regionId": f"imr_{uuid.uuid4().hex}",
        "imageId": image_id,
        "geometry": payload.geometry.model_dump(),
        "ocrStatus": ocr_status,
        "extractedText": extracted_text,
        "diagramId": payload.diagram_id,
        "conceptId": payload.concept_id,
        "conceptName": concept.display_name if concept else None,
        "createdAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _database_call(
        create_learning_image_region,
        settings.database_path,
        region,
        user_id=current_user.user_id,
    )
    return {
        "regionId": region["regionId"],
        "geometry": region["geometry"],
        "ocrStatus": region["ocrStatus"],
        "extractedText": region["extractedText"],
        "diagramId": region["diagramId"],
        "conceptId": region["conceptId"],
        "conceptName": region["conceptName"],
        "citations": [],
    }


def _image_region_context(
    request: Request, image_id: str, region_id: str, current_user: AuthUser = Depends(require_auth)
) -> tuple[dict[str, Any], dict[str, Any], Any | None]:
    settings = _settings(request)
    image = _database_call(
        get_learning_image, settings.database_path, image_id, user_id=current_user.user_id
    )
    if image is None:
        raise ApiError(404, "IMAGE_NOT_FOUND", "The requested image was not found.")
    region = _database_call(
        get_learning_image_region,
        settings.database_path,
        image_id,
        region_id,
        user_id=current_user.user_id,
    )
    if region is None:
        raise ApiError(404, "IMAGE_REGION_NOT_FOUND", "The selected image region was not found.")
    concept = None
    if region["diagramId"] and region["conceptId"]:
        diagram = get_diagram(region["diagramId"])
        if diagram is not None:
            concept = next(
                (item for item in diagram.concepts if item.concept_id == region["conceptId"]), None
            )
    return image, region, concept


def _image_region_search(
    request: Request,
    region: dict[str, Any],
    *,
    question: str | None,
    limit: int,
    mode: str | None,
    current_user: AuthUser,
) -> dict[str, Any]:
    settings = _settings(request)
    if limit > settings.max_retrieval_results:
        raise ApiError(422, "INVALID_REQUEST", "The result limit exceeds the configured maximum.")
    terms = [question or "", (region["extractedText"] or "")[:1_000]]
    concept = None
    if region["diagramId"] and region["conceptId"]:
        diagram = get_diagram(region["diagramId"])
        if diagram:
            concept = next(
                (item for item in diagram.concepts if item.concept_id == region["conceptId"]), None
            )
            if concept:
                terms.append(concept.retrieval_query)
    query = " ".join(term for term in terms if term).strip()
    if not query:
        return {
            "results": [],
            "retrievalMode": mode or settings.retrieval_mode,
            "fallbackReason": "no_region_text_or_question",
        }
    return retrieve_with_mode(
        settings.database_path,
        query=query[:2_000],
        document_ids=None,
        limit=limit,
        mode=mode or settings.retrieval_mode,
        settings=settings,
        embeddings=_embedding_service(request),
        user_id=current_user.user_id,
    )


@router.post(
    "/images/{image_id}/regions/{region_id}/evidence",
    response_model=ImageEvidenceResponse,
    tags=["images"],
)
def image_region_evidence(
    request: Request,
    image_id: str,
    region_id: str,
    payload: DiagramEvidenceRequest | None = None,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    payload = payload or DiagramEvidenceRequest()
    _, region, concept = _image_region_context(request, image_id, region_id, current_user)
    search = _image_region_search(
        request, region, question=None, limit=payload.limit, mode=None, current_user=current_user
    )
    _database_call(
        replace_image_region_citations,
        _database_path(request),
        region_id,
        search["results"],
        user_id=current_user.user_id,
    )
    return {
        "status": "evidence_found" if search["results"] else "insufficient_evidence",
        "imageId": image_id,
        "regionId": region_id,
        "geometry": region["geometry"],
        "extractedText": region["extractedText"],
        "ocrStatus": region["ocrStatus"],
        "conceptName": concept.display_name if concept else None,
        "results": search["results"],
        "retrievalMode": search["retrievalMode"],
        "fallbackReason": search["fallbackReason"],
    }


@router.post(
    "/images/{image_id}/regions/{region_id}/tutor",
    response_model=ImageTutorResponse,
    tags=["images", "tutor"],
)
async def image_region_tutor(
    request: Request,
    image_id: str,
    region_id: str,
    payload: ImageTutorRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    _, region, concept = _image_region_context(request, image_id, region_id, current_user)
    search = _image_region_search(
        request,
        region,
        question=payload.question,
        limit=payload.limit,
        mode=payload.mode,
        current_user=current_user,
    )
    _database_call(
        replace_image_region_citations,
        _database_path(request),
        region_id,
        search["results"],
        user_id=current_user.user_id,
    )
    generated = await _grounded_tutor_response(
        _settings(request),
        question=payload.question,
        concept=concept.display_name if concept else None,
        evidence=search["results"],
        retrieval_mode=search["retrievalMode"],
        fallback_reason=search["fallbackReason"],
    )
    return {
        "question": payload.question,
        **generated,
        "imageId": image_id,
        "regionId": region_id,
        "geometry": region["geometry"],
        "extractedText": region["extractedText"],
        "ocrStatus": region["ocrStatus"],
        "conceptName": concept.display_name if concept else None,
    }


@router.post("/retrieve", response_model=RetrievalResponse, tags=["retrieval"])
def retrieve_evidence(
    request: Request, payload: RetrievalRequest, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
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
        user_id=current_user.user_id,
    )


@router.post("/answers", response_model=AnswerResponse, tags=["retrieval"])
def answer(
    request: Request, payload: AnswerRequest, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
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
        user_id=current_user.user_id,
    )
    response = answer_from_evidence(payload.question, search["results"])
    response["retrievalMode"] = search["retrievalMode"]
    response["fallbackReason"] = search["fallbackReason"]
    return response


@router.get("/tutor/status", response_model=TutorStatusResponse, tags=["tutor"])
def tutor_status(request: Request) -> dict[str, Any]:
    settings = _settings(request)
    return {
        "configured": settings.tutor_configured,
        "modelId": settings.tutor_model_id if settings.tutor_configured else None,
    }


@router.post("/tutor/answers", response_model=TutorAnswerResponse, tags=["tutor"])
async def tutor_answer(
    request: Request, payload: TutorAnswerRequest, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    settings = _settings(request)
    if payload.limit > settings.max_retrieval_results:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"The result limit cannot exceed {settings.max_retrieval_results}.",
        )

    concept = None
    concept_name = None
    query = payload.question
    if payload.diagram_id is not None and payload.concept_id is not None:
        diagram = get_diagram(payload.diagram_id)
        if diagram is None:
            raise ApiError(404, "DIAGRAM_NOT_FOUND", "The requested diagram is not supported.")
        concept = next(
            (item for item in diagram.concepts if item.concept_id == payload.concept_id), None
        )
        if concept is None:
            raise ApiError(404, "CONCEPT_NOT_FOUND", "The selected concept is not supported.")
        concept_name = concept.display_name
        query = f"{payload.question} {concept.retrieval_query}"
    elif payload.graph_concept_id is not None:
        graph_concept = _database_call(
            get_knowledge_concept,
            settings.database_path,
            payload.graph_concept_id,
            user_id=current_user.user_id,
        )
        if graph_concept is None:
            raise ApiError(404, "CONCEPT_NOT_FOUND", "The requested graph concept was not found.")
        concept_name = graph_concept["name"]
        retrieval_query = " ".join([graph_concept["name"], *graph_concept["aliases"]])
        query = f"{payload.question} {retrieval_query}"

    search = retrieve_with_mode(
        settings.database_path,
        query=query,
        document_ids=payload.document_ids,
        limit=payload.limit,
        mode=payload.mode or settings.retrieval_mode,
        settings=settings,
        embeddings=_embedding_service(request),
        user_id=current_user.user_id,
    )
    generated = await _grounded_tutor_response(
        settings,
        question=payload.question,
        concept=concept_name,
        evidence=search["results"],
        retrieval_mode=search["retrievalMode"],
        fallback_reason=search["fallbackReason"],
    )
    return {"question": payload.question, **generated}


@router.get("/knowledge-graph", response_model=KnowledgeGraphConceptList, tags=["knowledge graph"])
def knowledge_graph(
    request: Request, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    return {
        "concepts": _database_call(
            list_knowledge_concepts, _database_path(request), user_id=current_user.user_id
        )
    }


@router.post(
    "/knowledge-graph/extract",
    response_model=KnowledgeGraphExtractResponse,
    tags=["knowledge graph"],
)
async def extract_knowledge_graph(
    request: Request,
    payload: KnowledgeGraphExtractRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    settings = _settings(request)
    try:
        chunks = knowledge_graph_chunks(
            settings.database_path,
            document_ids=payload.document_ids,
            limit=payload.chunk_limit + 1,
            after_chunk_id=payload.after_chunk_id,
            user_id=current_user.user_id,
        )
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc
    if not chunks:
        return {"chunksProcessed": 0, "nextAfterChunkId": None, "concepts": []}
    has_more = len(chunks) > payload.chunk_limit
    chunks = chunks[: payload.chunk_limit]
    parsed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    try:
        for chunk in chunks:
            result = await asyncio.to_thread(extract_chunk, settings, chunk)
            parsed.append((chunk, result))
        for chunk, result in parsed:
            store_knowledge_extraction(
                settings.database_path, chunk, result, user_id=current_user.user_id
            )
    except TutorUnavailable as exc:
        raise ApiError(
            503,
            "TUTOR_MODEL_UNAVAILABLE",
            "Configure the optional local tutor to extract graph candidates.",
        ) from exc
    except InvalidTutorResponse as exc:
        raise ApiError(
            502,
            "GRAPH_INVALID_EXTRACTION",
            "The local model returned malformed or unsupported graph data.",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local graph storage is unavailable.") from exc
    return {
        "chunksProcessed": len(chunks),
        "nextAfterChunkId": chunks[-1]["chunk_id"] if has_more else None,
        "concepts": _database_call(
            list_knowledge_concepts, settings.database_path, user_id=current_user.user_id
        ),
    }


@router.get(
    "/knowledge-graph/concepts/{concept_id}",
    response_model=KnowledgeGraphConceptDetail,
    tags=["knowledge graph"],
)
def knowledge_graph_concept(
    request: Request, concept_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    result = _database_call(
        get_knowledge_concept, _database_path(request), concept_id, user_id=current_user.user_id
    )
    if result is None:
        raise ApiError(404, "CONCEPT_NOT_FOUND", "The requested graph concept was not found.")
    return result


@router.post(
    "/knowledge-graph/relationships/{relationship_id}/verify",
    tags=["knowledge graph"],
)
def verify_knowledge_graph_relationship(
    request: Request, relationship_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    verified = _database_call(
        verify_knowledge_relationship,
        _database_path(request),
        relationship_id,
        user_id=current_user.user_id,
    )
    if not verified:
        raise ApiError(404, "RELATIONSHIP_NOT_FOUND", "The candidate relationship was not found.")
    return {"relationshipId": relationship_id, "status": "human_verified"}


@router.post("/quizzes", response_model=QuizSetResponse, tags=["learning"])
async def create_quiz(
    request: Request, payload: QuizGenerateRequest, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    settings = _settings(request)
    if payload.limit > settings.max_retrieval_results:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"The evidence limit cannot exceed {settings.max_retrieval_results}.",
        )
    topic = " ".join(payload.topic.split())
    topic_id = hashlib.sha256(topic.casefold().encode("utf-8")).hexdigest()[:20]
    query = topic
    if payload.diagram_id is not None and payload.concept_id is not None:
        diagram = get_diagram(payload.diagram_id)
        if diagram is None:
            raise ApiError(404, "DIAGRAM_NOT_FOUND", "The requested diagram is not supported.")
        concept = next(
            (item for item in diagram.concepts if item.concept_id == payload.concept_id), None
        )
        if concept is None:
            raise ApiError(404, "CONCEPT_NOT_FOUND", "The selected concept is not supported.")
        query = f"{topic} {concept.retrieval_query}"

    search = retrieve_with_mode(
        settings.database_path,
        query=query,
        document_ids=payload.document_ids,
        limit=payload.limit,
        mode=payload.mode or settings.retrieval_mode,
        settings=settings,
        embeddings=_embedding_service(request),
        user_id=current_user.user_id,
    )
    if not search["results"]:
        return {
            "status": "insufficient_evidence",
            "quizId": None,
            "topicId": None,
            "topic": topic,
            "createdAt": None,
            "questions": [],
        }

    try:
        generated = await asyncio.to_thread(
            generate_quiz_questions,
            settings,
            topic=topic,
            count=payload.question_count,
            evidence=search["results"],
        )
    except TutorUnavailable as exc:
        raise ApiError(
            503,
            "TUTOR_MODEL_UNAVAILABLE",
            "The optional local tutor is not configured or could not be reached.",
        ) from exc
    except InvalidTutorResponse as exc:
        raise ApiError(
            502,
            "QUIZ_INVALID_RESPONSE",
            "The local model returned malformed or unsupported quiz questions.",
        ) from exc
    if not generated:
        return {
            "status": "insufficient_evidence",
            "quizId": None,
            "topicId": None,
            "topic": topic,
            "createdAt": None,
            "questions": [],
        }

    quiz_id = uuid.uuid4().hex
    created_at = datetime.now(UTC).isoformat()
    try:
        create_quiz_set(
            settings.database_path,
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
            user_id=current_user.user_id,
        )
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503, "STORAGE_UNAVAILABLE", "Local learning storage is unavailable."
        ) from exc
    quiz = _database_call(
        get_quiz_set, settings.database_path, quiz_id, user_id=current_user.user_id
    )
    return {"status": "ready", **quiz}


@router.get("/quizzes/{quiz_id}", response_model=QuizSetResponse, tags=["learning"])
def read_quiz(
    request: Request,
    quiz_id: str,
    current_user: AuthUser = Depends(require_auth),
    review_incorrect: bool = False,
) -> dict[str, Any]:
    quiz = _database_call(
        get_quiz_set,
        _database_path(request),
        quiz_id,
        review_incorrect=review_incorrect,
        user_id=current_user.user_id,
    )
    if quiz is None:
        raise ApiError(404, "QUIZ_NOT_FOUND", "The requested quiz was not found.")
    return {"status": "ready", **quiz}


@router.post(
    "/quizzes/{quiz_id}/questions/{question_id}/attempts",
    response_model=QuizAttemptResponse,
    tags=["learning"],
)
def submit_quiz_answer(
    request: Request,
    quiz_id: str,
    question_id: str,
    payload: QuizAttemptRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    database_path = _database_path(request)
    question = _database_call(
        get_quiz_question, database_path, question_id, user_id=current_user.user_id
    )
    if question is None or question["quiz_id"] != quiz_id:
        raise ApiError(404, "QUIZ_QUESTION_NOT_FOUND", "The requested quiz question was not found.")
    try:
        outcome = evaluate_answer(question, payload.answer)
    except ValueError as exc:
        raise ApiError(422, "INVALID_ANSWER", str(exc)) from exc
    attempt_id = uuid.uuid4().hex
    _database_call(
        record_quiz_attempt,
        database_path,
        attempt_id=attempt_id,
        question_id=question_id,
        outcome=outcome,
        created_at=datetime.now(UTC).isoformat(),
        user_id=current_user.user_id,
    )
    quiz = _database_call(get_quiz_set, database_path, quiz_id, user_id=current_user.user_id)
    reviewed = next(item for item in quiz["questions"] if item["questionId"] == question_id)
    expected_answer = question["answer_key"]
    if question["question_type"] == "multiple_choice":
        expected_answer = next(
            item["text"] for item in question["options"] if item["id"] == expected_answer
        )
    return {
        "attemptId": attempt_id,
        "questionId": question_id,
        "outcome": outcome,
        "isCorrect": True if outcome == "correct" else False if outcome == "incorrect" else None,
        "expectedAnswer": expected_answer,
        "explanation": reviewed["explanation"],
        "citations": reviewed["citations"],
        "attemptCount": reviewed["attemptCount"],
    }


@router.get("/progress", response_model=LearningProgressResponse, tags=["learning"])
def learning_progress(
    request: Request, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    return _database_call(
        get_learning_progress, _database_path(request), user_id=current_user.user_id
    )


@router.get(
    "/learning-analytics",
    response_model=LearningAnalyticsResponse,
    tags=["learning"],
)
def learning_analytics(
    request: Request, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    now = datetime.now(UTC)
    data = _database_call(
        get_learning_analytics_data, _database_path(request), user_id=current_user.user_id
    )
    return build_analytics(
        **{
            "attempts": data["attempts"],
            "sessions": data["sessions"],
            "reviews": data["reviews"],
            "topic_sources": data["topicSources"],
            "concepts": data["concepts"],
            "now": now,
        }
    )


@router.post(
    "/study-sessions", response_model=StudySessionResponse, status_code=201, tags=["learning"]
)
def begin_study_session(
    request: Request, payload: StudySessionRequest, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    topic = " ".join(payload.topic.split())
    topic_id = hashlib.sha256(topic.casefold().encode("utf-8")).hexdigest()[:20]
    return _database_call(
        start_study_session,
        _database_path(request),
        session_id=uuid.uuid4().hex,
        topic_id=topic_id,
        topic=topic,
        started_at=datetime.now(UTC).isoformat(),
        user_id=current_user.user_id,
    )


@router.post(
    "/study-sessions/{session_id}/complete",
    response_model=StudySessionResponse,
    tags=["learning"],
)
def finish_study_session(
    request: Request, session_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    session = _database_call(
        complete_study_session,
        _database_path(request),
        session_id=session_id,
        completed_at=datetime.now(UTC).isoformat(),
        user_id=current_user.user_id,
    )
    if session is None:
        raise ApiError(404, "STUDY_SESSION_NOT_FOUND", "The study session was not found.")
    return session


@router.get(
    "/study-coach/plan",
    response_model=StudyCoachPlanResponse,
    tags=["learning"],
)
def study_coach_plan(
    request: Request, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    now = datetime.now(UTC)
    plan_date = now.date().isoformat()
    database_path = _database_path(request)
    data = _database_call(get_learning_analytics_data, database_path, user_id=current_user.user_id)
    analytics = build_analytics(
        attempts=data["attempts"],
        sessions=data["sessions"],
        reviews=data["reviews"],
        topic_sources=data["topicSources"],
        concepts=data["concepts"],
        now=now,
    )
    analytics["topicSources"] = data["topicSources"]
    candidates = build_daily_recommendations(
        analytics=data | analytics,
        now=now,
        user_id=current_user.user_id,
    )
    stored = _database_call(
        get_or_create_study_coach_plan,
        database_path,
        plan_date=plan_date,
        recommendations=candidates,
        updated_at=now.isoformat(),
        user_id=current_user.user_id,
    )
    live_topics = {topic["topicId"]: topic for topic in analytics["topics"]}
    recommendations = []
    for item in stored:
        topic = live_topics.get(item["topicId"])
        citations = data["topicSources"].get(item["topicId"], [])
        # Source deletion cascades learner activity; never display a stale plan without evidence.
        if topic is None or not citations:
            continue
        recommendations.append(
            StudyCoachRecommendation(
                **item,
                citations=citations,
                quizTopic=topic["topic"],
            ).model_dump(by_alias=True)
        )
    empty_reason = None
    if not recommendations:
        empty_reason = (
            "No quiz attempts are recorded yet. Complete a cited quiz to get a personalized plan."
            if not data["attempts"]
            else "No current source-backed topics are available for a study recommendation."
        )
    return {
        "planDate": plan_date,
        "recommendations": recommendations,
        "emptyReason": empty_reason,
        "methodology": (
            "Priority is deterministic: overdue reviews (+100 each), weak topic (+40), "
            "latest incorrect results (+10 each, capped at 3), ambiguous results (+5 each, "
            "capped at 3), graph neighbors (+1 each, capped at 5), and completed sessions "
            "today (-10 each, capped at 2). Every item links to stored quiz source citations."
        ),
    }


@router.patch(
    "/study-coach/recommendations/{recommendation_id}",
    response_model=StudyCoachRecommendation,
    tags=["learning"],
)
def decide_study_coach_recommendation(
    request: Request,
    recommendation_id: str,
    payload: StudyCoachActionRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    database_path = _database_path(request)
    current_plan = study_coach_plan(request, current_user)
    visible_recommendations = {
        row["recommendationId"]: row for row in current_plan["recommendations"]
    }
    current = visible_recommendations.get(recommendation_id)
    if current is None:
        raise ApiError(
            404,
            "RECOMMENDATION_NOT_FOUND",
            "The study recommendation is no longer available with current source evidence.",
        )
    item = _database_call(
        update_study_coach_recommendation,
        database_path,
        recommendation_id=recommendation_id,
        action=payload.action,
        updated_at=now.isoformat(),
        title=payload.title,
        estimated_minutes=payload.estimated_minutes,
        user_id=current_user.user_id,
    )
    if item is None:
        raise ApiError(404, "RECOMMENDATION_NOT_FOUND", "The study recommendation was not found.")
    citations = current["citations"]
    if not citations:
        raise ApiError(
            404,
            "RECOMMENDATION_EVIDENCE_REMOVED",
            "The source material for this recommendation is no longer available.",
        )
    return StudyCoachRecommendation(
        **item,
        citations=citations,
        quizTopic=item["topic"],
    ).model_dump(by_alias=True)


@router.post(
    "/study-coach/recommendations/{recommendation_id}/explanation",
    response_model=StudyCoachExplanationResponse,
    tags=["learning", "tutor"],
)
async def explain_study_coach_recommendation(
    request: Request, recommendation_id: str, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    plan = study_coach_plan(request, current_user)
    item = next(
        (
            recommendation
            for recommendation in plan["recommendations"]
            if recommendation["recommendationId"] == recommendation_id
        ),
        None,
    )
    if item is None:
        raise ApiError(404, "RECOMMENDATION_NOT_FOUND", "The study recommendation was not found.")
    citations = item["citations"]
    question = "Explain why this study topic is recommended today and what to focus on."
    result = await _grounded_tutor_response(
        _settings(request),
        question=question,
        concept=item["topic"],
        evidence=citations,
        retrieval_mode="lexical_fallback",
        fallback_reason="study_coach_uses_stored_quiz_citations",
    )
    return {
        "question": question,
        "status": result["status"],
        "answer": result["answer"],
        "citations": result["citations"],
    }


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
    current_user: AuthUser = Depends(require_auth),
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
        user_id=current_user.user_id,
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
def embedding_status(
    request: Request, current_user: AuthUser = Depends(require_auth)
) -> dict[str, Any]:
    service = _embedding_service(request)
    try:
        service.load_model()
    except ModelUnavailable:
        pass
    try:
        return service.status(_database_path(request), user_id=current_user.user_id)
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
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    try:
        return _embedding_service(request).rebuild(
            _database_path(request),
            force=payload.force if payload else False,
            user_id=current_user.user_id,
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


def make_error_response(
    status_code: int,
    code: str,
    message: str,
    details: object = None,
    request_id: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "requestId": request_id or uuid.uuid4().hex,
                "details": details,
            }
        },
    )
