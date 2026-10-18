"""Validated API input models for retrieval and evidence-only answers."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
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


class TutorAnswerRequest(ApiModel):
    question: str = Field(min_length=1, max_length=2_000)
    diagram_id: str | None = Field(default=None, min_length=1, max_length=80)
    concept_id: str | None = Field(default=None, min_length=1, max_length=80)
    graph_concept_id: str | None = Field(default=None, min_length=1, max_length=80)
    document_ids: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(default=5, ge=1, le=10)
    mode: Literal["lexical", "semantic", "hybrid"] | None = None

    @model_validator(mode="after")
    def concept_requires_diagram(self):
        if (self.diagram_id is None) != (self.concept_id is None):
            raise ValueError("diagramId and conceptId must be supplied together")
        if self.graph_concept_id is not None and self.diagram_id is not None:
            raise ValueError("Use either a diagram concept or a graph concept")
        return self


class TutorAnswerResponse(ApiModel):
    question: str
    status: Literal["answered", "insufficient_evidence"]
    answer: str | None
    citations: list[CitationResponse]
    retrieval_mode: Literal["lexical", "semantic", "hybrid", "lexical_fallback"]
    fallback_reason: str | None = None


class TutorStatusResponse(ApiModel):
    configured: bool
    model_id: str | None


class KnowledgeGraphExtractRequest(ApiModel):
    document_ids: list[str] | None = Field(default=None, max_length=50)
    chunk_limit: int = Field(default=10, ge=1, le=20)
    after_chunk_id: str | None = Field(default=None, min_length=1, max_length=120)


class KnowledgeGraphConcept(ApiModel):
    concept_id: str
    name: str
    aliases: list[str]
    evidence_count: int
    relationship_count: int


class KnowledgeGraphConceptList(ApiModel):
    concepts: list[KnowledgeGraphConcept]


class KnowledgeGraphEvidence(ApiModel):
    chunk_id: str
    document_id: str
    display_filename: str
    page_number: int
    supporting_text: str


class KnowledgeGraphRelationship(ApiModel):
    relationship_id: str
    source_concept_id: str
    source_name: str
    target_concept_id: str
    target_name: str
    relation: str
    chunk_id: str
    document_id: str
    display_filename: str
    page_number: int
    supporting_text: str
    status: Literal["candidate", "human_verified"]


class KnowledgeGraphConceptDetail(ApiModel):
    concept_id: str
    name: str
    aliases: list[str]
    evidence: list[KnowledgeGraphEvidence]
    relationships: list[KnowledgeGraphRelationship]


class KnowledgeGraphExtractResponse(ApiModel):
    chunks_processed: int
    next_after_chunk_id: str | None
    concepts: list[KnowledgeGraphConcept]


class QuizGenerateRequest(ApiModel):
    topic: str = Field(min_length=2, max_length=160)
    question_count: int = Field(default=5, ge=1, le=10)
    document_ids: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(default=5, ge=1, le=10)
    mode: Literal["lexical", "semantic", "hybrid"] | None = None
    diagram_id: str | None = Field(default=None, min_length=1, max_length=80)
    concept_id: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def concept_requires_diagram(self):
        if (self.diagram_id is None) != (self.concept_id is None):
            raise ValueError("diagramId and conceptId must be supplied together")
        return self


class QuizOption(ApiModel):
    id: Literal["A", "B", "C", "D"]
    text: str


class QuizQuestionResponse(ApiModel):
    question_id: str
    question_type: Literal["multiple_choice", "short_answer"]
    prompt: str
    options: list[QuizOption]
    explanation: str | None = None
    citations: list[CitationResponse] = Field(default_factory=list)
    latest_outcome: Literal["correct", "incorrect", "ambiguous"] | None = None
    attempt_count: int = 0


class QuizSetResponse(ApiModel):
    status: Literal["ready", "insufficient_evidence"]
    quiz_id: str | None
    topic_id: str | None
    topic: str
    created_at: str | None
    questions: list[QuizQuestionResponse]


class QuizAttemptRequest(ApiModel):
    answer: str = Field(min_length=1, max_length=2_000)


class QuizAttemptResponse(ApiModel):
    attempt_id: str
    question_id: str
    outcome: Literal["correct", "incorrect", "ambiguous"]
    is_correct: bool | None
    expected_answer: str
    explanation: str
    citations: list[CitationResponse]
    attempt_count: int


class TopicProgress(ApiModel):
    topic_id: str
    topic: str
    attempted_questions: int
    correct_questions: int
    incorrect_questions: int
    ambiguous_questions: int
    accuracy: float
    needs_review: bool


class RecentQuizAttempt(ApiModel):
    attempt_id: str
    outcome: Literal["correct", "incorrect", "ambiguous"]
    created_at: str
    prompt: str
    topic_id: str
    topic: str


class LearningProgressResponse(ApiModel):
    topics: list[TopicProgress]
    total_attempts: int
    recent_attempts: list[RecentQuizAttempt]


class StudySessionRequest(ApiModel):
    topic: str = Field(min_length=2, max_length=160)

    @field_validator("topic", mode="before")
    @classmethod
    def trim_topic(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class StudySessionResponse(ApiModel):
    session_id: str
    topic_id: str
    topic: str
    started_at: str
    completed_at: str | None


class AnalyticsTotals(ApiModel):
    attempt_count: int
    attempted_questions: int
    correct_questions: int
    incorrect_questions: int
    ambiguous_questions: int
    quiz_accuracy: float | None
    completed_study_sessions: int
    active_study_sessions: int
    overdue_reviews: int


class AnalyticsTopic(ApiModel):
    topic_id: str
    topic: str
    attempt_count: int
    attempted_questions: int
    correct_questions: int
    incorrect_questions: int
    ambiguous_questions: int
    accuracy: float | None
    mastery: float | None
    weak: bool
    overdue_review_count: int
    next_review_at: str | None
    graph_concept_id: str | None
    citations: list[CitationResponse]


class DailyActivity(ApiModel):
    date: str
    attempts: int
    correct: int
    completed_study_sessions: int


class AnalyticsActivity(ApiModel):
    activity_id: str
    kind: Literal["quiz_attempt", "study_session"]
    topic: str
    outcome: Literal["correct", "incorrect", "ambiguous"] | None
    occurred_at: str
    prompt: str | None


class ReviewHistoryItem(ApiModel):
    question_id: str
    topic_id: str
    topic: str
    outcome: Literal["correct", "incorrect", "ambiguous"]
    attempted_at: str
    due_at: str
    status: Literal["overdue", "upcoming"]


class AnalyticsMethodology(ApiModel):
    mastery: str
    recency_half_life_days: int
    weak_topic_threshold: float
    review_intervals_days: dict[str, int]
    review_due_boundary: str


class LearningAnalyticsResponse(ApiModel):
    generated_at: str
    totals: AnalyticsTotals
    topics: list[AnalyticsTopic]
    daily_activity: list[DailyActivity]
    recent_activity: list[AnalyticsActivity]
    review_history: list[ReviewHistoryItem]
    active_sessions: list[StudySessionResponse]
    methodology: AnalyticsMethodology


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
