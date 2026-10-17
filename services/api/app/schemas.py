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


class AnswerRequest(ApiModel):
    question: str = Field(min_length=1, max_length=2_000)
    document_ids: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(default=5, ge=1, le=100)


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


class DocumentsResponse(ApiModel):
    documents: list[DocumentResponse]


class DeleteDocumentResponse(ApiModel):
    document_id: str
    deleted: Literal[True]


class RetrievalInfo(ApiModel):
    method: Literal["fts5_bm25"]
    rank: int


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


class AnswerResponse(ApiModel):
    question: str
    status: Literal["evidence_found", "insufficient_evidence"]
    answer_type: Literal["extractive_evidence", "abstention"]
    answer: str | None
    citations: list[CitationResponse]
