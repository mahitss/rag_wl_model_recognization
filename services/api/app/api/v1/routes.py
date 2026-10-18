"""Versioned system, document, retrieval, and evidence-only answer routes."""

import asyncio
import hashlib
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, File, Request, UploadFile
from fastapi.responses import JSONResponse

from app.config import Settings
from app.diagrams.catalog import DIAGRAMS, get_diagram, resolve_region
from app.embeddings.service import EmbeddingService, ModelUnavailable
from app.errors import ApiError
from app.ingestion.pdf import ingest_pdf
from app.knowledge_graph import extract_chunk
from app.learning_analytics import build_analytics
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
    KnowledgeGraphConceptDetail,
    KnowledgeGraphConceptList,
    KnowledgeGraphExtractRequest,
    KnowledgeGraphExtractResponse,
    LearningAnalyticsResponse,
    LearningProgressResponse,
    QuizAttemptRequest,
    QuizAttemptResponse,
    QuizGenerateRequest,
    QuizSetResponse,
    ReadinessResponse,
    RebuildEmbeddingsRequest,
    RebuildEmbeddingsResponse,
    RetrievalRequest,
    RetrievalResponse,
    StudySessionRequest,
    StudySessionResponse,
    TutorAnswerRequest,
    TutorAnswerResponse,
    TutorStatusResponse,
)
from app.storage import (
    complete_study_session,
    create_quiz_set,
    delete_document,
    get_document,
    get_knowledge_concept,
    get_learning_analytics_data,
    get_learning_progress,
    get_quiz_question,
    get_quiz_set,
    knowledge_graph_chunks,
    list_documents,
    list_knowledge_concepts,
    record_quiz_attempt,
    start_study_session,
    store_knowledge_extraction,
    verify_knowledge_relationship,
)
from app.tutor import InvalidTutorResponse, TutorUnavailable, generate_grounded_answer

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


def _database_call(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except ApiError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable.") from exc


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(request: Request) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "wonderlearn-api",
        "capabilities": {
            "document_ingestion": True,
            "retrieval": True,
            "model_inference": _settings(request).tutor_configured,
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


@router.get("/tutor/status", response_model=TutorStatusResponse, tags=["tutor"])
def tutor_status(request: Request) -> dict[str, Any]:
    settings = _settings(request)
    return {
        "configured": settings.tutor_configured,
        "modelId": settings.tutor_model_id if settings.tutor_configured else None,
    }


@router.post("/tutor/answers", response_model=TutorAnswerResponse, tags=["tutor"])
async def tutor_answer(request: Request, payload: TutorAnswerRequest) -> dict[str, Any]:
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
            get_knowledge_concept, settings.database_path, payload.graph_concept_id
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
    )
    if not search["results"]:
        return {
            "question": payload.question,
            "status": "insufficient_evidence",
            "answer": None,
            "citations": [],
            "retrievalMode": search["retrievalMode"],
            "fallbackReason": search["fallbackReason"],
        }

    try:
        generated = await asyncio.to_thread(
            generate_grounded_answer,
            settings,
            question=payload.question,
            concept=concept_name,
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
            "TUTOR_INVALID_RESPONSE",
            "The local tutor returned an invalid response. Try again or check its status.",
        ) from exc

    return {
        "question": payload.question,
        "status": "answered" if generated["answer"] is not None else "insufficient_evidence",
        "answer": generated["answer"],
        "citations": generated["citations"],
        "retrievalMode": search["retrievalMode"],
        "fallbackReason": search["fallbackReason"],
    }


@router.get("/knowledge-graph", response_model=KnowledgeGraphConceptList, tags=["knowledge graph"])
def knowledge_graph(request: Request) -> dict[str, Any]:
    return {"concepts": _database_call(list_knowledge_concepts, _database_path(request))}


@router.post(
    "/knowledge-graph/extract",
    response_model=KnowledgeGraphExtractResponse,
    tags=["knowledge graph"],
)
async def extract_knowledge_graph(
    request: Request, payload: KnowledgeGraphExtractRequest
) -> dict[str, Any]:
    settings = _settings(request)
    try:
        chunks = knowledge_graph_chunks(
            settings.database_path,
            document_ids=payload.document_ids,
            limit=payload.chunk_limit + 1,
            after_chunk_id=payload.after_chunk_id,
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
            store_knowledge_extraction(settings.database_path, chunk, result)
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
        "concepts": _database_call(list_knowledge_concepts, settings.database_path),
    }


@router.get(
    "/knowledge-graph/concepts/{concept_id}",
    response_model=KnowledgeGraphConceptDetail,
    tags=["knowledge graph"],
)
def knowledge_graph_concept(request: Request, concept_id: str) -> dict[str, Any]:
    result = _database_call(get_knowledge_concept, _database_path(request), concept_id)
    if result is None:
        raise ApiError(404, "CONCEPT_NOT_FOUND", "The requested graph concept was not found.")
    return result


@router.post(
    "/knowledge-graph/relationships/{relationship_id}/verify",
    tags=["knowledge graph"],
)
def verify_knowledge_graph_relationship(request: Request, relationship_id: str) -> dict[str, Any]:
    verified = _database_call(
        verify_knowledge_relationship, _database_path(request), relationship_id
    )
    if not verified:
        raise ApiError(404, "RELATIONSHIP_NOT_FOUND", "The candidate relationship was not found.")
    return {"relationshipId": relationship_id, "status": "human_verified"}


@router.post("/quizzes", response_model=QuizSetResponse, tags=["learning"])
async def create_quiz(request: Request, payload: QuizGenerateRequest) -> dict[str, Any]:
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
        )
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503, "STORAGE_UNAVAILABLE", "Local learning storage is unavailable."
        ) from exc
    quiz = _database_call(get_quiz_set, settings.database_path, quiz_id)
    return {"status": "ready", **quiz}


@router.get("/quizzes/{quiz_id}", response_model=QuizSetResponse, tags=["learning"])
def read_quiz(request: Request, quiz_id: str, review_incorrect: bool = False) -> dict[str, Any]:
    quiz = _database_call(
        get_quiz_set,
        _database_path(request),
        quiz_id,
        review_incorrect=review_incorrect,
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
    request: Request, quiz_id: str, question_id: str, payload: QuizAttemptRequest
) -> dict[str, Any]:
    database_path = _database_path(request)
    question = _database_call(get_quiz_question, database_path, question_id)
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
    )
    quiz = _database_call(get_quiz_set, database_path, quiz_id)
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
def learning_progress(request: Request) -> dict[str, Any]:
    return _database_call(get_learning_progress, _database_path(request))


@router.get(
    "/learning-analytics",
    response_model=LearningAnalyticsResponse,
    tags=["learning"],
)
def learning_analytics(request: Request) -> dict[str, Any]:
    now = datetime.now(UTC)
    data = _database_call(get_learning_analytics_data, _database_path(request))
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
def begin_study_session(request: Request, payload: StudySessionRequest) -> dict[str, Any]:
    topic = " ".join(payload.topic.split())
    topic_id = hashlib.sha256(topic.casefold().encode("utf-8")).hexdigest()[:20]
    return _database_call(
        start_study_session,
        _database_path(request),
        session_id=uuid.uuid4().hex,
        topic_id=topic_id,
        topic=topic,
        started_at=datetime.now(UTC).isoformat(),
    )


@router.post(
    "/study-sessions/{session_id}/complete",
    response_model=StudySessionResponse,
    tags=["learning"],
)
def finish_study_session(request: Request, session_id: str) -> dict[str, Any]:
    session = _database_call(
        complete_study_session,
        _database_path(request),
        session_id=session_id,
        completed_at=datetime.now(UTC).isoformat(),
    )
    if session is None:
        raise ApiError(404, "STUDY_SESSION_NOT_FOUND", "The study session was not found.")
    return session


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
