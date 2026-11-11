"""Validated API input models for retrieval and evidence-only answers."""

from typing import Any, Literal, Self

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


class RegistrationResponse(ApiModel):
    message: str


class SessionUserResponse(ApiModel):
    user_id: str
    username: str
    expires_at: str
    is_admin: bool = False


class CsrfTokenResponse(ApiModel):
    csrf_token: str


class LogoutResponse(ApiModel):
    message: str


class FeedbackCategory(str):
    pass


class FeedbackCreateRequest(ApiModel):
    category: Literal["bug", "usability", "feature_request", "other"]
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4_000)


class FeedbackUpdateRequest(ApiModel):
    category: Literal["bug", "usability", "feature_request", "other"] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=4_000)
    status: Literal["new", "reviewing", "planned", "resolved", "withdrawn"] | None = None


class FeedbackAdminUpdateRequest(ApiModel):
    category: Literal["bug", "usability", "feature_request", "other"] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=4_000)
    status: (
        Literal["new", "reviewing", "planned", "in_progress", "resolved", "withdrawn", "declined"]
        | None
    ) = None
    resolution_notes: str | None = Field(default=None, max_length=4000)
    improvement_task_id: str | None = Field(default=None, min_length=1, max_length=80)


class FeedbackAdminStatusRequest(ApiModel):
    status: Literal[
        "new", "reviewing", "planned", "in_progress", "resolved", "withdrawn", "declined"
    ]


class FeedbackItem(ApiModel):
    feedback_id: str
    user_id: str
    username: str | None = None
    category: Literal["bug", "usability", "feature_request", "other"]
    title: str
    description: str
    status: Literal[
        "new", "reviewing", "planned", "in_progress", "resolved", "withdrawn", "declined"
    ]
    resolution_notes: str | None = None
    improvement_task_id: str | None = None
    created_at: str
    updated_at: str


class FeedbackListResponse(ApiModel):
    feedback: list[FeedbackItem]


class ImprovementTaskStatus(str):
    pass


class ImprovementTaskCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    status: Literal["proposed", "in_progress", "resolved", "declined"] = "proposed"
    severity: int = Field(default=3, ge=1, le=5)
    impact: int = Field(default=3, ge=1, le=5)
    reproducibility: int = Field(default=3, ge=1, le=5)
    workflow_frequency: int = Field(default=3, ge=1, le=5)
    implementation_effort: int = Field(default=3, ge=1, le=5)
    regression_test: str | None = Field(default=None, max_length=200)
    feedback_ids: list[str] | None = Field(default=None, max_length=100)

    @field_validator("feedback_ids", mode="before")
    @classmethod
    def normalize_feedback_ids(cls, value):
        if value is None:
            return []
        return [str(fid).strip() for fid in value if str(fid).strip()]


class ImprovementTaskUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    status: Literal["proposed", "in_progress", "resolved", "declined"] | None = None
    severity: int | None = Field(default=None, ge=1, le=5)
    impact: int | None = Field(default=None, ge=1, le=5)
    reproducibility: int | None = Field(default=None, ge=1, le=5)
    workflow_frequency: int | None = Field(default=None, ge=1, le=5)
    implementation_effort: int | None = Field(default=None, ge=1, le=5)
    regression_test: str | None = Field(default=None, max_length=200)
    implemented: bool | None = None


class ImprovementTaskResponse(ApiModel):
    task_id: str
    title: str
    description: str
    status: Literal["proposed", "in_progress", "resolved", "declined"]
    priority_score: float
    priority_factors: dict[str, Any]
    severity: int
    impact: int
    report_count: int
    reproducibility: int
    workflow_frequency: int
    implementation_effort: int
    regression_test: str | None = None
    implemented: bool
    implemented_at: str | None = None
    user_id: str
    username: str | None = None
    created_at: str
    updated_at: str
    feedback_ids: list[str]


class ImprovementTaskListResponse(ApiModel):
    tasks: list[ImprovementTaskResponse]


class LinkFeedbackRequest(ApiModel):
    feedback_id: str = Field(min_length=1, max_length=80)


class FeedbackFilterRequest(ApiModel):
    category: Literal["bug", "usability", "feature_request", "other"] | None = None
    status: (
        Literal["new", "reviewing", "planned", "in_progress", "resolved", "withdrawn", "declined"]
        | None
    ) = None
    date_from: str | None = None
    date_to: str | None = None
    sort_by: Literal["recency", "created"] = "recency"
    sort_order: Literal["asc", "desc"] = "desc"


class PrioritizationExplanation(ApiModel):
    formula: str
    weights: dict[str, int]
    measured_fields: list[str]
    estimated_fields: list[str]
    range_note: str


class Capabilities(BaseModel):
    document_ingestion: bool
    retrieval: bool
    model_inference: bool


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    environment: str
    capabilities: Capabilities


class StorageChecks(BaseModel):
    sqlite: bool
    sqlite_fts5: bool


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: StorageChecks


class ProductAnalyticsPrivacy(ApiModel):
    retention_days: int
    raw_content: Literal["not_collected"] = "not_collected"
    identifiers: Literal["user_id_only_for_personalized_views"] = (
        "user_id_only_for_personalized_views"
    )


class ProductAnalyticsFeatureSummary(ApiModel):
    feature: str
    event_count: int
    positive_outcome_rate: float | None
    positive_outcomes: int


class ProductAnalyticsTrendPoint(ApiModel):
    date: str
    event_count: int
    success_count: int
    failure_count: int


class ProductAnalyticsTotals(ApiModel):
    event_count: int
    success_count: int
    failure_count: int
    feature_count: int


class ProductAnalyticsSummary(ApiModel):
    generated_at: str
    enabled: bool
    window_days: int
    totals: ProductAnalyticsTotals
    features: list[ProductAnalyticsFeatureSummary]
    trends: list[ProductAnalyticsTrendPoint]
    privacy: ProductAnalyticsPrivacy


class ProductAnalyticsEventRequest(ApiModel):
    event_name: Literal[
        "document_upload_completed",
        "document_upload_failed",
        "image_upload_completed",
        "image_upload_failed",
        "tutor_request_completed",
        "tutor_request_abstained",
        "tutor_request_failed",
        "quiz_started",
        "quiz_completed",
        "quiz_failed",
        "study_session_completed",
        "study_session_failed",
        "study_coach_recommendation_accepted",
        "study_coach_recommendation_dismissed",
    ]
    feature: Literal[
        "documents",
        "images",
        "tutor",
        "quizzes",
        "study_sessions",
        "study_coach",
    ]
    result: Literal[
        "success",
        "failure",
        "abstained",
        "accepted",
        "dismissed",
        "started",
        "completed",
    ]
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def event_identity_is_consistent(self) -> Self:
        expected = {
            "document_upload_completed": ("documents", "success"),
            "document_upload_failed": ("documents", "failure"),
            "image_upload_completed": ("images", "success"),
            "image_upload_failed": ("images", "failure"),
            "tutor_request_completed": ("tutor", "success"),
            "tutor_request_abstained": ("tutor", "abstained"),
            "tutor_request_failed": ("tutor", "failure"),
            "quiz_started": ("quizzes", "started"),
            "quiz_completed": ("quizzes", "completed"),
            "quiz_failed": ("quizzes", "failure"),
            "study_session_completed": ("study_sessions", "completed"),
            "study_session_failed": ("study_sessions", "failure"),
            "study_coach_recommendation_accepted": ("study_coach", "accepted"),
            "study_coach_recommendation_dismissed": ("study_coach", "dismissed"),
        }
        if expected[self.event_name] != (self.feature, self.result):
            raise ValueError("Event name, feature, and result must describe a supported event.")
        return self


class DocumentResponse(ApiModel):
    document_id: str
    display_filename: str
    sha256: str
    byte_size: int
    page_count: int
    chunk_count: int
    created_at: str
    parser_version: str
    format: Literal["pdf", "text", "image"] = "pdf"
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


class NormalizedImageRegion(ApiModel):
    x: float = Field(ge=0, lt=1, allow_inf_nan=False)
    y: float = Field(ge=0, lt=1, allow_inf_nan=False)
    width: float = Field(gt=0, le=1, allow_inf_nan=False)
    height: float = Field(gt=0, le=1, allow_inf_nan=False)

    @field_validator("x", "y", "width", "height", mode="before")
    @classmethod
    def reject_boolean_coordinates(cls, value):
        if isinstance(value, bool):
            raise ValueError("Region coordinates must be numbers")
        return value

    @model_validator(mode="after")
    def region_must_fit_image(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("The selected region must fit within normalized image bounds")
        return self


class ImageRegionCreateRequest(ApiModel):
    geometry: NormalizedImageRegion
    diagram_id: str | None = Field(default=None, min_length=1, max_length=80)
    concept_id: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def concept_requires_diagram(self):
        if (self.diagram_id is None) != (self.concept_id is None):
            raise ValueError("diagramId and conceptId must be supplied together")
        return self


class LearningImageResponse(ApiModel):
    image_id: str
    display_filename: str
    content_type: Literal["image/png", "image/jpeg"]
    width: int
    height: int
    byte_size: int
    sha256: str
    created_at: str
    ocr_status: Literal["available", "empty", "unavailable", "failed"]
    extracted_text: str | None


class LearningImageSummaryResponse(ApiModel):
    image_id: str
    display_filename: str
    content_type: Literal["image/png", "image/jpeg"]
    width: int
    height: int
    byte_size: int
    sha256: str
    created_at: str
    ocr_status: Literal["available", "empty", "unavailable", "failed"]


class LearningImageListResponse(ApiModel):
    images: list[LearningImageSummaryResponse]


class LearningImageRegionResponse(ApiModel):
    region_id: str
    geometry: NormalizedImageRegion
    ocr_status: Literal["available", "empty", "unavailable", "failed"]
    extracted_text: str | None
    diagram_id: str | None
    concept_id: str | None
    concept_name: str | None
    citations: list[CitationResponse]


class LearningImageDetailResponse(LearningImageResponse):
    regions: list[LearningImageRegionResponse]


class ImageRegionCreateResponse(LearningImageRegionResponse):
    pass


class ImageEvidenceResponse(ApiModel):
    status: Literal["evidence_found", "insufficient_evidence"]
    image_id: str
    region_id: str
    geometry: NormalizedImageRegion
    extracted_text: str | None
    ocr_status: Literal["available", "empty", "unavailable", "failed"]
    concept_name: str | None
    results: list[CitationResponse]
    retrieval_mode: Literal["lexical", "semantic", "hybrid", "lexical_fallback"]
    fallback_reason: str | None


class ImageTutorRequest(ApiModel):
    question: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=5, ge=1, le=10)
    mode: Literal["lexical", "semantic", "hybrid"] | None = None


class ImageTutorResponse(ApiModel):
    question: str
    status: Literal["answered", "insufficient_evidence"]
    answer: str | None
    image_id: str
    region_id: str
    geometry: NormalizedImageRegion
    extracted_text: str | None
    ocr_status: Literal["available", "empty", "unavailable", "failed"]
    concept_name: str | None
    citations: list[CitationResponse]
    retrieval_mode: Literal["lexical", "semantic", "hybrid", "lexical_fallback"]
    fallback_reason: str | None


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
    difficulty_level: Literal["foundational", "building", "practice", "advanced"] | None = None

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
    difficulty_level: Literal["foundational", "building", "practice", "advanced"] | None = None


class QuizSetResponse(ApiModel):
    status: Literal["ready", "insufficient_evidence"]
    quiz_id: str | None
    topic_id: str | None
    topic: str
    created_at: str | None
    questions: list[QuizQuestionResponse]


class QuizAttemptRequest(ApiModel):
    answer: str = Field(min_length=1, max_length=2_000)
    duration_ms: int | None = Field(default=None, ge=0)


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


class StudyCoachRecommendation(ApiModel):
    recommendation_id: str
    plan_date: str
    topic_id: str
    topic: str
    title: str
    priority: int
    reasons: list[str]
    graph_concept_id: str | None
    related_concept_ids: list[str]
    status: Literal["planned", "accepted", "dismissed", "completed"]
    estimated_minutes: int = Field(ge=5, le=180)
    updated_at: str
    citations: list[CitationResponse]
    quiz_topic: str


class StudyCoachPlanResponse(ApiModel):
    plan_date: str
    recommendations: list[StudyCoachRecommendation]
    empty_reason: str | None
    methodology: str


class StudyCoachExplanationResponse(ApiModel):
    question: str
    status: Literal["answered", "insufficient_evidence"]
    answer: str | None
    citations: list[CitationResponse]


class StudyCoachActionRequest(ApiModel):
    action: Literal["accept", "edit", "dismiss", "complete"]
    title: str | None = Field(default=None, min_length=2, max_length=160)
    estimated_minutes: int | None = Field(default=None, ge=5, le=180)

    @model_validator(mode="after")
    def edit_requires_changes(self):
        if self.action == "edit" and self.title is None and self.estimated_minutes is None:
            raise ValueError("Edit must include a new title or estimated duration")
        if self.action != "edit" and (self.title is not None or self.estimated_minutes is not None):
            raise ValueError("Only the edit action accepts title or duration changes")
        return self


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


# ---------------------------------------------------------------------------
# M34: Personalized learning paths and adaptive difficulty
# ---------------------------------------------------------------------------


class LearningPathStatus(str):
    pass


class LearningPathCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)


class LearningPathUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    status: Literal["active", "paused", "completed", "archived"] | None = None


class LearningPathResponse(ApiModel):
    path_id: str
    user_id: str
    title: str
    description: str
    status: Literal["active", "paused", "completed", "archived"]
    created_at: str
    updated_at: str


class LearningPathListResponse(ApiModel):
    paths: list[LearningPathResponse]


class LearningObjectiveStatus(str):
    pass


class LearningObjectiveCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    order_index: int = Field(default=0, ge=0)
    source_document_id: str | None = Field(default=None, min_length=1, max_length=80)
    source_chunk_id: str | None = Field(default=None, min_length=1, max_length=80)
    source_passage: str | None = Field(default=None, max_length=4000)
    prerequisite_objective_ids: list[str] | None = Field(default=None, max_length=50)
    rationale: str | None = Field(default=None, max_length=4000)
    difficulty_level: Literal["foundational", "building", "practice", "advanced"] = "foundational"

    @field_validator("prerequisite_objective_ids", mode="before")
    @classmethod
    def normalize_prerequisite_ids(cls, value):
        if value is None:
            return []
        return [str(fid).strip() for fid in value if str(fid).strip()]


class LearningObjectiveUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    status: Literal["not_started", "in_progress", "completed", "skipped"] | None = None
    order_index: int | None = Field(default=None, ge=0)
    rationale: str | None = Field(default=None, max_length=4000)
    difficulty_level: Literal["foundational", "building", "practice", "advanced"] | None = None


class LearningObjectiveResponse(ApiModel):
    objective_id: str
    path_id: str
    title: str
    description: str
    status: Literal["not_started", "in_progress", "completed", "skipped"]
    order_index: int
    source_document_id: str | None = None
    source_chunk_id: str | None = None
    source_passage: str | None = None
    prerequisite_objective_ids: list[str]
    rationale: str | None = None
    difficulty_level: Literal["foundational", "building", "practice", "advanced"]
    evidence_ids: list[str]
    activity_links: list[dict[str, str]]
    created_at: str
    updated_at: str


class LearningObjectiveListResponse(ApiModel):
    objectives: list[LearningObjectiveResponse]


class LinkObjectiveEvidenceRequest(ApiModel):
    citation_ids: list[str] = Field(default_factory=list, max_length=50)


class LinkObjectiveActivityRequest(ApiModel):
    activity_type: Literal["quiz_attempt", "study_session"]
    activity_id: str = Field(min_length=1, max_length=80)


class AdaptiveRecommendationResponse(ApiModel):
    topic_id: str
    topic: str
    difficulty_level: Literal["foundational", "building", "practice", "advanced"]
    priority_score: float
    reasons: list[str]
    citations: list[CitationResponse]
    citations_count: int
    activity_suggestion: Literal[
        "review_overdue",
        "foundational_practice",
        "introductory_quiz",
        "targeted_quiz",
        "mixed_quiz",
        "challenge_quiz",
    ]
    mastery: float | None = None
    measured_fields: list[str]
    estimated_fields: list[str]


class AdaptiveRecommendationsResponse(ApiModel):
    recommendations: list[AdaptiveRecommendationResponse]


# ---------------------------------------------------------------------------
# M36: Shared study spaces (private collaboration workspaces).
# ---------------------------------------------------------------------------


class StudySpaceRole(str):
    pass


class StudySpaceCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    learningObjective: str | None = Field(default=None, max_length=1000)


class StudySpaceUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    learningObjective: str | None = Field(default=None, max_length=1000)
    status: Literal["active", "archived"] | None = None


class WorkspaceMember(ApiModel):
    userId: str
    username: str | None = None
    role: Literal["owner", "member"]
    joinedAt: str


class WorkspaceMemberInviteRequest(ApiModel):
    username: str = Field(min_length=3, max_length=64)


class StudySpaceResponse(ApiModel):
    spaceId: str
    ownerId: str
    title: str
    description: str
    learningObjective: str | None = None
    status: Literal["active", "archived"]
    memberCount: int
    createdAt: str
    updatedAt: str
    userRole: Literal["owner", "member"]


class StudySpaceListResponse(ApiModel):
    spaces: list[StudySpaceResponse]


class WorkspaceDocument(ApiModel):
    documentId: str
    displayFilename: str
    sha256: str
    byteSize: int
    pageCount: int
    chunkCount: int
    createdAt: str
    parserVersion: str
    sharedAt: str
    sharedBy: str
    sharedByUsername: str | None = None


class WorkspaceDocumentShareRequest(ApiModel):
    documentId: str = Field(min_length=1, max_length=80)


class WorkspaceDocumentListResponse(ApiModel):
    documents: list[WorkspaceDocument]


class WorkspaceQuiz(ApiModel):
    quizId: str
    topicId: str
    topic: str
    createdAt: str
    sharedAt: str
    sharedBy: str
    sharedByUsername: str | None = None


class WorkspaceQuizShareRequest(ApiModel):
    quizId: str = Field(min_length=1, max_length=80)


class WorkspaceQuizListResponse(ApiModel):
    quizzes: list[WorkspaceQuiz]


class WorkspaceDiscussion(ApiModel):
    discussionId: str
    spaceId: str
    userId: str
    username: str | None = None
    content: str
    createdAt: str


class WorkspaceDiscussionCreateRequest(ApiModel):
    content: str = Field(min_length=1, max_length=4000)


class WorkspaceDiscussionReply(ApiModel):
    replyId: str
    discussionId: str
    userId: str
    username: str | None = None
    content: str
    createdAt: str


class WorkspaceDiscussionReplyCreateRequest(ApiModel):
    content: str = Field(min_length=1, max_length=4000)


class WorkspaceDiscussionListResponse(ApiModel):
    discussions: list[WorkspaceDiscussion]


class WorkspaceDiscussionReplyListResponse(ApiModel):
    replies: list[WorkspaceDiscussionReply]


class WorkspaceMemberRemoveRequest(ApiModel):
    userId: str = Field(min_length=1, max_length=80)


# ---------------------------------------------------------------------------
# M38: AI Learning Content Studio.
# ---------------------------------------------------------------------------


class ContentResourceFormat(str):
    pass


class ContentResourceDetailLevel(str):
    pass


class ContentResourceStatus(str):
    pass


class ContentSourceType(str):
    pass


class ContentSectionType(str):
    pass


class ContentResourceCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    format: Literal[
        "notes", "summary", "flashcards", "questions", "revision_sheet", "concept_outline"
    ]
    detailLevel: Literal["brief", "standard", "comprehensive"] = "standard"
    topic: str = Field(min_length=2, max_length=160)
    documentIds: list[str] | None = Field(default=None, max_length=50)
    spaceId: str | None = Field(default=None, min_length=1, max_length=80)


class ContentResourceSection(ApiModel):
    sectionId: str
    resourceId: str
    sectionType: Literal["heading", "body", "flashcard", "question", "summary"]
    sectionTitle: str
    content: str
    citations: list[CitationResponse]
    version: int
    createdAt: str
    verificationStatus: Literal[
        "verified", "needs_review", "insufficient_evidence"
    ] = "needs_review"
    verificationMethod: Literal["deterministic", "human"] = "deterministic"
    verificationDetails: dict[str, Any] = {}
    verifiedAt: str | None = None
    verifiedBy: str | None = None


class ContentResourceResponse(ApiModel):
    resourceId: str
    userId: str
    title: str
    description: str | None = None
    format: Literal[
        "notes", "summary", "flashcards", "questions", "revision_sheet", "concept_outline"
    ]
    detailLevel: Literal["brief", "standard", "comprehensive"]
    status: Literal["draft", "reviewed", "archived"]
    sourceType: Literal["document", "workspace"]
    sourceRefs: list[dict[str, Any]]
    sections: list[ContentResourceSection] = []
    createdAt: str
    updatedAt: str
    reviewedAt: str | None = None
    reviewedBy: str | None = None


class ContentResourceListResponse(ApiModel):
    resources: list[ContentResourceResponse]


class ContentResourceUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    status: Literal["draft", "reviewed", "archived"] | None = None


class ContentSectionEditRequest(ApiModel):
    sectionTitle: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=10000)
    citations: list[str] | None = Field(default=None, max_length=50)


class ContentSectionRegenerateRequest(ApiModel):
    sectionType: Literal["heading", "body", "flashcard", "question", "summary"] | None = None
    sectionTitle: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=10000)
    citations: list[dict[str, Any]] | None = Field(default=None, max_length=50)


class ContentResourceReviewRequest(ApiModel):
    status: Literal["reviewed", "archived"]


class ContentResourceRevision(ApiModel):
    revisionId: str
    resourceId: str
    sectionId: str
    content: str
    citations: list[CitationResponse]
    createdAt: str


class ContentResourceRevisionListResponse(ApiModel):
    revisions: list[ContentResourceRevision]


class ContentResourceQuizLinkRequest(ApiModel):
    quizId: str = Field(min_length=1, max_length=80)


# ---------------------------------------------------------------------------
# M39: AI Study Content Quality & Verification Engine.
# ---------------------------------------------------------------------------


class ContentResourceSectionVerification(ApiModel):
    sectionId: str
    resourceId: str
    sectionType: Literal["heading", "body", "flashcard", "question", "summary"]
    sectionTitle: str
    content: str
    citations: list[CitationResponse]
    version: int
    createdAt: str
    verificationStatus: Literal["verified", "needs_review", "insufficient_evidence"]
    verificationMethod: Literal["deterministic", "human"]
    verificationDetails: dict[str, Any]
    verifiedAt: str | None = None
    verifiedBy: str | None = None


class ContentResourceValidation(ApiModel):
    validationId: str
    resourceId: str
    userId: str
    citationValidity: bool
    evidenceCoverage: float
    schemaValidity: bool
    unsupportedClaimCount: int
    questionAnswerCorrectness: float | None
    verificationMethod: Literal["deterministic", "semantic_estimate"]
    details: dict[str, Any]
    sections: list[dict[str, Any]] = []
    status: Literal["verified", "needs_review", "insufficient_evidence"] | None = None
    verifiedBy: str | None = None
    verifiedAt: str | None = None
    createdAt: str


class ContentResourceValidationListResponse(ApiModel):
    validations: list[ContentResourceValidation]


class ContentSectionVerifyRequest(ApiModel):
    verificationStatus: Literal["verified", "needs_review", "insufficient_evidence"]
    verificationNotes: str | None = Field(default=None, max_length=4000)


# ---------------------------------------------------------------------------
# M41: Intelligent Search & Knowledge Discovery.
# Unified search across documents, content resources, quizzes, learning
# objectives, and knowledge-graph concepts with user isolation.
# ---------------------------------------------------------------------------


class SearchResultItem(ApiModel):
    """One unified search result with provenance and a snippet."""

    result_type: Literal[
        "document_passage",
        "content_section",
        "quiz_question",
        "learning_objective",
        "knowledge_concept",
    ]
    id: str
    title: str
    snippet: str
    score: float
    source_type: Literal[
        "document", "content_resource", "quiz", "learning_objective", "knowledge_concept"
    ]
    source_id: str
    source_title: str | None = None
    page_number: int | None = None
    citations: list[CitationResponse] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


class UnifiedSearchRequest(ApiModel):
    query: str = Field(min_length=1, max_length=2_000)
    resource_types: list[
        Literal[
            "document",
            "content_resource",
            "quiz",
            "learning_objective",
            "knowledge_concept",
        ]
    ] | None = Field(default=None, max_length=10)
    document_ids: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    mode: Literal["lexical", "semantic", "hybrid"] | None = None


class UnifiedSearchResponse(ApiModel):
    results: list[SearchResultItem]
    total: int
    limit: int
    offset: int
    query: str
    retrieval_mode: str
    fallback_reason: str | None = None
    by_type: dict[str, int]


# ---------------------------------------------------------------------------
# M42: Smart Revision & Memory Retention Engine.
# Unified revision queue and review submission for spaced-repetition review
# of flashcards and quiz questions.
# ---------------------------------------------------------------------------


class RevisionQueueItem(ApiModel):
    """One item in the learner's revision queue."""

    itemId: str
    itemType: Literal["quiz", "flashcard"]
    topicId: str | None = None
    resourceId: str | None = None
    topic: str
    title: str
    prompt: str
    questionType: Literal["multiple_choice", "short_answer"] | None = None
    options: list[dict[str, str]] | None = None
    explanation: str | None = None
    lastResult: Literal["remembered", "partially", "forgotten"]
    lastAttemptedAt: str | None = None
    dueAt: str
    citations: list[CitationResponse] = Field(default_factory=list)
    sourceType: Literal["quiz", "content_resource"]
    sectionTitle: str | None = None
    sectionType: Literal["heading", "body", "flashcard", "question", "summary"] | None = None
    resourceTitle: str | None = None


class RevisionQueueResponse(ApiModel):
    items: list[RevisionQueueItem]
    total: int
    limit: int
    offset: int

# ---------------------------------------------------------------------------
# M43: Personal AI Learning Companion.
# Unified conversational interface for grounded Q&A, quiz revision,
# progress summaries, recommendations, and study-plan management.
# ---------------------------------------------------------------------------


class CompanionMessageMode(str):
    pass


class CompanionMessageRequest(ApiModel):
    """One turn in the conversational companion interface."""
    message: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=80)
    mode: Literal["ask", "quiz", "revision", "progress", "recommend", "manage"] | None = None
    document_ids: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(default=5, ge=1, le=20)


class CompanionAction(ApiModel):
    """A suggested follow-up action the companion can execute."""
    action: Literal[
        "start_quiz",
        "start_revision",
        "start_session",
        "create_path",
        "accept_recommendation",
        "dismiss_recommendation",
        "none",
    ]
    description: str
    payload: dict[str, Any] = {}
    requires_confirmation: bool = False


class CompanionMessageResponse(ApiModel):
    """Companion reply with citations and optional suggested action."""
    message_id: str
    conversation_id: str
    role: Literal["assistant"]
    content: str
    status: Literal["answered", "insufficient_evidence", "model_unavailable", "error"]
    citations: list[CitationResponse] = Field(default_factory=list)
    retrieval_mode: Literal["lexical", "semantic", "hybrid", "lexical_fallback"] | None = None
    fallback_reason: str | None = None
    action: CompanionAction | None = None
    requires_confirmation: bool = False
    created_at: str


class CompanionConversationResponse(ApiModel):
    """One persisted conversation."""
    conversation_id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str


class CompanionConversationListResponse(ApiModel):
    conversations: list[CompanionConversationResponse]
    total: int
    limit: int
    offset: int


class CompanionConversationCreateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)


class CompanionMessage(ApiModel):
    """One persisted message in a conversation."""
    message_id: str
    conversation_id: str
    role: Literal["user", "assistant"]
    content: str
    citations: list[CitationResponse] = Field(default_factory=list)
    status: Literal["ok", "insufficient_evidence", "model_unavailable", "error"]
    created_at: str


class CompanionMessagesResponse(ApiModel):
    messages: list[CompanionMessage]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# M45: AI Observability & Model Evaluation Dashboard.
# Model execution telemetry and quality evaluation are admin-only.
# No user IDs, document IDs, prompts, secrets, or raw model outputs are stored.
# ---------------------------------------------------------------------------


class ModelTaskOutcome(str):
    pass


class ModelObservationResponse(ApiModel):
    task_type: Literal["tutor", "quiz", "flashcard", "progress", "recommend", "manage"]
    provider: Literal["local"]
    model_id: str
    outcome: Literal["success", "timeout", "cancellation", "failure"]
    latency_ms: float
    fallback_occurred: bool
    citation_validation: Literal["valid", "invalid", "not_applicable"] | None = None
    abstention: bool | None = None
    timestamp: str


class ModelLatencySummary(ApiModel):
    count: int
    p50_ms: float
    p95_ms: float
    max_ms: float


class ModelMetricsSnapshotResponse(ApiModel):
    task_counts: dict[str, int]
    outcome_counts: dict[str, int]
    provider_counts: dict[str, int]
    total_observations: int
    success_rate: float | None = None
    failure_rate: float | None = None
    fallback_count: int
    fallback_rate: float | None = None
    latency_ms: ModelLatencySummary
    recent_failures: list[dict[str, Any]]


class ModelEvaluationCreateRequest(ApiModel):
    task_type: Literal["tutor", "quiz", "flashcard", "progress", "recommend", "manage"]
    dataset: Literal["retrieval_eval", "generated", "quality_v2", "observability"]
    metric: Literal[
        "retrieval_quality",
        "citation_validity",
        "evidence_coverage",
        "quiz_schema_validity",
        "model_failure_rate",
        "model_timeout_rate",
    ]


class ModelEvaluationResponse(ApiModel):
    task_type: str
    dataset: str
    metric: str
    value: float | dict[str, Any] | None = None
    details: dict[str, Any] | None = None
    evaluated_at: str


# ---------------------------------------------------------------------------
# M57: Model discovery & health checks.
# ---------------------------------------------------------------------------


class ModelDiscoveryRequest(ApiModel):
    model_id: str | None = None


class ModelDiscoveryResponse(ApiModel):
    models: list[str]
    available: bool
    error: str | None = None


class ModelHealthCheckRequest(ApiModel):
    model_id: str | None = None


class ModelHealthResponse(ApiModel):
    model_id: str
    available: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# M59: AI Content Verification & Trust Engine.
# Deterministic verification of AI-generated content against source evidence.
# Outcomes: verified, partially_verified, unverified, rejected.
# ---------------------------------------------------------------------------


class VerificationOutcome(str):
    pass


class VerificationCheckDetail(ApiModel):
    """Result of one individual verification check."""

    check_name: str
    passed: bool
    detail: str | None = None


class VerifyTutorAnswerRequest(ApiModel):
    question: str = Field(min_length=1, max_length=2_000)
    answer_text: str = Field(min_length=1, max_length=8_000)
    citations: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    user_id: str | None = Field(default=None, min_length=1, max_length=80)


class VerifyQuizQuestionRequest(ApiModel):
    question: dict[str, Any]
    citations: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    chunks: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


class VerifyKnowledgeGraphRelationshipRequest(ApiModel):
    relationship: dict[str, Any]
    chunks: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


class VerifyMultimodalContentRequest(ApiModel):
    extracted_text: str = Field(min_length=1, max_length=10_000)
    source_type: Literal["image", "pdf", "document"]
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class VerificationResultSchema(ApiModel):
    outcome: Literal["verified", "partially_verified", "unverified", "rejected"]
    confidence: float = Field(ge=0.0, le=1.0)
    checks: dict[str, Any]
    reason: str | None = None
    citations_valid: bool
    evidence_coverage: float = Field(ge=0.0, le=1.0)
    unsupported_claim_count: int = Field(ge=0)
    schema_valid: bool


class VerifyTutorAnswerResponse(ApiModel):
    verification_id: str
    question: str
    answer_text: str
    outcome: Literal["verified", "partially_verified", "unverified", "rejected"]
    confidence: float
    checks: dict[str, Any]
    reason: str | None
    citations_valid: bool
    evidence_coverage: float
    unsupported_claim_count: int
    schema_valid: bool
    human_readable: str
    verified_at: str


class VerifyQuizQuestionResponse(ApiModel):
    verification_id: str
    question_type: str
    outcome: Literal["verified", "partially_verified", "unverified", "rejected"]
    confidence: float
    checks: dict[str, Any]
    reason: str | None
    citations_valid: bool
    evidence_coverage: float
    unsupported_claim_count: int
    schema_valid: bool
    human_readable: str
    verified_at: str


class VerifyKnowledgeGraphRelationshipResponse(ApiModel):
    verification_id: str
    relationship_id: str
    outcome: Literal["verified", "partially_verified", "unverified", "rejected"]
    confidence: float
    checks: dict[str, Any]
    reason: str | None
    citations_valid: bool
    evidence_coverage: float
    unsupported_claim_count: int
    schema_valid: bool
    human_readable: str
    verified_at: str


class VerifyMultimodalContentResponse(ApiModel):
    verification_id: str
    source_type: Literal["image", "pdf", "document"]
    outcome: Literal["verified", "partially_verified", "unverified", "rejected"]
    confidence: float
    checks: dict[str, Any]
    reason: str | None
    citations_valid: bool
    evidence_coverage: float
    unsupported_claim_count: int
    schema_valid: bool
    human_readable: str
    verified_at: str


class BatchVerificationRequest(ApiModel):
    verifications: list[dict[str, Any]] = Field(
        default_factory=list, max_length=50
    )


class BatchVerificationResponse(ApiModel):
    results: list[VerificationResultSchema]
    overall_outcome: Literal[
        "verified", "partially_verified", "unverified", "rejected"
    ]
    total_verified: int
    total_rejected: int
    verified_at: str


# ---------------------------------------------------------------------------
# M60: Unified Learning Journey & Experience Orchestration.
# Coherent journey stages, unified dashboard, next-action recommendations.
# ---------------------------------------------------------------------------


class JourneyStage(ApiModel):
    stageId: str
    label: str
    description: str
    completed: bool
    evidence: dict[str, Any]
    stageOrder: int


class JourneyProgress(ApiModel):
    completionPercent: int
    completedStages: int
    totalStages: int
    currentStage: JourneyStage | None
    stages: list[JourneyStage]


class DashboardGoal(ApiModel):
    goalId: str
    title: str
    description: str
    type: str
    priority: int
    count: int | None = None
    topicId: str | None = None
    topic: str | None = None
    mastery: float | None = None
    estimatedMinutes: int | None = None


class ContinueLearningItem(ApiModel):
    itemId: str
    title: str
    type: str
    topicId: str | None = None
    topic: str | None = None
    startedAt: str | None = None
    lastOutcome: str | None = None
    occurredAt: str | None = None


class TodayTask(ApiModel):
    taskId: str
    title: str
    type: str
    topicId: str | None = None
    topic: str | None = None
    priority: int
    estimatedMinutes: int
    status: str
    reasons: list[str] = []
    dueToday: bool = False
    dueAt: str | None = None
    lastResult: str | None = None


class DueRevisions(ApiModel):
    totalDue: int
    dueCount: int
    needsAttentionCount: int
    items: list[dict[str, Any]] = []


class RecentActivityItem(ApiModel):
    activityId: str
    kind: str
    topic: str
    outcome: str | None = None
    occurredAt: str | None = None
    prompt: str | None = None


class LearningPathProgressItem(ApiModel):
    pathId: str
    title: str
    status: str
    createdAt: str
    updatedAt: str


class RecommendedNextAction(ApiModel):
    action: str
    title: str
    description: str
    stageId: str
    priority: int
    topicId: str | None = None
    topic: str | None = None
    requiresConfirmation: bool


class JourneyDashboardResponse(ApiModel):
    generatedAt: str
    userId: str
    journey: JourneyProgress
    dashboard: dict[str, Any]
    analytics: dict[str, Any]
    studyCoachPlan: dict[str, Any]
    revisionQueue: dict[str, Any]
    learningPaths: list[dict[str, Any]]
    documents: list[dict[str, Any]]
    images: list[dict[str, Any]]
    knowledgeConcepts: list[dict[str, Any]]
    contentResources: list[dict[str, Any]]


class JourneyOverviewResponse(ApiModel):
    generatedAt: str
    userId: str
    totals: dict[str, Any]
    weakTopicCount: int
    activePathCount: int
    documentCount: int
    totalRevisionItems: int


class StageProgressionResponse(ApiModel):
    generatedAt: str
    userId: str
    completionPercent: int
    completedStages: int
    totalStages: int
    stages: list[JourneyStage]


class NextActionResponse(ApiModel):
    action: str | None
    title: str | None
    description: str | None
    stageId: str | None
    priority: int | None
    topicId: str | None = None
    topic: str | None = None
    requiresConfirmation: bool = False


# ---------------------------------------------------------------------------
# M61: Collaborative Learning & Study Groups.
# Study groups, sessions, notes, goals, and revision items.
# ---------------------------------------------------------------------------


class StudyGroupCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class StudyGroupResponse(ApiModel):
    groupId: str
    ownerId: str
    title: str
    description: str
    status: str
    memberCount: int
    createdAt: str
    updatedAt: str
    userRole: str


class StudyGroupListResponse(ApiModel):
    groups: list[StudyGroupResponse]


class StudyGroupJoinCodeRequest(ApiModel):
    joinCode: str = Field(min_length=1, max_length=128)


class StudyGroupInvitationCreateRequest(ApiModel):
    targetUsername: str = Field(min_length=3, max_length=64)
    expiresInDays: int = Field(default=7, ge=1, le=90)


class StudyGroupInvitationResponse(ApiModel):
    invitationId: str
    spaceId: str
    inviterUserId: str
    targetUsername: str
    expiresAt: str
    status: str


class StudyGroupInvitationListResponse(ApiModel):
    invitations: list[StudyGroupInvitationResponse]


class StudyGroupMember(ApiModel):
    userId: str
    username: str | None
    role: str
    joinedAt: str


class StudyGroupMemberListResponse(ApiModel):
    members: list[StudyGroupMember]


class StudyGroupMemberRoleRequest(ApiModel):
    role: str


# Study session schemas.
class StudySessionCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=160)
    status: str = Field(default="scheduled")


class StudySessionResponse(ApiModel):
    sessionId: str
    spaceId: str
    title: str
    topic: str
    status: str
    createdBy: str
    createdAt: str
    startedAt: str | None
    completedAt: str | None
    outcomeNote: str | None


class StudySessionListResponse(ApiModel):
    sessions: list[StudySessionResponse]
    total: int
    limit: int
    offset: int


class StudySessionStatusUpdateRequest(ApiModel):
    status: str
    outcomeNote: str | None = Field(default=None, max_length=1000)


class StudySessionParticipant(ApiModel):
    userId: str
    username: str | None
    role: str
    joinedAt: str
    leftAt: str | None


class StudySessionParticipantListResponse(ApiModel):
    participants: list[StudySessionParticipant]


# Shared note schemas.
class SharedNoteCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)


class SharedNoteResponse(ApiModel):
    noteId: str
    spaceId: str
    title: str
    content: str
    createdBy: str
    updatedBy: str
    createdAt: str
    updatedAt: str


class SharedNoteListResponse(ApiModel):
    notes: list[SharedNoteResponse]
    total: int
    limit: int
    offset: int


class SharedNoteUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1)


# Group goal schemas.
class GroupStudyGoalCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    targetTopic: str | None = Field(default=None, max_length=160)


class GroupStudyGoalResponse(ApiModel):
    goalId: str
    spaceId: str
    title: str
    targetTopic: str | None
    status: str
    createdBy: str
    createdAt: str
    completedAt: str | None


class GroupStudyGoalListResponse(ApiModel):
    goals: list[GroupStudyGoalResponse]


class GroupStudyGoalStatusUpdateRequest(ApiModel):
    status: str


# Group revision item schemas.
class GroupRevisionItemCreateRequest(ApiModel):
    itemType: str
    itemId: str
    topic: str | None = Field(default=None, max_length=160)
    nextDueDate: str | None = None
    assignedTo: str | None = Field(default=None, max_length=80)


class GroupRevisionItemResponse(ApiModel):
    itemId: str
    spaceId: str
    itemType: str
    itemId: str
    topic: str | None
    nextDueDate: str
    status: str
    assignedTo: str | None
    createdAt: str
    updatedAt: str


class GroupRevisionItemListResponse(ApiModel):
    items: list[GroupRevisionItemResponse]


# ---------------------------------------------------------------------------
# M62: Collaborative AI Study Rooms.
# AI-powered group study rooms with sessions, activities, and summaries.
# ---------------------------------------------------------------------------


class StudyRoomCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=160)
    objective: str | None = Field(default=None, max_length=2000)
    status: str = Field(default="scheduled")


class StudyRoomResponse(ApiModel):
    roomId: str
    spaceId: str
    title: str
    topic: str
    objective: str | None
    status: str
    createdBy: str
    createdAt: str
    startedAt: str | None
    completedAt: str | None
    summary: str | None
    summaryGeneratedAt: str | None


class StudyRoomListResponse(ApiModel):
    rooms: list[StudyRoomResponse]
    total: int
    limit: int
    offset: int


class StudyRoomUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    topic: str | None = Field(default=None, min_length=1, max_length=160)
    objective: str | None = Field(default=None, max_length=2000)
    status: str | None = None


class StudyRoomParticipantResponse(ApiModel):
    userId: str
    username: str | None
    role: str
    joinedAt: str
    leftAt: str | None


class StudyRoomParticipantListResponse(ApiModel):
    participants: list[StudyRoomParticipantResponse]


class StudyRoomActivityCreateRequest(ApiModel):
    activityType: str
    topic: str | None = Field(default=None, max_length=160)
    question: str | None = Field(default=None)
    answer: str | None = None
    result: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StudyRoomActivityResponse(ApiModel):
    activityId: str
    roomId: str
    userId: str
    activityType: str
    topic: str | None
    question: str | None
    answer: str | None
    result: str | None
    metadata: dict[str, Any]
    createdAt: str


class StudyRoomActivityListResponse(ApiModel):
    activities: list[StudyRoomActivityResponse]
    total: int
    limit: int
    offset: int


class StudyRoomSummaryCreateRequest(ApiModel):
    topicsCovered: list[str]
    questionsAttempted: int
    commonMistakes: list[str]
    conceptsNeedingRevision: list[str]
    unresolvedQuestions: list[str]
    followUpRecommendations: list[str]
    aiGenerated: bool


class StudyRoomSummaryResponse(ApiModel):
    summaryId: str
    roomId: str
    topicsCovered: list[str]
    questionsAttempted: int
    commonMistakes: list[str]
    conceptsNeedingRevision: list[str]
    unresolvedQuestions: list[str]
    followUpRecommendations: list[str]
    aiGenerated: bool
    createdAt: str


# AI-powered room actions.
class RoomAIExplainRequest(ApiModel):
    concept: str = Field(min_length=1, max_length=500)


class RoomAIQuizRequest(ApiModel):
    topic: str = Field(min_length=1, max_length=200)
    questionCount: int = Field(default=5, ge=1, le=10)


class RoomAIHintRequest(ApiModel):
    question: str = Field(min_length=1, max_length=1000)
    answer: str | None = Field(default=None, max_length=2000)


class RoomAIExplainAnswerRequest(ApiModel):
    question: str = Field(min_length=1, max_length=1000)
    submittedAnswer: str = Field(min_length=1, max_length=2000)


class RoomAISummarizeRequest(ApiModel):
    pass


class RoomAIResponse(ApiModel):
    content: str
    citations: list[dict[str, Any]] = []
    status: str
    metadata: dict[str, Any] = {}


class RoomAIQuizResponse(ApiModel):
    quizId: str | None
    topic: str
    questions: list[dict[str, Any]]
    status: str


# ---------------------------------------------------------------------------
# M63: Collaborative AI Course Builder & Curriculum Generator.
# Course creation, curriculum management, and progress tracking.
# ---------------------------------------------------------------------------


class CourseCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    spaceId: str | None = Field(default=None, min_length=1, max_length=80)
    sourceType: str = Field(default="document")
    difficultyLevel: str | None = Field(
        default=None,
        description="foundational, building, practice, or advanced",
    )
    estimatedWorkloadMinutes: int | None = Field(
        default=None, ge=1
    )


class CourseResponse(ApiModel):
    courseId: str
    userId: str
    spaceId: str | None
    title: str
    description: str
    sourceType: str
    sourceRefs: list[dict[str, Any]]
    status: str
    difficultyLevel: str | None
    estimatedWorkloadMinutes: int | None
    createdAt: str
    updatedAt: str


class CourseListResponse(ApiModel):
    courses: list[CourseResponse]


class CourseUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    status: str | None = None
    difficultyLevel: str | None = None
    estimatedWorkloadMinutes: int | None = None


class CourseModuleCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    orderIndex: int = Field(default=0, ge=0)


class CourseModuleResponse(ApiModel):
    moduleId: str
    courseId: str
    title: str
    description: str
    orderIndex: int
    status: str
    createdAt: str
    updatedAt: str


class CourseModuleListResponse(ApiModel):
    modules: list[CourseModuleResponse]


class CourseTopicCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    orderIndex: int = Field(default=0, ge=0)
    prerequisiteTopicIds: list[str] = Field(default_factory=list)
    difficultyLevel: str | None = None
    estimatedMinutes: int | None = None


class CourseTopicResponse(ApiModel):
    topicId: str
    moduleId: str
    title: str
    description: str
    orderIndex: int
    prerequisiteTopicIds: list[str]
    difficultyLevel: str | None
    estimatedMinutes: int | None
    status: str
    createdAt: str
    updatedAt: str


class CourseTopicListResponse(ApiModel):
    topics: list[CourseTopicResponse]


class CourseObjectiveCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    orderIndex: int = Field(default=0, ge=0)


class CourseObjectiveResponse(ApiModel):
    objectiveId: str
    courseId: str
    title: str
    description: str
    orderIndex: int
    status: str
    createdAt: str
    updatedAt: str


class CourseObjectiveListResponse(ApiModel):
    objectives: list[CourseObjectiveResponse]


class CourseAssessmentCreateRequest(ApiModel):
    topicId: str | None = None
    assessmentType: str = Field(
        default="quiz",
        description="quiz, flashcard, or review",
    )
    quizId: str | None = None


class CourseAssessmentResponse(ApiModel):
    assessmentId: str
    courseId: str
    topicId: str | None
    assessmentType: str
    quizId: str | None
    status: str
    createdAt: str
    completedAt: str | None


class CourseAssessmentListResponse(ApiModel):
    assessments: list[CourseAssessmentResponse]


class CourseProgressCreateRequest(ApiModel):
    topicId: str
    status: str = Field(default="not_started")
    masteryScore: float | None = None
    completedAt: str | None = None


class CourseProgressResponse(ApiModel):
    courseId: str
    userId: str
    topicId: str
    status: str
    masteryScore: float | None
    completedAt: str | None


class CourseProgressUpdateRequest(ApiModel):
    status: str | None = None
    masteryScore: float | None = None
    completedAt: str | None = None


class CourseBuildRequest(ApiModel):
    topic: str = Field(min_length=1, max_length=200)
    spaceId: str | None = Field(default=None, min_length=1, max_length=80)
    documentIds: list[str] | None = Field(default=None, max_length=50)
    limit: int = Field(default=10, ge=1, le=50)
    mode: str | None = None
    difficultyLevel: str | None = None


class CourseBuildResponse(ApiModel):
    courseId: str | None
    userId: str
    spaceId: str | None
    title: str
    description: str
    sourceType: str
    sourceRefs: list[dict[str, Any]]
    status: str
    difficultyLevel: str | None
    estimatedWorkloadMinutes: int | None
    createdAt: str
    updatedAt: str
    modules: list[dict[str, Any]]


class CourseCurriculumResponse(ApiModel):
    courseId: str
    title: str
    description: str
    modules: list[dict[str, Any]]


class CourseNextTopicResponse(ApiModel):
    moduleId: str | None
    moduleTitle: str | None
    topicId: str | None
    title: str | None
    description: str | None
    difficultyLevel: str | None
    estimatedMinutes: int | None
    prerequisitesMet: bool | None


class CourseAdaptiveRecommendationsResponse(ApiModel):
    recommendations: list[dict[str, Any]]


# AI-powered course content generation.


class CourseContentGenerateRequest(ApiModel):
    courseId: str
    topicId: str


class CourseContentGenerateResponse(ApiModel):
    courseId: str
    topicId: str
    sectionId: str | None
    title: str
    content: str
    citations: list[dict[str, Any]]
    status: str


# ---------------------------------------------------------------------------
# M64: Collaborative AI Assessment & Competency Management schemas.
# Assessment creation, taking, scoring, competency tracking.
# ---------------------------------------------------------------------------


class AssessmentCreateRequest(ApiModel):
    courseId: str | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    assessmentType: str = Field(
        default="custom",
        description="quiz, diagnostic, practice, final, or custom",
    )
    status: str = Field(default="draft", description="draft, published, or archived")


class AssessmentResponse(ApiModel):
    assessmentId: str
    userId: str
    courseId: str | None
    title: str
    description: str
    assessmentType: str
    status: str
    createdAt: str
    updatedAt: str


class AssessmentListResponse(ApiModel):
    assessments: list[AssessmentResponse]


class AssessmentUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    status: str | None = Field(
        default=None, description="draft, published, or archived"
    )


class AssessmentQuestionCreateRequest(ApiModel):
    questionType: str = Field(
        description="multiple_choice, multiple_select, short_answer, long_answer, or code"
    )
    prompt: str = Field(min_length=1, max_length=5000)
    answerKey: str
    acceptedAnswers: list[str] | None = None
    explanation: str | None = None
    difficultyLevel: str | None = Field(
        default=None, description="foundational, building, practice, or advanced"
    )
    citations: list[dict[str, Any]] | None = None


class AssessmentQuestionResponse(ApiModel):
    questionId: str
    assessmentId: str
    questionType: str
    prompt: str
    answerKey: str | None = None
    acceptedAnswers: list[str] = []
    explanation: str | None = None
    difficultyLevel: str | None = None
    citations: list[dict[str, Any]] = []
    createdAt: str


class AssessmentQuestionListResponse(ApiModel):
    questions: list[AssessmentQuestionResponse]


class AssessmentAttemptCreateRequest(ApiModel):
    status: str = Field(
        default="in_progress", description="in_progress, completed, or abandoned"
    )


class AssessmentAttemptResponse(ApiModel):
    attemptId: str
    assessmentId: str
    userId: str
    status: str
    createdAt: str
    completedAt: str | None


class AssessmentAttemptListResponse(ApiModel):
    attempts: list[AssessmentAttemptResponse]


class QuestionAnswerSubmitRequest(ApiModel):
    questionType: str = Field(
        description="multiple_choice, multiple_select, short_answer, long_answer, or code"
    )
    submittedAnswer: str
    answerKey: str | None = None
    acceptedAnswers: list[str] | None = None
    explanation: str | None = None
    result: str = Field(
        description="correct, incorrect, partial, or ambiguous"
    )
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    citations: list[dict[str, Any]] | None = None


class QuestionAnswerResponse(ApiModel):
    attemptId: str
    questionId: str
    userId: str
    submittedAnswer: str
    answerKey: str | None = None
    acceptedAnswers: list[str] = []
    explanation: str | None = None
    result: str | None = None
    score: float | None = None
    citations: list[dict[str, Any]] = []
    createdAt: str


class QuestionAnswerListResponse(ApiModel):
    answers: list[QuestionAnswerResponse]


class CompetencyCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    parentCompetencyId: str | None = None
    level: int = Field(default=0, ge=0)


class CompetencyResponse(ApiModel):
    competencyId: str
    userId: str
    title: str
    description: str
    parentCompetencyId: str | None
    level: int
    createdAt: str
    updatedAt: str


class CompetencyListResponse(ApiModel):
    competencies: list[CompetencyResponse]


class CompetencyUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    parentCompetencyId: str | None = None
    level: int | None = Field(default=None, ge=0)


class CompetencyPrerequisiteLinkRequest(ApiModel):
    competencyId: str
    prerequisiteCompetencyId: str


class CompetencyPrerequisiteResponse(ApiModel):
    competencyId: str
    title: str
    level: int


class ObjectiveCompetencyLinkRequest(ApiModel):
    objectiveId: str
    competencyId: str


class QuestionCompetencyLinkRequest(ApiModel):
    questionId: str
    competencyId: str


class CompetencyMasteryResponse(ApiModel):
    competencyId: str
    userId: str
    masteryScore: float
    evidenceCount: int
    lastAssessedAt: str | None


class CompetencyMasteryUpdateRequest(ApiModel):
    masteryScore: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# M65: Learning Gap Intelligence & Remediation Engine schemas.
# Gap detection, root-cause analysis, remediation, reassessment.
# ---------------------------------------------------------------------------


class LearningGapResponse(ApiModel):
    gapId: str
    userId: str
    topicId: str | None
    topic: str | None
    competencyId: str | None
    gapType: str
    severity: str
    evidenceStrength: str
    status: str
    recurrence: int
    detectedAt: str
    acknowledgedAt: str | None
    resolvedAt: str | None
    dismissedAt: str | None
    rootCauseHypothesis: str | None
    rootCauseEvidence: str | None
    baselineMastery: float | None
    reassessedMastery: float | None


class LearningGapListResponse(ApiModel):
    gaps: list[LearningGapResponse]


class GapEvidenceResponse(ApiModel):
    evidenceId: str
    gapId: str
    evidenceType: str
    referenceId: str
    description: str | None
    recordedAt: str


class GapEvidenceListResponse(ApiModel):
    evidence: list[GapEvidenceResponse]


class GapRemediationResponse(ApiModel):
    remediationId: str
    gapId: str
    activityType: str
    title: str
    description: str | None
    priorityScore: float
    rationale: str | None
    status: str
    createdAt: str
    acceptedAt: str | None
    completedAt: str | None


class GapRemediationListResponse(ApiModel):
    remediations: list[GapRemediationResponse]


class RemediationAcceptRequest(ApiModel):
    pass


class RemediationDismissRequest(ApiModel):
    pass


class RemediationCompleteRequest(ApiModel):
    pass


class RemediationReassessmentResponse(ApiModel):
    reassessmentId: str
    gapId: str
    activityId: str | None
    beforeMastery: float
    afterMastery: float
    improvement: float
    resolved: bool
    reassessedAt: str


class RemediationReassessmentListResponse(ApiModel):
    reassessments: list[RemediationReassessmentResponse]


class RemediationReassessmentCreateRequest(ApiModel):
    activityId: str | None
    beforeMastery: float = Field(ge=0.0, le=1.0)
    afterMastery: float = Field(ge=0.0, le=1.0)
    resolved: bool = False


class GapDetectionRequest(ApiModel):
    pass


class GapDetectionResponse(ApiModel):
    gapsDetected: int
    gaps: list[LearningGapResponse]


class GapRootCauseRequest(ApiModel):
    hypothesis: str | None = None
    evidence: str | None = None


class GapDismissRequest(ApiModel):
    reason: str | None = None


class LearningGapDashboardResponse(ApiModel):
    totalGaps: int
    criticalGaps: int
    moderateGaps: int
    minorGaps: int
    detectedGaps: int
    remediatingGaps: int
    resolvedGaps: int
    dismissedGaps: int
    gaps: list[LearningGapResponse]


# ---------------------------------------------------------------------------
# M66: AI Examination Simulator & Exam Preparation Engine schemas.
# Exam creation, taking, scoring, readiness.
# ---------------------------------------------------------------------------


class ExamCreateRequest(ApiModel):
    courseId: str | None = None
    title: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    durationMinutes: int = Field(ge=1)
    totalMarks: int = Field(ge=1)
    questionCount: int = Field(ge=1)
    passingScore: float = Field(ge=0.0, le=1.0)
    negativeMarking: float = Field(ge=0.0, le=1.0)
    instructions: str | None = None
    difficultyDistribution: dict[str, Any] | None = None
    status: str = Field(default="draft")


class ExamResponse(ApiModel):
    examId: str
    userId: str
    courseId: str | None
    title: str
    subject: str
    topic: str
    description: str | None
    durationMinutes: int
    totalMarks: int
    questionCount: int
    passingScore: float
    negativeMarking: float
    instructions: str | None
    difficultyDistribution: dict[str, Any]
    status: str
    createdAt: str
    updatedAt: str


class ExamListResponse(ApiModel):
    exams: list[ExamResponse]


class ExamUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    subject: str | None = Field(default=None, min_length=1, max_length=100)
    topic: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    durationMinutes: int | None = Field(default=None, ge=1)
    totalMarks: int | None = Field(default=None, ge=1)
    questionCount: int | None = Field(default=None, ge=1)
    passingScore: float | None = Field(default=None, ge=0.0, le=1.0)
    negativeMarking: float | None = Field(default=None, ge=0.0, le=1.0)
    instructions: str | None = Field(default=None)
    difficultyDistribution: dict[str, Any] | None = None
    status: str | None = None


class ExamSectionCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    questionCount: int = Field(ge=0)
    marks: int = Field(ge=0)
    position: int = Field(ge=0)


class ExamSectionResponse(ApiModel):
    sectionId: str
    examId: str
    title: str
    description: str | None
    questionCount: int
    marks: int
    position: int


class ExamQuestionCreateRequest(ApiModel):
    sectionId: str | None = None
    sourceQuestionId: str | None = None
    questionType: str
    prompt: str = Field(min_length=1, max_length=5000)
    answerKey: str | None = None
    acceptedAnswers: list[str] | None = None
    explanation: str | None = None
    marks: int = Field(ge=1)
    difficultyLevel: str | None = None
    position: int = Field(ge=0)
    citations: list[dict[str, Any]] | None = None


class ExamQuestionResponse(ApiModel):
    examQuestionId: str
    examId: str
    sectionId: str | None
    sourceQuestionId: str | None
    questionType: str
    prompt: str
    answerKey: str | None
    acceptedAnswers: list[str]
    explanation: str | None
    marks: int
    difficultyLevel: str | None
    position: int
    citations: list[dict[str, Any]]
    createdAt: str


class ExamQuestionListResponse(ApiModel):
    questions: list[ExamQuestionResponse]


class ExamAttemptCreateRequest(ApiModel):
    status: str = Field(default="in_progress")


class ExamAttemptResponse(ApiModel):
    attemptId: str
    examId: str
    userId: str
    startedAt: str
    completedAt: str | None
    status: str
    timeSpentMs: int
    totalMarksObtained: float | None
    percentage: float | None
    passed: bool | None
    autoSubmitted: bool


class ExamAttemptListResponse(ApiModel):
    attempts: list[ExamAttemptResponse]


class ExamAnswerSubmitRequest(ApiModel):
    submittedAnswer: str | None = None
    marksObtained: float = Field(ge=0.0)
    outcome: str
    isMarkedForReview: bool = False
    timeSpentMs: int | None = Field(default=None, ge=0)


class ExamAnswerResponse(ApiModel):
    examAnswerId: str
    attemptId: str
    examQuestionId: str
    submittedAnswer: str | None
    marksObtained: float
    outcome: str
    isMarkedForReview: bool
    timeSpentMs: int | None
    createdAt: str
    updatedAt: str


class ExamAnswerListResponse(ApiModel):
    answers: list[ExamAnswerResponse]


class ExamResultCreateRequest(ApiModel):
    attemptId: str
    totalMarks: int = Field(ge=0)
    marksObtained: float = Field(ge=0.0)
    percentage: float = Field(ge=0.0, le=100.0)
    passed: bool
    correctCount: int = Field(ge=0)
    incorrectCount: int = Field(ge=0)
    unansweredCount: int = Field(ge=0)
    partialCount: int = Field(ge=0)
    timeSpentMs: int | None = Field(default=None, ge=0)
    topicWise: dict[str, Any] | None = None
    competencyWise: dict[str, Any] | None = None
    difficultyWise: dict[str, Any] | None = None


class ExamResultResponse(ApiModel):
    resultId: str
    attemptId: str
    examId: str
    userId: str
    totalMarks: int
    marksObtained: float
    percentage: float
    passed: bool
    correctCount: int
    incorrectCount: int
    unansweredCount: int
    partialCount: int
    timeSpentMs: int | None
    topicWise: dict[str, Any]
    competencyWise: dict[str, Any]
    difficultyWise: dict[str, Any]
    createdAt: str


class ExamResultListResponse(ApiModel):
    results: list[ExamResultResponse]


class ExamReadinessResponse(ApiModel):
    examId: str
    userId: str
    readinessScore: float = Field(ge=0.0, le=1.0)
    readinessLevel: str
    evidenceBased: bool
    weakTopics: list[str]
    recommendedActions: list[str]
    calculatedAt: str


# ---------------------------------------------------------------------------
# M67: Predictive Learning Analytics & Student Performance Intelligence schemas.
# Performance aggregation, trend intelligence, predictions, recommendations.
# ---------------------------------------------------------------------------


class AnalyticsSnapshotResponse(ApiModel):
    snapshotId: str
    userId: str
    snapshotDate: str
    topicId: str | None
    topic: str | None
    masteryScore: float | None
    accuracy: float | None
    attemptCount: int
    correctCount: int
    incorrectCount: int
    ambiguousCount: int
    reviewCount: int
    overdueReviewCount: int
    timeSpentMs: int | None
    createdAt: str


class AnalyticsSnapshotListResponse(ApiModel):
    snapshots: list[AnalyticsSnapshotResponse]


class PerformancePredictionResponse(ApiModel):
    predictionId: str
    userId: str
    predictionType: str
    targetId: str | None
    targetName: str | None
    predictedValue: float | None
    confidence: float
    uncertainty: str
    evidence: dict[str, Any]
    limitations: dict[str, Any]
    isGuaranteed: bool
    createdAt: str
    expiresAt: str | None


class PerformancePredictionListResponse(ApiModel):
    predictions: list[PerformancePredictionResponse]


class PredictionModelVersionResponse(ApiModel):
    modelVersionId: str
    modelName: str
    version: str
    description: str | None
    createdAt: str
    isActive: bool
    validationMetrics: dict[str, Any]


class PredictionModelVersionListResponse(ApiModel):
    models: list[PredictionModelVersionResponse]


class TopicTrendResponse(ApiModel):
    trendId: str
    userId: str
    topicId: str
    topic: str
    measurementDate: str
    masteryScore: float | None
    accuracy: float | None
    attemptCount: int
    createdAt: str


class TopicTrendListResponse(ApiModel):
    trends: list[TopicTrendResponse]


class PerformanceTrendPoint(ApiModel):
    date: str
    masteryScore: float | None
    accuracy: float | None
    attemptCount: int


class TopicProgressTrend(ApiModel):
    topicId: str
    topic: str
    trend: list[PerformanceTrendPoint]
    improvement: float | None
    direction: str  # improving, declining, stable, insufficient_data


class CompetencyTrend(ApiModel):
    competencyId: str
    competencyName: str
    currentMastery: float | None
    masteryHistory: list[PerformanceTrendPoint]
    trendDirection: str


class ExamPerformanceHistory(ApiModel):
    examId: str
    examTitle: str
    subject: str
    topic: str
    percentage: float
    passed: bool
    takenAt: str
    durationMinutes: int | None


class PerformanceDashboardResponse(ApiModel):
    userId: str
    overallMastery: float | None
    overallAccuracy: float | None
    totalAttempts: int
    totalExamsTaken: int
    averageExamScore: float | None
    weakTopics: list[str]
    strongTopics: list[str]
    topicProgress: list[TopicProgressTrend]
    competencyTrends: list[CompetencyTrend]
    examHistory: list[ExamPerformanceHistory]
    activePredictions: list[PerformancePredictionResponse]
    recommendations: list[dict[str, Any]]
    generatedAt: str


class PerformancePredictionRequest(ApiModel):
    predictionType: str
    targetId: str | None = None
    targetName: str | None = None


class RecommendationItem(ApiModel):
    recommendationId: str
    type: str  # revise_topic, practice_competency, retake_exam, review_prerequisite, adjust_schedule, focus_gap
    title: str
    description: str
    evidence: dict[str, Any]
    reason: str
    priority: float
    targetId: str | None
    targetName: str | None


class RecommendationsResponse(ApiModel):
    userId: str
    recommendations: list[RecommendationItem]
    generatedAt: str


# ---------------------------------------------------------------------------
# M69: AI Lesson Planner & Teaching Studio schemas.
# Lesson planning, teaching sessions, attendance, learner questions.
# ---------------------------------------------------------------------------


class LessonPlanCreateRequest(ApiModel):
    classroomId: str | None = None
    title: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=200)
    studentLevel: str | None = Field(
        default=None, description="foundational, building, practice, or advanced"
    )
    durationMinutes: int = Field(ge=1)
    status: str = Field(default="draft")
    learningObjectives: list[str] | None = None
    prerequisiteConcepts: list[str] | None = None
    sourceMaterialRefs: list[dict[str, Any]] | None = None


class LessonPlanResponse(ApiModel):
    lessonPlanId: str
    userId: str
    classroomId: str | None
    title: str
    subject: str
    topic: str
    studentLevel: str | None
    durationMinutes: int
    status: str
    learningObjectives: list[str]
    prerequisiteConcepts: list[str]
    sourceMaterialRefs: list[dict[str, Any]]
    createdAt: str
    updatedAt: str
    publishedAt: str | None


class LessonPlanListResponse(ApiModel):
    lessonPlans: list[LessonPlanResponse]


class LessonPlanUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    subject: str | None = Field(default=None, min_length=1, max_length=100)
    topic: str | None = Field(default=None, min_length=1, max_length=200)
    studentLevel: str | None = None
    durationMinutes: int | None = Field(default=None, ge=1)
    status: str | None = None
    learningObjectives: list[str] | None = None
    prerequisiteConcepts: list[str] | None = None
    sourceMaterialRefs: list[dict[str, Any]] | None = None


class LessonPlanSectionCreateRequest(ApiModel):
    sectionType: str
    sectionTitle: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    citations: list[dict[str, Any]] | None = None
    position: int = Field(default=0, ge=0)
    orderIndex: int = Field(default=0, ge=0)


class LessonPlanSectionResponse(ApiModel):
    sectionId: str
    lessonPlanId: str
    sectionType: str
    sectionTitle: str
    content: str
    citations: list[dict[str, Any]]
    position: int
    orderIndex: int
    createdAt: str


class LessonPlanSectionListResponse(ApiModel):
    sections: list[LessonPlanSectionResponse]


class LessonSessionCreateRequest(ApiModel):
    lessonPlanId: str
    classroomId: str | None = None
    title: str = Field(min_length=1, max_length=200)
    scheduledAt: str | None = None
    status: str = Field(default="scheduled")


class LessonSessionResponse(ApiModel):
    sessionId: str
    lessonPlanId: str
    userId: str
    classroomId: str | None
    title: str
    scheduledAt: str | None
    status: str
    startedAt: str | None
    completedAt: str | None


class LessonSessionListResponse(ApiModel):
    sessions: list[LessonSessionResponse]


class LessonSessionUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    scheduledAt: str | None = None
    status: str | None = None
    startedAt: str | None = None
    completedAt: str | None = None


class LessonAttendanceRecord(ApiModel):
    sessionId: str
    userId: str
    joinedAt: str | None = None
    leftAt: str | None = None
    participationEvents: list[dict[str, Any]] | None = None


class LessonAttendanceResponse(ApiModel):
    attendanceId: str
    sessionId: str
    userId: str
    joinedAt: str | None
    leftAt: str | None
    participationEvents: list[dict[str, Any]]
    recordedAt: str


class LessonAttendanceListResponse(ApiModel):
    attendance: list[LessonAttendanceResponse]


class LessonQuestionRecord(ApiModel):
    sessionId: str
    userId: str
    questionText: str = Field(min_length=1)
    answeredAt: str | None = None
    answerText: str | None = None


class LessonQuestionResponse(ApiModel):
    questionId: str
    sessionId: str
    userId: str
    questionText: str
    answeredAt: str | None
    answerText: str | None
    recordedAt: str


class LessonQuestionListResponse(ApiModel):
    questions: list[LessonQuestionResponse]


class LessonPlanGenerateRequest(ApiModel):
    subject: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=200)
    studentLevel: str | None = Field(
        default=None, description="foundational, building, practice, or advanced"
    )
    durationMinutes: int = Field(ge=1)
    documentIds: list[str] | None = Field(default=None, max_length=50)
    spaceId: str | None = Field(default=None, min_length=1, max_length=80)
    limit: int = Field(default=10, ge=1, le=50)
    mode: str | None = None


class LessonPlanGenerateResponse(ApiModel):
    lessonPlanId: str
    userId: str
    title: str
    subject: str
    topic: str
    studentLevel: str | None
    durationMinutes: int
    sections: list[dict[str, Any]]
    evidenceGaps: list[str]
    status: str
    createdAt: str


# ---------------------------------------------------------------------------
# M70: Live Interactive Classroom & Virtual Learning Environment schemas.
# Classroom sessions, participants, presence, questions, polls, announcements,
# activity state, and session recovery.
# ---------------------------------------------------------------------------


class ClassroomSessionCreateRequest(ApiModel):
    lessonPlanId: str | None = None
    title: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=200)
    status: str = Field(default="scheduled")


class ClassroomSessionResponse(ApiModel):
    sessionId: str
    lessonPlanId: str | None
    educatorId: str
    title: str
    subject: str
    topic: str
    status: str
    startedAt: str | None
    endedAt: str | None
    createdAt: str
    updatedAt: str


class ClassroomSessionListResponse(ApiModel):
    sessions: list[ClassroomSessionResponse]


class ClassroomSessionUpdateRequest(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    subject: str | None = Field(default=None, min_length=1, max_length=100)
    topic: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = None
    startedAt: str | None = None
    endedAt: str | None = None


class ClassroomParticipantResponse(ApiModel):
    participantId: str
    sessionId: str
    userId: str
    role: str
    joinedAt: str
    leftAt: str | None
    isConnected: bool


class ClassroomParticipantJoinRequest(ApiModel):
    role: str = Field(default="learner")


class ClassroomPresenceResponse(ApiModel):
    sessionId: str
    userId: str
    lastHeartbeat: str
    isOnline: bool


class ClassroomPresenceListResponse(ApiModel):
    presence: list[ClassroomPresenceResponse]


class ClassroomQuestionRecord(ApiModel):
    sessionId: str
    userId: str
    questionText: str = Field(min_length=1, max_length=1000)


class ClassroomQuestionResponse(ApiModel):
    questionId: str
    sessionId: str
    userId: str
    questionText: str
    isAnswered: bool
    answerText: str | None
    answeredAt: str | None
    createdAt: str


class ClassroomQuestionListResponse(ApiModel):
    questions: list[ClassroomQuestionResponse]


class ClassroomQuestionAnswerRequest(ApiModel):
    answerText: str = Field(min_length=1)


class ClassroomPollCreateRequest(ApiModel):
    question: str = Field(min_length=1, max_length=1000)
    options: list[str] = Field(min_length=2, max_length=10)
    correctOptionIndex: int | None = Field(default=None, ge=0)


class ClassroomPollResponse(ApiModel):
    pollId: str
    sessionId: str
    educatorId: str
    question: str
    options: list[str]
    correctOptionIndex: int | None
    isOpen: bool
    createdAt: str
    closedAt: str | None


class ClassroomPollListResponse(ApiModel):
    polls: list[ClassroomPollResponse]


class ClassroomPollRespondRequest(ApiModel):
    optionIndex: int = Field(ge=0)


class ClassroomPollResultsResponse(ApiModel):
    pollId: str
    sessionId: str
    educatorId: str
    question: str
    options: list[str]
    optionCounts: list[int]
    totalResponses: int
    correctOptionIndex: int | None
    isOpen: bool
    createdAt: str
    closedAt: str | None


class ClassroomAnnouncementCreateRequest(ApiModel):
    sessionId: str
    educatorId: str
    message: str = Field(min_length=1, max_length=1000)


class ClassroomAnnouncementResponse(ApiModel):
    announcementId: str
    sessionId: str
    educatorId: str
    message: str
    createdAt: str


class ClassroomAnnouncementListResponse(ApiModel):
    announcements: list[ClassroomAnnouncementResponse]


class ClassroomActivityStateUpdateRequest(ApiModel):
    stateType: str
    stateData: dict[str, Any]


class ClassroomActivityStateResponse(ApiModel):
    stateId: str
    sessionId: str
    educatorId: str
    stateType: str
    stateData: dict[str, Any]
    updatedAt: str


class ClassroomSessionRecoveryResponse(ApiModel):
    session: ClassroomSessionResponse
    participants: list[ClassroomParticipantResponse]
    presence: list[ClassroomPresenceResponse]
    questions: list[ClassroomQuestionResponse]
    announcements: list[ClassroomAnnouncementResponse]
    activityState: ClassroomActivityStateResponse | None


# ---------------------------------------------------------------------------
# M71: Classroom Intelligence & Teaching Effectiveness Analytics schemas.
# Performance analytics, teaching effectiveness, recommendations.
# ---------------------------------------------------------------------------


class ClassroomAnalyticsSnapshotResponse(ApiModel):
    snapshotId: str
    sessionId: str
    snapshotDate: str
    userId: str
    totalParticipants: int
    activeParticipants: int
    totalQuestions: int
    answeredQuestions: int
    totalPolls: int
    totalPollResponses: int
    averageAssessmentScore: float | None
    totalAssessments: int
    totalAssignmentsCompleted: int
    totalAssignmentsAssigned: int
    averageMasteryScore: float | None
    createdAt: str


class ClassroomAnalyticsSnapshotListResponse(ApiModel):
    snapshots: list[ClassroomAnalyticsSnapshotResponse]


class ClassroomTopicPerformanceResponse(ApiModel):
    topicPerformanceId: str
    sessionId: str
    topicId: str
    topic: str
    totalAttempts: int
    correctAttempts: int
    incorrectAttempts: int
    ambiguousAttempts: int
    accuracy: float | None
    masteryScore: float | None
    lastUpdated: str


class ClassroomTopicPerformanceListResponse(ApiModel):
    topicPerformance: list[ClassroomTopicPerformanceResponse]


class ClassroomCompetencyPerformanceResponse(ApiModel):
    competencyPerformanceId: str
    sessionId: str
    competencyId: str
    competencyName: str
    totalAssessments: int
    passedAssessments: int
    averageMastery: float | None
    evidenceCount: int
    lastUpdated: str


class ClassroomCompetencyPerformanceListResponse(ApiModel):
    competencyPerformance: list[ClassroomCompetencyPerformanceResponse]


class ClassroomLearningGapTrendResponse(ApiModel):
    gapTrendId: str
    sessionId: str
    gapType: str
    gapDescription: str
    topicId: str | None
    topic: str | None
    competencyId: str | None
    severity: str
    affectedStudentCount: int
    totalStudents: int
    severityPercentage: float
    firstDetected: str
    lastUpdated: str


class ClassroomLearningGapTrendListResponse(ApiModel):
    gapTrends: list[ClassroomLearningGapTrendResponse]


class ClassroomAssessmentTrendResponse(ApiModel):
    assessmentTrendId: str
    sessionId: str
    assessmentType: str
    assessmentId: str | None
    assessmentTitle: str | None
    totalParticipants: int
    totalCompleted: int
    totalIncomplete: int
    averageScore: float | None
    minScore: float | None
    maxScore: float | None
    passCount: int
    failCount: int
    passRate: float | None
    lastUpdated: str


class ClassroomAssessmentTrendListResponse(ApiModel):
    assessmentTrends: list[ClassroomAssessmentTrendResponse]


class ClassroomTeachingEffectivenessResponse(ApiModel):
    effectivenessId: str
    sessionId: str
    educatorId: str
    effectivenessType: str
    indicatorName: str
    indicatorValue: float | None
    indicatorDescription: str | None
    evidenceCount: int
    dataSource: str
    measuredAt: str


class ClassroomTeachingEffectivenessListResponse(ApiModel):
    effectivenessIndicators: list[ClassroomTeachingEffectivenessResponse]


class ClassroomRecommendationResponse(ApiModel):
    recommendationId: str
    sessionId: str
    educatorId: str
    recommendationType: str
    title: str
    description: str
    rationale: str | None
    priorityScore: float
    targetTopic: str | None
    targetCompetency: str | None
    evidenceSummary: str | None
    status: str
    createdAt: str
    updatedAt: str


class ClassroomRecommendationListResponse(ApiModel):
    recommendations: list[ClassroomRecommendationResponse]


class ClassroomIntelligenceDashboardResponse(ApiModel):
    sessionId: str
    educatorId: str
    sessionTitle: str
    sessionTopic: str
    sessionStatus: str
    generatedAt: str
    sampleSize: int
    overallMetrics: dict[str, Any]
    topicPerformance: list[ClassroomTopicPerformanceResponse]
    competencyPerformance: list[ClassroomCompetencyPerformanceResponse]
    learningGapTrends: list[ClassroomLearningGapTrendResponse]
    assessmentTrends: list[ClassroomAssessmentTrendResponse]
    teachingEffectiveness: list[ClassroomTeachingEffectivenessResponse]
    recommendations: list[ClassroomRecommendationResponse]
    dataLimitations: list[str]


# ---------------------------------------------------------------------------
# M72: Communication & Learning Progress Portal schemas.
# ---------------------------------------------------------------------------


class ClassroomMessageCreateRequest(ApiModel):
    sessionId: str
    senderId: str
    content: str = Field(min_length=1, max_length=4000)
    messageType: str = Field(default="classroom")
    recipientId: str | None = None


class ClassroomMessageResponse(ApiModel):
    messageId: str
    sessionId: str
    senderId: str
    recipientId: str | None
    messageType: str
    content: str
    createdAt: str
    updatedAt: str
    isRead: bool = False
    readAt: str | None = None


class ClassroomMessageListResponse(ApiModel):
    messages: list[ClassroomMessageResponse]


class MarkMessageReadRequest(ApiModel):
    messageId: str


class UnreadMessageCount(ApiModel):
    sessionId: str
    unreadCount: int


class NotificationResponse(ApiModel):
    notificationId: str
    userId: str
    type: str
    title: str
    body: str
    relatedId: str | None
    relatedType: str | None
    isRead: bool
    readAt: str | None
    createdAt: str
    expiresAt: str | None


class NotificationListResponse(ApiModel):
    notifications: list[NotificationResponse]


class NotificationPreferencesResponse(ApiModel):
    userId: str
    emailEnabled: bool
    inAppEnabled: bool
    announcementEnabled: bool
    assignmentEnabled: bool
    feedbackEnabled: bool
    assessmentEnabled: bool
    progressUpdateEnabled: bool
    updatedAt: str


class NotificationPreferencesUpdateRequest(ApiModel):
    emailEnabled: bool | None = None
    inAppEnabled: bool | None = None
    announcementEnabled: bool | None = None
    assignmentEnabled: bool | None = None
    feedbackEnabled: bool | None = None
    assessmentEnabled: bool | None = None
    progressUpdateEnabled: bool | None = None


# Learner progress portal - observed data only, no estimates.
class ProgressAssessmentSummary(ApiModel):
    topicId: str
    topic: str
    totalAttempts: int
    correctAttempts: int
    accuracy: float | None
    lastUpdated: str | None


class ProgressRevisionSummary(ApiModel):
    topicId: str
    topic: str
    lastOutcome: str
    attemptedAt: str | None
    dueAt: str | None


class ProgressStudySessionSummary(ApiModel):
    sessionId: str
    topicId: str
    topic: str
    startedAt: str
    completedAt: str | None


class ProgressPortalResponse(ApiModel):
    userId: str
    assignments: list[dict[str, Any]]
    assessments: list[ProgressAssessmentSummary]
    revisions: list[ProgressRevisionSummary]
    studySessions: list[ProgressStudySessionSummary]
    messageCount: int
    unreadNotificationCount: int
    dataSources: list[str]


# ---------------------------------------------------------------------------
# M73: Institution Management & Multi-Tenant Education Platform schemas.
# ---------------------------------------------------------------------------


class InstitutionCreateRequest(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    domain: str | None = Field(default=None, max_length=200)


class InstitutionResponse(ApiModel):
    institutionId: str
    name: str
    domain: str | None
    status: str
    settings: dict[str, Any]
    createdBy: str
    createdAt: str
    updatedAt: str
    deletedAt: str | None


class InstitutionListResponse(ApiModel):
    institutions: list[InstitutionResponse]


class InstitutionUpdateRequest(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    domain: str | None = Field(default=None, max_length=200)
    settings: dict[str, Any] | None = None


class InstitutionMemberResponse(ApiModel):
    membershipId: str
    userId: str
    username: str
    role: str
    status: str
    invitedBy: str | None
    joinedAt: str | None
    updatedAt: str


class InstitutionMemberListResponse(ApiModel):
    members: list[InstitutionMemberResponse]


class AddInstitutionMemberRequest(ApiModel):
    userId: str
    role: str = Field(default="learner")


class SuspendMemberRequest(ApiModel):
    userId: str


class RevokeMemberRequest(ApiModel):
    userId: str


class InstitutionInvitationResponse(ApiModel):
    invitationId: str
    institutionId: str
    email: str
    role: str
    token: str
    inviterId: str
    expiresAt: str
    acceptedAt: str | None
    acceptedBy: str | None
    createdAt: str


class InstitutionInvitationListResponse(ApiModel):
    invitations: list[InstitutionInvitationResponse]


class CreateInvitationRequest(ApiModel):
    email: str
    role: str = Field(default="learner")
    expiresInDays: int = Field(default=7, ge=1, le=90)


class AcceptInvitationRequest(ApiModel):
    token: str


class InstitutionDepartmentResponse(ApiModel):
    departmentId: str
    institutionId: str
    name: str
    description: str
    headUserId: str | None
    status: str
    createdAt: str
    updatedAt: str


class InstitutionDepartmentListResponse(ApiModel):
    departments: list[InstitutionDepartmentResponse]


class CreateDepartmentRequest(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    headUserId: str | None = None


class InstitutionProgramResponse(ApiModel):
    programId: str
    institutionId: str
    departmentId: str | None
    name: str
    description: str
    academicYear: str | None
    term: str | None
    status: str
    createdAt: str
    updatedAt: str


class InstitutionProgramListResponse(ApiModel):
    programs: list[InstitutionProgramResponse]


class CreateProgramRequest(ApiModel):
    departmentId: str | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    academicYear: str | None = Field(default=None, max_length=20)
    term: str | None = Field(default=None, max_length=50)


class InstitutionSettingResponse(ApiModel):
    institutionId: str
    settingKey: str
    settingValue: str
    updatedAt: str


class UpdateInstitutionSettingRequest(ApiModel):
    settingKey: str = Field(min_length=1, max_length=100)
    settingValue: str


class InstitutionPlanEntitlementsResponse(ApiModel):
    institutionId: str
    plan: str
    maxUsers: int | None
    maxClassrooms: int | None
    maxCourses: int | None
    maxStorageMb: int | None
    features: dict[str, Any]
    updatedAt: str | None


class SetPlanEntitlementsRequest(ApiModel):
    plan: str = Field(min_length=1, max_length=100)
    maxUsers: int | None = Field(default=None, ge=1)
    maxClassrooms: int | None = Field(default=None, ge=1)
    maxCourses: int | None = Field(default=None, ge=1)
    maxStorageMb: int | None = Field(default=None, ge=1)
    features: dict[str, Any] | None = None


class DeleteInstitutionRequest(ApiModel):
    institutionId: str


class RestoreInstitutionRequest(ApiModel):
    institutionId: str


# ---------------------------------------------------------------------------
# M74: API Platform & Third-Party Integration Ecosystem schemas.
# ---------------------------------------------------------------------------


class PaginatedResponse(ApiModel):
    items: list[dict[str, Any]]
    total: int
    page: int
    pageSize: int
    totalPages: int


class IntegrationCreateRequest(ApiModel):
    provider: str = Field(min_length=1, max_length=100)
    providerType: str = Field(
        description="lms, sis, content_repository, identity_provider, calendar_service, educational_tool"
    )
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    baseUrl: str | None = Field(default=None, max_length=500)
    config: dict[str, Any] | None = None


class IntegrationUpdateRequest(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    baseUrl: str | None = Field(default=None, max_length=500)
    config: dict[str, Any] | None = None
    status: str | None = None


class IntegrationResponse(ApiModel):
    integrationId: str
    institutionId: str
    provider: str
    providerType: str
    name: str
    description: str
    baseUrl: str | None
    config: dict[str, Any]
    status: str
    createdBy: str
    createdAt: str
    updatedAt: str


class IntegrationCredentialCreateRequest(ApiModel):
    secretName: str = Field(min_length=1, max_length=100)
    secretValue: str = Field(min_length=1)


class IntegrationCredentialResponse(ApiModel):
    credentialId: str
    integrationId: str
    secretName: str
    createdAt: str
    updatedAt: str


class WebhookEndpointCreateRequest(ApiModel):
    url: str = Field(min_length=1, max_length=500)
    secret: str = Field(min_length=1, max_length=200)
    eventTypes: list[str] = Field(default_factory=list)


class WebhookEndpointUpdateRequest(ApiModel):
    url: str | None = Field(default=None, min_length=1, max_length=500)
    secret: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None
    eventTypes: list[str] | None = None


class WebhookEndpointResponse(ApiModel):
    endpointId: str
    institutionId: str
    url: str
    secret: str
    active: bool
    eventTypes: list[str]
    failureCount: int
    lastFailureAt: str | None
    createdAt: str
    updatedAt: str


class WebhookSecretRotateRequest(ApiModel):
    newSecret: str = Field(min_length=1, max_length=200)


class WebhookEventResponse(ApiModel):
    eventId: str
    institutionId: str
    eventType: str
    payload: dict[str, Any]
    createdAt: str


class WebhookDeliveryResponse(ApiModel):
    deliveryId: str
    endpointId: str
    eventType: str
    eventId: str
    httpStatus: int | None
    attempt: int
    deliveredAt: str | None
    createdAt: str


class IdempotencyKeyResponse(ApiModel):
    responseStatus: int
    responseBody: dict[str, Any] | None


class VersionInfoResponse(ApiModel):
    apiVersion: str
    supportedVersions: list[str]
    documentationUrl: str


# ---------------------------------------------------------------------------
# M75: Platform Observability & SRE schemas.
# ---------------------------------------------------------------------------


class DependencyHealthResponse(ApiModel):
    name: str
    healthy: bool
    lastCheck: float | None
    lastError: str | None


class DependencyHealthListResponse(ApiModel):
    dependencies: list[DependencyHealthResponse]


class CircuitBreakerStateResponse(ApiModel):
    name: str
    state: str
    failureCount: int
    openedAt: float | None


class CircuitBreakerListResponse(ApiModel):
    circuitBreakers: list[CircuitBreakerStateResponse]


class SLISnapshotResponse(ApiModel):
    category: str
    availabilityPercent: float
    errorRatePercent: float
    totalRequests: int
    latencyMs: dict[str, Any]


class SnapshotAllResponse(ApiModel):
    status: str
    uptimeSeconds: float
    requestsTotal: int
    requestsByRoute: dict[str, int]
    statusCodes: dict[str, int]
    errorCounts: dict[str, int]
    latencyMs: dict[str, Any]
    recentFailures: list[dict[str, Any]]
    sli: dict[str, SLISnapshotResponse]
    dependencies: dict[str, DependencyHealthResponse]


class ObservabilityDashboardResponse(ApiModel):
    service: str
    version: str
    environment: str
    status: str
    uptimeSeconds: float
    requestsTotal: int
    errorRatePercent: float
    p95LatencyMs: float
    recentFailures: list[dict[str, Any]]
    dependencies: dict[str, DependencyHealthResponse]
    sli: dict[str, SLISnapshotResponse]


class IncidentRecord(ApiModel):
    incidentId: str
    severity: str
    title: str
    description: str
    detectedAt: str
    resolvedAt: str | None
    status: str


class IncidentListResponse(ApiModel):
    incidents: list[IncidentRecord]


# ---------------------------------------------------------------------------
# M78: Universal Search & Intelligent Knowledge Discovery schemas.
# ---------------------------------------------------------------------------


class SearchIndexEntryResponse(ApiModel):
    entryId: str
    institutionId: str | None
    userId: str | None
    resourceType: str
    resourceId: str
    contentHash: str
    indexedAt: str
    updatedAt: str
    fts5Indexed: bool
    semanticIndexed: bool
    indexStatus: str
    errorMessage: str | None


class SearchIndexJobResponse(ApiModel):
    jobId: str
    institutionId: str | None
    jobType: str
    status: str
    totalItems: int
    indexedItems: int
    failedItems: int
    createdAt: str
    startedAt: str | None
    completedAt: str | None
    errorMessage: str | None


class SearchQueryLogResponse(ApiModel):
    logId: str
    institutionId: str | None
    userId: str | None
    queryHash: str
    queryLength: int
    resultCount: int
    resourceTypes: list[str]
    mode: str
    createdAt: str


class SearchEvaluationCaseResponse(ApiModel):
    caseId: str
    institutionId: str | None
    query: str
    expectedResultIds: list[str]
    expectedTopics: list[str]
    metadata: dict[str, Any]
    createdAt: str
    updatedAt: str


class SearchEvaluationResultResponse(ApiModel):
    resultId: str
    caseId: str
    institutionId: str | None
    recallAtK: float | None
    precisionAtK: float | None
    mrr: float | None
    citationCorrectness: float | None
    retrievalLatencyMs: float | None
    totalResults: int
    relevantResults: int
    mode: str
    createdAt: str


class SearchIndexRequest(ApiModel):
    resourceType: str = Field(min_length=1, max_length=50)
    resourceId: str = Field(min_length=1, max_length=200)
    contentHash: str = Field(min_length=1, max_length=128)
    fts5Indexed: bool = False
    semanticIndexed: bool = False

class SearchQueryCreateRequest(ApiModel):
    query: str = Field(min_length=1, max_length=2000)
    resultCount: int = Field(default=0, ge=0)
    resourceTypes: list[str] = Field(default_factory=list)
    mode: str = Field(default="lexical", min_length=1, max_length=20)


class SearchEvaluationCaseCreateRequest(ApiModel):
    query: str = Field(min_length=1, max_length=2000)
    expectedResultIds: list[str] = Field(min_length=1)
    expectedTopics: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchEvaluationResultCreateRequest(ApiModel):
    caseId: str
    recallAtK: float | None = Field(default=None, ge=0.0, le=1.0)
    precisionAtK: float | None = Field(default=None, ge=0.0, le=1.0)
    mrr: float | None = Field(default=None, ge=0.0, le=1.0)
    citationCorrectness: float | None = Field(default=None, ge=0.0, le=1.0)
    retrievalLatencyMs: float | None = Field(default=None, ge=0.0)
    totalResults: int = Field(default=0, ge=0)
    relevantResults: int = Field(default=0, ge=0)
    mode: str = Field(default="lexical", min_length=1, max_length=20)


class SearchExplainRequest(ApiModel):
    query: str = Field(min_length=1, max_length=2000)
    contextResults: list[dict[str, Any]] = Field(default_factory=list)


class SearchExplainResponse(ApiModel):
    query: str
    interpretedIntent: str
    relatedTopics: list[str]
    prerequisiteConcepts: list[str]
    suggestedFollowUps: list[str]
    groundedAnswer: str | None
    confidence: float | None


class SearchRelatedTopicsRequest(ApiModel):
    query: str = Field(min_length=1, max_length=2000)
    topicIds: list[str] = Field(default_factory=list)
    maxResults: int = Field(default=10, ge=1, le=50)


class SearchRelatedTopicsResponse(ApiModel):
    query: str
    relatedTopics: list[str]
    prerequisiteConcepts: list[str]
    suggestedFollowUps: list[str]


# ---------------------------------------------------------------------------
# M79: Personal Knowledge Graph & Cross-Subject Intelligence schemas.
# ---------------------------------------------------------------------------


class DuplicateConceptGroup(ApiModel):
    conceptId: str
    topic: str
    similarityScore: float


class DuplicateDetectionResponse(ApiModel):
    duplicateGroups: list[DuplicateConceptGroup]


class MergeConceptsRequest(ApiModel):
    primaryConceptId: str
    duplicateConceptIds: list[str] = Field(min_length=1)


class MergeConceptsResponse(ApiModel):
    conceptId: str
    name: str
    aliases: list[str]
    mergedConceptIds: list[str]


class TraversePrerequisitesResponse(ApiModel):
    concepts: list[dict[str, Any]]
    relationships: list[dict[str, Any]]


class CrossSubjectConnection(ApiModel):
    sourceConceptId: str
    targetConceptId: str
    sourceName: str
    targetName: str
    relation: str
    connectionType: str
    evidenceLevel: str


class CrossSubjectDiscoveryResponse(ApiModel):
    queryTopic: str | None
    crossSubjectConnections: list[CrossSubjectConnection]


class LearnerCompetencyData(ApiModel):
    topicId: str
    topic: str
    totalAttempts: int
    correctAttempts: int
    accuracy: float | None
    lastUpdated: str | None


class LearnerRevisionData(ApiModel):
    topicId: str
    topic: str
    lastOutcome: str
    attemptedAt: str | None
    dueAt: str | None


class LearnerProgressData(ApiModel):
    topicId: str
    topic: str
    accuracy: float | None
    lastOutcome: str | None
    totalAttempts: int | None
    correctAttempts: int | None
    lastAttempted: str | None


class PersonalizedKnowledgeGraphResponse(ApiModel):
    userId: str
    classroomGraph: dict[str, Any]
    weakAreas: list[dict[str, Any]]
    learnerSummary: dict[str, Any]


class KnowledgeGraphInsightsResponse(ApiModel):
    userId: str | None
    totalConcepts: int
    totalRelationships: int
    totalEvidence: int
    verifiedRelationships: int
    verificationRate: float
    evidenceCoverage: float


# ---------------------------------------------------------------------------
# M80: Educational Standards & Learning Interoperability schemas
# ---------------------------------------------------------------------------


class XAPIActor(ApiModel):
    objectType: str = "Agent"
    name: str | None = None
    mbox: str | None = None
    mbox_sha1sum: str | None = None
    openid: str | None = None
    account: dict[str, Any] | None = None


class XAPIVerb(ApiModel):
    id: str
    display: dict[str, str]


class XAPIObject(ApiModel):
    objectType: str = "Activity"
    id: str
    definition: dict[str, Any] | None = None


class XAPIResult(ApiModel):
    score: dict[str, Any] | None = None
    success: bool | None = None
    completion: bool | None = None
    duration: str | None = None
    response: str | None = None
    extensions: dict[str, Any] | None = None


class XAPIContext(ApiModel):
    registration: str | None = None
    instructor: XAPIActor | None = None
    team: XAPIActor | None = None
    contextActivities: dict[str, Any] | None = None
    revision: str | None = None
    platform: str | None = None
    language: str | None = None
    statement: dict[str, Any] | None = None
    extensions: dict[str, Any] | None = None


class XAPIStatementCreate(ApiModel):
    actor: XAPIActor
    verb: XAPIVerb
    object: XAPIObject
    result: XAPIResult | None = None
    context: XAPIContext | None = None
    timestamp: str | None = None
    authority: XAPIActor | None = None
    version: str = "1.0.3"
    attachmentUrls: list[str] | None = None


class XAPIStatementResponse(ApiModel):
    statementId: str
    userId: str
    tenantId: str
    actor: XAPIActor
    verb: XAPIVerb
    object: XAPIObject
    result: XAPIResult | None = None
    context: XAPIContext | None = None
    timestamp: str
    storedAt: str
    authority: XAPIActor | None = None
    version: str
    attachmentUrls: list[str]


class XAPIStatementListResponse(ApiModel):
    statements: list[XAPIStatementResponse]


class SCORMPackageCreate(ApiModel):
    displayName: str
    sourceType: str  # scorm12 | scorm2004 | imscc
    manifestSha256: str
    archiveSha256: str
    manifest: dict[str, Any]
    organizationId: str | None = None
    courseId: str | None = None
    status: str = "imported"


class SCORMPackageResponse(ApiModel):
    packageId: str
    userId: str
    tenantId: str
    displayName: str
    sourceType: str
    manifestSha256: str
    archiveSha256: str
    organizationId: str | None = None
    courseId: str | None = None
    status: str
    createdAt: str
    importedAt: str


class SCORMPackageListResponse(ApiModel):
    packages: list[SCORMPackageResponse]


class SCORMSessionCreate(ApiModel):
    packageId: str
    learnerId: str
    lessonStatus: str | None = None
    scoreRaw: float | None = None
    scoreMin: float | None = None
    scoreMax: float | None = None
    totalTimeMs: int | None = None
    lessonLocation: str | None = None
    suspendData: dict[str, Any] | None = None


class SCORMSessionResponse(ApiModel):
    sessionId: str
    packageId: str
    learnerId: str
    lessonStatus: str | None = None
    scoreRaw: float | None = None
    scoreMin: float | None = None
    scoreMax: float | None = None
    totalTimeMs: int | None = None
    lessonLocation: str | None = None
    suspendData: dict[str, Any] | None = None
    startedAt: str
    lastUpdatedAt: str


class SCORMSessionListResponse(ApiModel):
    sessions: list[SCORMSessionResponse]


class LTILaunchCreate(ApiModel):
    userId: str | None = None
    toolConsumerInstanceGuid: str | None = None
    toolConsumerInfoProductFamilyCode: str | None = None
    ltiMessageType: str | None = None
    ltiVersion: str | None = None
    resourceLinkId: str | None = None
    userIdLms: str | None = None
    roles: list[str] | None = None
    audit: dict[str, Any] | None = None


class LTILaunchResponse(ApiModel):
    launchId: str
    userId: str | None = None
    tenantId: str
    toolConsumerInstanceGuid: str | None = None
    toolConsumerInfoProductFamilyCode: str | None = None
    ltiMessageType: str | None = None
    ltiVersion: str | None = None
    resourceLinkId: str | None = None
    userIdLms: str | None = None
    roles: list[str] = []
    launchPresentedAt: str
    audit: dict[str, Any] = {}


class InteroperabilityAuditRecord(ApiModel):
    auditId: str
    userId: str | None = None
    tenantId: str
    operation: str
    standard: str
    targetId: str | None = None
    success: bool
    errorCode: str | None = None
    createdAt: str


class InteroperabilityValidationResult(ApiModel):
    valid: bool
    standard: str
    version: str
    warnings: list[str] = []
    errors: list[str] = []
    details: dict[str, Any] = {}


class InteroperabilityImportResult(ApiModel):
    importId: str
    standard: str
    status: str  # imported | validated | failed
    packageId: str | None = None
    warnings: list[str] = []
    errors: list[str] = []
    details: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# M81: AI-Powered Learning Content Recommendation & Discovery schemas
# ---------------------------------------------------------------------------


class RecommendationItem(ApiModel):
    recommendationId: str
    type: str
    # topic_practice | gap_focus | prerequisite | competency_practice |
    # exam_retake | content_discovery | next_topic
    title: str
    description: str | None = None
    reason: str
    confidence: str  # high | medium | low | insufficient_evidence
    priority: float
    targetType: str  # topic | competency | gap | concept | exam | resource
    targetId: str
    targetName: str | None = None
    difficultyLevel: str | None = None  # foundational | building | practice | advanced
    estimatedMinutes: int | None = None
    activitySuggestion: str | None = None
    citations: list[dict[str, Any]] = []
    prerequisiteConceptIds: list[str] = []
    knowledgeGapIds: list[str] = []
    measuredFields: list[str] = []
    estimatedFields: list[str] = []
    aiUnavailable: bool = False


class RecommendationRequest(ApiModel):
    limit: int = 10
    includeDismissed: bool = False
    topicId: str | None = None
    difficultyLevel: str | None = None
    includePrerequisites: bool = True
    includeKnowledgeGaps: bool = True


class RecommendationResponse(ApiModel):
    recommendations: list[RecommendationItem]
    generatedAt: str
    total: int
    coldStart: bool
    aiUnavailable: bool
    userId: str


class RecommendationFeedbackCreate(ApiModel):
    recommendationId: str
    recommendationType: str
    targetType: str
    targetId: str
    action: str  # dismiss | select | rate | postpone
    rating: int | None = None
    note: str | None = None


class RecommendationFeedbackResponse(ApiModel):
    feedbackId: str
    userId: str
    tenantId: str
    recommendationId: str
    recommendationType: str
    targetType: str
    targetId: str
    action: str
    rating: int | None = None
    note: str | None = None
    createdAt: str


class RecommendationFeedbackListResponse(ApiModel):
    feedback: list[RecommendationFeedbackResponse]
    total: int

