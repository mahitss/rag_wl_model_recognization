# ruff: noqa: E501
"""M60: Unified Learning Journey & Experience Orchestration tests.

Tests cover:
* Journey dashboard aggregation from persisted subsystems
* Journey stage completion computation
* Dashboard widgets (current goals, continue learning, today's tasks,
  due revisions, weak topics, recent activity, upcoming assessments,
  learning path progress, recommended next action)
* Journey overview (lightweight)
* Journey stage progression
* Next action recommendation
* User isolation (no cross-user data leakage)
* Empty state handling (new user with no activity)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.journey import (
    _build_current_goals,
    _build_recommended_next_action,
    _build_todays_tasks,
    _compute_stage_completion,
    _stage_completed,
    build_journey_dashboard,
    build_journey_overview,
    build_next_action,
)
from app.storage import (
    complete_study_session,
    connect_database,
    create_learning_objective,
    create_learning_path,
    create_quiz_set,
    create_user,
    initialize_storage,
    link_objective_activity,
    link_objective_evidence,
    list_content_resources,
    list_documents,
    list_knowledge_concepts,
    list_learning_images,
    list_learning_paths,
    record_quiz_attempt,
    record_quiz_review,
    start_study_session,
    store_knowledge_extraction,
)


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values = {
        "_env_file": None,
        "database_path": tmp_path / "wonderlearn.sqlite3",
        "cors_origins": "http://localhost:5173",
        "embedding_dimensions": 4,
        "embedding_batch_size": 2,
        "lexical_candidate_limit": 10,
        "semantic_candidate_limit": 10,
        "retrieval_mode": "lexical",
        "max_upload_bytes": 10 * 1024 * 1024,
        "max_image_upload_bytes": 10 * 1024 * 1024,
        "max_image_dimension": 4096,
        "max_image_pixels": 16_000_000,
        "max_pdf_pages": 250,
        "max_extracted_chars": 5_000_000,
        "chunk_size_chars": 2_000,
        "chunk_overlap_chars": 200,
        "tutor_base_url": "http://127.0.0.1:8080/v1",
        "tutor_model_id": "local-model",
        "tutor_timeout_seconds": 90.0,
        "tutor_max_tokens": 700,
    }
    values.update(overrides)
    return Settings(**values)


def setup_test_db(tmp_path: Path) -> tuple[Settings, str, Path]:
    """Create a database with a test user and return (settings, user_id, database_path)."""
    settings = make_settings(tmp_path)
    initialize_storage(settings.database_path)
    user_id = create_user(settings.database_path, "journeytest", "hashed-password", is_admin=False)
    return settings, user_id, settings.database_path


def _seed_user_activity(
    settings: Settings,
    database_path: Path,
    user_id: str,
) -> None:
    """Seed a realistic set of learner activity for dashboard testing."""
    now = datetime.now(UTC)

    # Create a document.
    import uuid as _uuid
    doc_id = _uuid.uuid4().hex
    with connect_database(database_path) as conn:
        with conn:
            conn.execute(
                """INSERT INTO documents(
                       document_id, user_id, display_name, sha256, byte_size,
                       page_count, chunk_count, created_at, parser_version
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc_id, user_id, "biology.pdf", "abc123", 1024,
                    5, 10, now.isoformat(), "v1",
                ),
            )
            # Insert a chunk owned by this user so quiz citations validate.
            chunk_id = "chk_1"
            conn.execute(
                """INSERT INTO chunks(
                       chunk_id, document_id, page_number, chunk_index,
                       char_start, char_end, text, user_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    chunk_id, doc_id, 1, 0,
                    0, 50, "Chlorophyll absorbs light energy during photosynthesis.",
                    user_id,
                ),
            )

    # Create a learning path.
    create_learning_path(
        database_path,
        user_id=user_id,
        title="Biology 101",
        description="Intro biology",
    )

    # Create a quiz set with questions.
    quiz_id = "quiz_1"
    topic_id = "topic_biology"
    created_at = now.isoformat()
    create_quiz_set(
        database_path,
        {
            "quiz_id": quiz_id,
            "topic_id": topic_id,
            "topic": "Biology",
            "created_at": created_at,
            "questions": [
                {
                    "question_id": "q_1",
                    "question_type": "multiple_choice",
                    "prompt": "What does chlorophyll absorb?",
                    "options": [{"id": "A", "text": "Light energy"},
                                {"id": "B", "text": "Sound energy"},
                                {"id": "C", "text": "Soil"},
                                {"id": "D", "text": "Oxygen"}],
                    "answer_key": "A",
                    "accepted_answers": [],
                    "explanation": "Chlorophyll absorbs light energy during photosynthesis.",
                    "citations": [{"citationId": "chk_1", "documentId": doc_id,
                                   "displayFilename": "biology.pdf", "pageNumber": 1,
                                   "chunkIndex": 0, "charStart": 0, "charEnd": 50,
                                   "text": "Chlorophyll absorbs light energy.",
                                   "retrieval": {"method": "fts5_bm25", "rank": 1}}],
                },
            ],
        },
        user_id=user_id,
    )

    # Record quiz attempts.
    record_quiz_attempt(
        database_path,
        attempt_id="attempt_1",
        question_id="q_1",
        outcome="correct",
        created_at=now.isoformat(),
        user_id=user_id,
        duration_ms=5000,
    )
    record_quiz_attempt(
        database_path,
        attempt_id="attempt_2",
        question_id="q_1",
        outcome="incorrect",
        created_at=now.isoformat(),
        user_id=user_id,
        duration_ms=3000,
    )

    # Record quiz reviews (for revision queue).
    record_quiz_review(
        database_path,
        question_id="q_1",
        result="remembered",
        confidence_rating=4,
        user_id=user_id,
    )

    # Start and complete a study session.
    session_id = "session_1"
    start_study_session(
        database_path,
        session_id=session_id,
        topic_id=topic_id,
        topic="Biology",
        started_at=now.isoformat(),
        user_id=user_id,
    )
    complete_study_session(
        database_path,
        session_id=session_id,
        completed_at=now.isoformat(),
        user_id=user_id,
    )

    # Store knowledge extraction.
    store_knowledge_extraction(
        database_path,
        chunk={"chunk_id": "chk_1", "document_id": doc_id, "user_id": user_id, "page_number": 1},
        extraction={
            "concepts": [
                {"name": "photosynthesis", "aliases": ["light synthesis"], "normalized_name": "photosynthesis", "concept_id": "kg_photosynthesis", "supporting_text": "Chlorophyll absorbs light energy."},
                {"name": "chlorophyll", "aliases": [], "normalized_name": "chlorophyll", "concept_id": "kg_chlorophyll", "supporting_text": "Chlorophyll absorbs light energy."},
            ],
            "relationships": [{
                "relationship_id": "rel_1",
                "source_normalized": "photosynthesis",
                "target_normalized": "chlorophyll",
                "relation": "contains",
                "supporting_text": "Chlorophyll absorbs light energy.",
            }],
        },
        user_id=user_id,
    )

    # Create a learning objective linked to the quiz attempt.
    obj = create_learning_objective(
        database_path,
        path_id="path_1",
        title="Understand photosynthesis",
        description="Learn how chlorophyll absorbs light energy.",
        order_index=0,
        source_document_id=doc_id,
        source_chunk_id="chk_1",
        source_passage="Chlorophyll absorbs light energy.",
        prerequisite_objective_ids=[],
        rationale="Foundational concept for biology.",
        difficulty_level="foundational",
    )
    if obj:
        link_objective_activity(
            database_path,
            objective_id=obj["objective_id"],
            activity_type="quiz_attempt",
            activity_id="attempt_1",
            user_id=user_id,
        )
        link_objective_evidence(
            database_path,
            objective_id=obj["objective_id"],
            citation_ids=["chk_1"],
            user_id=user_id,
        )

    # Create a content resource.
    from app.storage import create_content_resource
    create_content_resource(
        database_path,
        user_id=user_id,
        title="Biology Notes",
        description="Study notes on photosynthesis.",
        format="notes",
        detail_level="standard",
        source_type="document",
        source_refs=[{"documentId": doc_id}],
        status="reviewed",
    )


# ---------------------------------------------------------------------------
# Journey dashboard tests
# ---------------------------------------------------------------------------


def test_journey_dashboard_aggregates_all_subsystems(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)
    _seed_user_activity(settings, database_path, user_id)
    now = datetime.now(UTC)

    result = build_journey_dashboard(
        settings=settings,
        database_path=str(database_path),
        user_id=user_id,
        now=now,
    )

    assert result["userId"] == user_id
    assert "journey" in result
    assert "dashboard" in result
    assert "analytics" in result
    assert "studyCoachPlan" in result
    assert "revisionQueue" in result
    assert "learningPaths" in result
    assert "documents" in result
    assert "images" in result
    assert "knowledgeConcepts" in result
    assert "contentResources" in result

    # Journey should show completion > 0 since we seeded activity.
    assert result["journey"]["completionPercent"] > 0
    assert result["journey"]["completedStages"] > 0
    assert result["journey"]["totalStages"] == 10

    # Dashboard widgets should be present.
    dashboard = result["dashboard"]
    assert "currentGoals" in dashboard
    assert "continueLearning" in dashboard
    assert "todaysTasks" in dashboard
    assert "dueRevisions" in dashboard
    assert "weakTopics" in dashboard
    assert "recentActivity" in dashboard
    assert "upcomingAssessments" in dashboard
    assert "learningPathProgress" in dashboard
    assert "recommendedNextAction" in dashboard


def test_journey_dashboard_empty_state_new_user(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)
    now = datetime.now(UTC)

    result = build_journey_dashboard(
        settings=settings,
        database_path=str(database_path),
        user_id=user_id,
        now=now,
    )

    # New user: only onboarding is complete (authenticated user = 10%).
    assert result["journey"]["completionPercent"] == 10
    assert result["journey"]["completedStages"] == 1
    # New user: only onboarding goal is present.
    assert len(result["dashboard"]["currentGoals"]) == 1
    goal = result["dashboard"]["currentGoals"][0]
    assert goal["goalId"] == "start_first_quiz"
    assert goal["title"] == "Take your first quiz"
    assert goal["type"] == "onboarding"
    assert goal["priority"] == 10
    assert result["dashboard"]["recommendedNextAction"] is not None
    # Current stage is material_upload (no documents yet), so next action
    # suggests generating a quiz from uploaded material.
    assert result["dashboard"]["recommendedNextAction"]["action"] == "generate_quiz"


def test_journey_stage_completion_with_activity(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)
    _seed_user_activity(settings, database_path, user_id)
    now = datetime.now(UTC)

    data = __import__("app.storage", fromlist=["get_learning_analytics_data"]).get_learning_analytics_data(
        database_path, user_id=user_id
    )
    from app.learning_analytics import build_analytics
    analytics = build_analytics(
        attempts=data["attempts"],
        sessions=data["sessions"],
        reviews=data["reviews"],
        topic_sources=data["topicSources"],
        concepts=data["concepts"],
        now=now,
    )

    stages = _compute_stage_completion(
        analytics=analytics,
        documents=list_documents(database_path, user_id=user_id),
        images=list_learning_images(database_path, user_id=user_id),
        paths=list_learning_paths(database_path, user_id=user_id),
        concepts=list_knowledge_concepts(database_path, user_id=user_id),
        content_resources=list_content_resources(database_path, user_id=user_id),
        revision_stats=__import__("app.storage", fromlist=["get_revision_stats"]).get_revision_stats(
            database_path, user_id=user_id
        ),
        weak_topics=__import__("app.storage", fromlist=["get_weak_topics"]).get_weak_topics(
            database_path, user_id=user_id
        ),
        now=now,
    )

    # Onboarding is always complete (authenticated user).
    onboarding = next(s for s in stages if s["stageId"] == "onboarding")
    assert onboarding["completed"] is True

    # Material upload should be complete (we seeded a document).
    upload = next(s for s in stages if s["stageId"] == "material_upload")
    assert upload["completed"] is True

    # Initial assessment should be complete (we seeded quiz attempts).
    assessment = next(s for s in stages if s["stageId"] == "initial_assessment")
    assert assessment["completed"] is True

    # Learning path should be complete (we seeded an active path).
    path = next(s for s in stages if s["stageId"] == "learning_path")
    assert path["completed"] is True

    # Topic learning should be complete (we seeded a completed session).
    topic = next(s for s in stages if s["stageId"] == "topic_learning")
    assert topic["completed"] is True


def test_journey_overview_lightweight(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)
    _seed_user_activity(settings, database_path, user_id)
    now = datetime.now(UTC)

    result = build_journey_overview(
        settings=settings,
        database_path=str(database_path),
        user_id=user_id,
        now=now,
    )

    assert result["userId"] == user_id
    assert result["totals"]["attemptCount"] == 2
    assert result["documentCount"] == 1
    assert result["activePathCount"] == 1


def test_next_action_overdue_reviews_priority(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)
    _seed_user_activity(settings, database_path, user_id)
    now = datetime.now(UTC)

    # The seeded review has result "remembered", so no overdue reviews.
    # Next action should fall through to weak topics or coach plan.
    action = build_next_action(
        settings=settings,
        database_path=str(database_path),
        user_id=user_id,
        now=now,
    )
    assert action is not None
    assert "action" in action
    assert "title" in action
    assert "priority" in action


def test_user_isolation_in_journey(tmp_path):
    settings, user_id_a, database_path = setup_test_db(tmp_path)
    user_id_b = create_user(database_path, "journeytest_b", "hashed-password", is_admin=False)

    # Seed activity for user A only.
    _seed_user_activity(settings, database_path, user_id_a)
    now = datetime.now(UTC)

    result_a = build_journey_dashboard(
        settings=settings,
        database_path=str(database_path),
        user_id=user_id_a,
        now=now,
    )
    result_b = build_journey_dashboard(
        settings=settings,
        database_path=str(database_path),
        user_id=user_id_b,
        now=now,
    )

    # User A has activity; user B has none.
    assert result_a["journey"]["completedStages"] > result_b["journey"]["completedStages"]
    assert result_a["analytics"]["totals"]["attemptCount"] == 2
    assert result_b["analytics"]["totals"]["attemptCount"] == 0


def test_build_current_goals_with_overdue_reviews(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)

    # Minimal analytics with overdue reviews.
    analytics = {
        "totals": {
            "attemptCount": 0,
            "attemptedQuestions": 0,
            "correctQuestions": 0,
            "incorrectQuestions": 0,
            "ambiguousQuestions": 0,
            "quizAccuracy": None,
            "completedStudySessions": 0,
            "activeStudySessions": 0,
            "overdueReviews": 3,
        },
        "topics": [],
        "recentActivity": [],
    }
    coach_plan = {"recommendations": []}
    weak_topics = []

    goals = _build_current_goals(
        analytics=analytics,
        coach_plan=coach_plan,
        weak_topics=weak_topics,
        overdue_topics=[],
    )

    overdue_goal = next((g for g in goals if g["goalId"] == "complete_overdue_reviews"), None)
    assert overdue_goal is not None
    assert overdue_goal["priority"] == 100
    assert overdue_goal["count"] == 3


def test_build_recommended_next_action_weak_topic(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)
    now = datetime.now(UTC)

    analytics = {
        "totals": {
            "attemptCount": 1,
            "overdueReviews": 0,
            "quizAccuracy": 0.4,
        },
        "topics": [],
    }
    coach_plan = {"recommendations": []}
    weak_topics = [
        {"topicId": "topic_weak", "topic": "Weak Biology", "mastery": 0.3,
         "incorrectQuestions": 2},
    ]
    revision_items = []

    stage_completion = [
        {"stageId": "onboarding", "completed": True},
        {"stageId": "material_upload", "completed": True},
        {"stageId": "initial_assessment", "completed": True},
        {"stageId": "learning_path", "completed": False},
    ]

    action = _build_recommended_next_action(
        stage_completion=stage_completion,
        current_stage_index=3,
        analytics=analytics,
        coach_plan=coach_plan,
        revision_items=revision_items,
        weak_topics=weak_topics,
        now=now,
    )

    assert action is not None
    assert action["action"] == "study_topic"
    assert action["topicId"] == "topic_weak"
    assert action["topic"] == "Weak Biology"
    assert action["priority"] == 80


def test_build_todays_tasks(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)
    now = datetime.now(UTC)

    coach_plan = {
        "recommendations": [
            {
                "recommendationId": "rec_1",
                "topicId": "topic_1",
                "topic": "Biology",
                "title": "Review Biology",
                "priority": 90,
                "estimatedMinutes": 20,
                "status": "planned",
                "reasons": ["Overdue review", "Weak topic"],
            },
        ],
    }
    revision_items = [
        {
            "itemId": "rev_1",
            "title": "Quiz: Photosynthesis",
            "topicId": "topic_1",
            "topic": "Biology",
            "priority": 95,
            "estimatedMinutes": 10,
            "status": "due",
            "dueAt": now.isoformat(),
            "lastResult": "forgotten",
        },
    ]

    tasks = _build_todays_tasks(
        coach_plan=coach_plan,
        revision_items=revision_items,
        now=now,
    )

    assert len(tasks) == 2
    # Tasks sorted by priority descending.
    assert tasks[0]["priority"] >= tasks[1]["priority"]
    assert tasks[0]["type"] in {"study_coach", "revision"}


def test_stage_completed_material_upload(tmp_path):
    settings, user_id, database_path = setup_test_db(tmp_path)
    now = datetime.now(UTC)

    # No documents -> not complete.
    completed, evidence = _stage_completed(
        stage_id="material_upload",
        totals={"attemptCount": 0, "overdueReviews": 0},
        documents=[],
        images=[],
        paths=[],
        concepts=[],
        content_resources=[],
        revision_stats={"totalItems": 0},
        weak_topics=[],
        today=now.date().isoformat(),
    )
    assert completed is False

    # With a document -> complete.
    completed, evidence = _stage_completed(
        stage_id="material_upload",
        totals={"attemptCount": 0, "overdueReviews": 0},
        documents=[{"documentId": "doc_1", "displayFilename": "bio.pdf"}],
        images=[],
        paths=[],
        concepts=[],
        content_resources=[],
        revision_stats={"totalItems": 0},
        weak_topics=[],
        today=now.date().isoformat(),
    )
    assert completed is True
    assert evidence["documentCount"] == 1


def test_journey_dashboard_api_endpoint_structure(tmp_path):
    """Verify the dashboard response matches the expected schema structure."""
    settings, user_id, database_path = setup_test_db(tmp_path)
    _seed_user_activity(settings, database_path, user_id)
    now = datetime.now(UTC)

    result = build_journey_dashboard(
        settings=settings,
        database_path=str(database_path),
        user_id=user_id,
        now=now,
    )

    # Verify all required top-level keys.
    for key in ["generatedAt", "userId", "journey", "dashboard", "analytics",
                "studyCoachPlan", "revisionQueue", "learningPaths", "documents",
                "images", "knowledgeConcepts", "contentResources"]:
        assert key in result, f"Missing key: {key}"

    # Verify journey structure.
    journey = result["journey"]
    for key in ["completionPercent", "completedStages", "totalStages", "currentStage", "stages"]:
        assert key in journey, f"Missing journey key: {key}"
    assert journey["totalStages"] == 10

    # Verify dashboard structure.
    dashboard = result["dashboard"]
    for key in ["currentGoals", "continueLearning", "todaysTasks", "dueRevisions",
                "weakTopics", "recentActivity", "upcomingAssessments",
                "learningPathProgress", "recommendedNextAction"]:
        assert key in dashboard, f"Missing dashboard key: {key}"

    # Verify stage structure.
    for stage in journey["stages"]:
        for key in ["stageId", "label", "description", "completed", "evidence", "stageOrder"]:
            assert key in stage, f"Missing stage key: {key}"
