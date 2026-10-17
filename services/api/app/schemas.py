"""Validated API input models for retrieval and evidence-only answers."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class RetrievalRequest(ApiModel):
    query: str = Field(min_length=1, max_length=2_000)
    document_ids: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(default=5, ge=1, le=100)
    mode: Literal["lexical", "semantic", "hybrid"] | None = None


class AnswerRequest(ApiModel):
    question: str = Field(min_length=1, max_length=2_000)
    document_ids: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(default=5, ge=1, le=100)
    mode: Literal["lexical", "semantic", "hybrid"] | None = None


class Capabilities(BaseModel):
    document_ingestion: bool
    retrieval: bool
    model_inference: bool


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    capabilities: Capabilities


class StorageChecks(BaseModel):
    sqlite: bool
    sqlite_fts5: bool


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: StorageChecks


class DocumentResponse(ApiModel):
    document_id: str
    display_filename: str
    sha256: str
    byte_size: int
    page_count: int
    chunk_count: int
    created_at: str
    parser_version: str
    status: Literal["indexed"]
    embedding_status: Literal["not_requested", "indexed", "incomplete", "unavailable"] | None = None


class DocumentsResponse(ApiModel):
    documents: list[DocumentResponse]


class DeleteDocumentResponse(ApiModel):
    document_id: str
    deleted: Literal[True]


class RetrievalInfo(ApiModel):
    method: Literal["fts5_bm25", "cosine_similarity", "rrf_hybrid"]
    rank: int
    similarity: float | None = None
    rrf_score: float | None = None
    lexical_rank: int | None = None
    semantic_rank: int | None = None


class CitationResponse(ApiModel):
    citation_id: str
    document_id: str
    display_filename: str
    page_number: int
    chunk_index: int
    char_start: int
    char_end: int
    text: str
    retrieval: RetrievalInfo


class RetrievalResponse(ApiModel):
    results: list[CitationResponse]
    retrieval_mode: Literal["lexical", "semantic", "hybrid", "lexical_fallback"] = "lexical"
    fallback_reason: str | None = None


class DiagramGeometry(ApiModel):
    x: float
    y: float
    width: float
    height: float


class DiagramRegionResponse(ApiModel):
    region_id: str
    concept_id: str
    display_name: str
    aliases: list[str]
    geometry: DiagramGeometry


class DiagramResponse(ApiModel):
    diagram_id: str
    title: str
    description: str
    image_url: str
    image_aspect_ratio: float
    regions: list[DiagramRegionResponse]


class DiagramsResponse(ApiModel):
    diagrams: list[DiagramResponse]


class DiagramEvidenceRequest(ApiModel):
    limit: int = Field(default=5, ge=1, le=10)


class DiagramConceptResponse(ApiModel):
    concept_id: str
    display_name: str
    aliases: list[str]


class DiagramEvidenceResponse(ApiModel):
    status: Literal["evidence_found", "insufficient_evidence"]
    diagram_id: str
    region_id: str
    concept: DiagramConceptResponse
    results: list[CitationResponse]
    retrieval_mode: Literal["lexical", "semantic", "hybrid", "lexical_fallback"]
    fallback_reason: str | None = None


class AnswerResponse(ApiModel):
    question: str
    status: Literal["evidence_found", "insufficient_evidence"]
    answer_type: Literal["extractive_evidence", "abstention"]
    answer: str | None
    citations: list[CitationResponse]
    retrieval_mode: Literal["lexical", "semantic", "hybrid", "lexical_fallback"] = "lexical"
    fallback_reason: str | None = None


class EmbeddingIndexCounts(ApiModel):
    total_chunks: int
    indexed_chunks: int
    current_chunks: int
    stale_chunks: int
    missing_chunks: int


class EmbeddingStatusResponse(ApiModel):
    model_id: str
    model_revision: str
    dimensions: int
    model_state: Literal["not_checked", "available", "unavailable"]
    index: EmbeddingIndexCounts


class RebuildEmbeddingsRequest(ApiModel):
    force: bool = False


class RebuildEmbeddingsResponse(EmbeddingStatusResponse):
    indexed_this_run: int
    complete: bool
