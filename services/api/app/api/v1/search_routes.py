"""M78: Universal Search & Intelligent Knowledge Discovery API routes.

Endpoints:

* POST   /api/v1/search/index            - Create indexing job (admin)
* GET    /api/v1/search/index            - List indexing jobs
* GET    /api/v1/search/index/{job_id}   - Get indexing job
* POST   /api/v1/search/index/entry      - Record index entry
* GET    /api/v1/search/index/status     - Get index status for a resource
* POST   /api/v1/search/evaluate/case    - Create evaluation case
* GET    /api/v1/search/evaluate/cases   - List evaluation cases
* POST   /api/v1/search/evaluate/result  - Record evaluation result
* GET    /api/v1/search/evaluate/results - List evaluation results
* GET    /api/v1/search/evaluate/metrics - Aggregate evaluation metrics
* POST   /api/v1/search/explain          - AI query understanding
* POST   /api/v1/search/related-topics   - Suggest related topics

All endpoints require authentication. Index management requires
institution-admin membership. Reads require membership.

CSRF protection is applied globally via CSRFProtectionMiddleware.
"""
# ruff: noqa: B008

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.auth import AuthUser, require_auth
from app.errors import ApiError
from app.schemas import (
    SearchEvaluationCaseCreateRequest,
    SearchEvaluationResultCreateRequest,
    SearchExplainRequest,
    SearchIndexRequest,
    SearchRelatedTopicsRequest,
)
from app.search_platform import (
    compute_aggregate_metrics,
    create_evaluation_case,
    create_indexing_job,
    explain_query,
    get_index_entry_status,
    get_indexing_job,
    list_evaluation_cases,
    list_evaluation_results,
    list_indexing_jobs,
    record_evaluation_result,
    record_index_entry,
    suggest_related_topics,
    update_indexing_job,
)

router = APIRouter(prefix="/api/v1/search", tags=["search"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------


@router.post("/index", response_model=dict[str, Any], status_code=201)
def create_indexing_job_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    job_type: str = Body(default="full_reindex", embed=True),
) -> dict[str, Any]:
    """Create a search index job. Requires authentication."""
    database_path = str(_database_path(request))
    result = create_indexing_job(
        database_path,
        institution_id=None,
        job_type=job_type,
    )
    return result


@router.get("/index", response_model=dict[str, Any])
def list_indexing_jobs_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List indexing jobs."""
    database_path = str(_database_path(request))
    jobs = list_indexing_jobs(
        database_path,
        institution_id=None,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"jobs": jobs, "total": len(jobs), "limit": limit, "offset": offset}


@router.get("/index/{job_id}", response_model=dict[str, Any])
def get_indexing_job_route(
    request: Request,
    job_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get an indexing job by ID."""
    database_path = str(_database_path(request))
    job = get_indexing_job(database_path, job_id=job_id)
    if job is None:
        raise ApiError(404, "JOB_NOT_FOUND", "Indexing job not found.")
    return job


@router.patch("/index/{job_id}", response_model=dict[str, Any])
def update_indexing_job_route(
    request: Request,
    job_id: str = Path(...),
    status: str | None = Body(default=None, embed=True),
    total_items: int | None = Body(default=None, embed=True, ge=0),
    indexed_items: int | None = Body(default=None, ge=0),
    failed_items: int | None = Body(default=None, ge=0),
    error_message: str | None = Body(default=None, embed=True),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update an indexing job's status and counters."""
    database_path = str(_database_path(request))
    result = update_indexing_job(
        database_path,
        job_id=job_id,
        status=status,
        total_items=total_items,
        indexed_items=indexed_items,
        failed_items=failed_items,
        error_message=error_message,
    )
    if result is None:
        raise ApiError(404, "JOB_NOT_FOUND", "Indexing job not found.")
    return result


@router.post("/index/entry", response_model=dict[str, Any], status_code=201)
def record_index_entry_route(
    request: Request,
    payload: SearchIndexRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record or update a search index entry."""
    database_path = str(_database_path(request))
    result = record_index_entry(
        database_path,
        institution_id=None,
        user_id=current_user.user_id,
        resource_type=payload.resourceType,
        resource_id=payload.resourceId,
        content_hash=payload.contentHash,
        fts5_indexed=payload.fts5Indexed,
        semantic_indexed=payload.semanticIndexed,
    )
    if result is None:
        raise ApiError(422, "INVALID_REQUEST", "Invalid index entry input.")
    return result


@router.get("/index/status", response_model=dict[str, Any] | None)
def get_index_entry_status_route(
    request: Request,
    resource_type: str = Query(..., min_length=1, max_length=50),
    resource_id: str = Query(..., min_length=1, max_length=200),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any] | None:
    """Get the indexing status for a resource."""
    database_path = str(_database_path(request))
    result = get_index_entry_status(
        database_path,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    return result


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@router.post("/evaluate/case", response_model=dict[str, Any], status_code=201)
def create_evaluation_case_route(
    request: Request,
    payload: SearchEvaluationCaseCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a search evaluation case."""
    database_path = str(_database_path(request))
    case_id = create_evaluation_case(
        database_path,
        institution_id=None,
        query=payload.query,
        expected_result_ids=payload.expectedResultIds,
        expected_topics=payload.expectedTopics,
        metadata=payload.metadata,
    )
    return {"caseId": case_id}


@router.get("/evaluate/cases", response_model=dict[str, Any])
def list_evaluation_cases_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List search evaluation cases."""
    database_path = str(_database_path(request))
    cases = list_evaluation_cases(
        database_path,
        institution_id=None,
        limit=limit,
        offset=offset,
    )
    return {"cases": cases, "total": len(cases), "limit": limit, "offset": offset}


@router.post("/evaluate/result", response_model=dict[str, Any], status_code=201)
def record_evaluation_result_route(
    request: Request,
    payload: SearchEvaluationResultCreateRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Record a search evaluation result."""
    database_path = str(_database_path(request))
    result_id = record_evaluation_result(
        database_path,
        institution_id=None,
        case_id=payload.caseId,
        recall_at_k=payload.recallAtK,
        precision_at_k=payload.precisionAtK,
        mrr=payload.mrr,
        citation_correctness=payload.citationCorrectness,
        retrieval_latency_ms=payload.retrievalLatencyMs,
        total_results=payload.totalResults,
        relevant_results=payload.relevantResults,
        mode=payload.mode,
    )
    return {"resultId": result_id}


@router.get("/evaluate/results", response_model=dict[str, Any])
def list_evaluation_results_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    case_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List search evaluation results."""
    database_path = str(_database_path(request))
    results = list_evaluation_results(
        database_path,
        institution_id=None,
        case_id=case_id,
        limit=limit,
        offset=offset,
    )
    return {"results": results, "total": len(results), "limit": limit, "offset": offset}


@router.get("/evaluate/metrics", response_model=dict[str, Any])
def get_evaluation_metrics_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    case_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Get aggregate evaluation metrics."""
    database_path = str(_database_path(request))
    results = list_evaluation_results(
        database_path,
        institution_id=None,
        case_id=case_id,
        limit=10000,
        offset=0,
    )
    metrics = compute_aggregate_metrics(results)
    return {"caseId": case_id, **metrics}


# ---------------------------------------------------------------------------
# AI-powered query understanding
# ---------------------------------------------------------------------------


@router.post("/explain", response_model=dict[str, Any])
def explain_query_route(
    request: Request,
    payload: SearchExplainRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Explain a search query: intent, related topics, prerequisites,
    follow-up suggestions, and grounded answer."""
    database_path = str(_database_path(request))
    settings = request.app.state.settings
    embeddings = request.app.state.embedding_service
    result = explain_query(
        database_path,
        query=payload.query,
        context_results=payload.contextResults,
        settings=settings,
        embeddings=embeddings,
        user_id=current_user.user_id,
    )
    return result


@router.post("/related-topics", response_model=dict[str, Any])
def suggest_related_topics_route(
    request: Request,
    payload: SearchRelatedTopicsRequest,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Suggest related topics for a query."""
    database_path = str(_database_path(request))
    result = suggest_related_topics(
        database_path,
        query=payload.query,
        topic_ids=payload.topicIds,
        max_results=payload.maxResults,
    )
    return result