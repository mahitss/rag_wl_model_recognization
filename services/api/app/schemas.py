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
    citations: list[str] | None = Field(default=None, max_length=50)


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
