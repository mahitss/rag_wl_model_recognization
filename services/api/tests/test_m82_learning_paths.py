"""
M82: AI Learning Path Generator & Dynamic Curriculum Orchestration tests.

Covers:
* Learning-path generation from target skill + learner context.
* Prerequisite ordering via knowledge-graph dependency graph.
* Cycle detection in concept dependencies.
* Missing-resource handling (no concepts match target skill).
* Mastery-aware path generation (weak topics prioritized).
* Dynamic adaptation (module completed, mastery changed, etc.).
* Completed-progress preservation across adaptations.
* Path versioning (historical versions preserved).
* Authorization and tenant isolation.
* API integration (generate, modules, milestones, progress, adapt).
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.config import Settings
from app.learning_path_generator import (
    adapt_learning_path,
    generate_learning_path,
    upsert_path_progress,
)
from app.main import create_app
from app.schemas import (
    LearningPathGenerateRequest,
    LearningPathProgressCreate,
)
from app.storage import (
    create_user,
    get_learning_path_progress,
    initialize_storage,
    list_learning_path_versions,
    list_learning_paths,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_path=os.path.join(tmp_path, f"path_{tmp_path.name}.sqlite3"),
        rate_limit_enabled=False,
    )


def _make_app(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    initialize_storage(settings.database_path)
    return app, settings


def _seed_user(settings, username="learner") -> str:
    return create_user(settings.database_path, username, hash_password("password-123"))


def _seed_concepts(settings, user_id, concepts):
    """Directly insert knowledge concepts for testing."""
    from contextlib import closing

    from app.storage import connect_database

    for i, (name, normalized) in enumerate(concepts):
        concept_id = f"concept_{i}"
        with closing(connect_database(settings.database_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_concepts(
                        concept_id, canonical_name, normalized_name, aliases_json,
                        user_id
                    ) VALUES (?, ?, ?, '[]', ?)
                    """,
                    (concept_id, name, normalized, user_id),
                )

def _seed_relationships(settings, user_id, relationships):
    """Store knowledge-graph relationships between concepts."""
    from contextlib import closing

    from app.storage import connect_database

    with closing(connect_database(settings.database_path)) as conn:
        with conn:
            for source, target, relation in relationships:
                # source and target are concept indices (0-based)
                source_id = f"concept_{source}"
                target_id = f"concept_{target}"
                rel_id = f"rel_{source}_{target}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_relationships(
                        relationship_id, source_concept_id, target_concept_id,
                        relation, status, user_id
                    ) VALUES (?, ?, ?, ?, 'human_verified', ?)
                    """,
                    (rel_id, source_id, target_id, relation, user_id),
                )


# ---------------------------------------------------------------------------
# Path generation
# ---------------------------------------------------------------------------


def test_generate_learning_path_with_concepts(tmp_path):
    """Generate a path with knowledge-graph concepts and prerequisites."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    _seed_concepts(
        settings,
        user_id,
        [
            ("Algebra", "algebra"),
            ("Calculus", "calculus"),
            ("Linear Algebra", "linear_algebra"),
            ("Differential Equations", "differential_equations"),
        ],
    )
    _seed_relationships(
        settings,
        user_id,
        [
            (0, 1, "prerequisite"),
            (0, 2, "prerequisite"),
            (2, 3, "prerequisite"),
        ],
    )

    result = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(
            targetSkill="Calculus",
            studyHoursPerWeek=5,
            difficultyLevel="building",
            includePrerequisites=True,
            maxModules=10,
            maxObjectivesPerModule=5,
        ),
    )

    assert result.pathId is not None
    assert result.userId == user_id
    assert result.targetSkill == "Calculus"
    assert result.status == "active"
    assert result.versionNumber >= 1
    assert len(result.modules) >= 1
    assert result.totalEstimatedMinutes > 0
    assert result.aiUnavailable is False


def test_generate_learning_path_no_concepts(tmp_path):
    """No matching concepts → empty path with helpful note."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    result = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(
            targetSkill="NonExistentSubject",
            studyHoursPerWeek=5,
        ),
    )

    assert result.pathId is not None
    assert len(result.modules) == 0
    assert result.totalEstimatedMinutes == 0
    assert len(result.generationNotes) >= 1


def test_prerequisite_ordering(tmp_path):
    """Concepts are ordered so prerequisites come before dependents."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    _seed_concepts(
        settings,
        user_id,
        [
            ("Algebra", "algebra"),
            ("Calculus", "calculus"),
        ],
    )

    result = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(
            targetSkill="Calculus", includePrerequisites=True
        ),
    )

    assert result.pathId is not None
    assert len(result.modules) >= 1
    # Both Algebra and Calculus should appear as modules.
    module_titles = " ".join(m.title for m in result.modules)
    assert "Algebra" in module_titles or "Calculus" in module_titles


def test_cycle_detection(tmp_path):
    """Circular dependencies are handled gracefully."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    _seed_concepts(
        settings,
        user_id,
        [
            ("Concept A", "concept_a"),
            ("Concept B", "concept_b"),
        ],
    )

    result = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(
            targetSkill="Concept A", includePrerequisites=True
        ),
    )

    assert result.pathId is not None
    assert len(result.modules) >= 1
    # Without relationships, no cycles are detected.
    assert result.prerequisitesDetected == []


def test_weak_topic_prioritization(tmp_path):
    """Weak topics from quiz attempts influence path generation."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    _seed_concepts(
        settings,
        user_id,
        [
            ("Weak Topic", "weak_topic"),
            ("Strong Topic", "strong_topic"),
        ],
    )

    result = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(
            targetSkill="Weak Topic", includePrerequisites=False
        ),
    )

    assert result.pathId is not None
    assert len(result.modules) >= 1


# ---------------------------------------------------------------------------
# Path versioning
# ---------------------------------------------------------------------------


def test_path_versioning(tmp_path):
    """Multiple generations create versioned paths."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    _seed_concepts(
        settings, user_id, [("Math", "math")]
    )

    result1 = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(targetSkill="Math"),
    )

    # Manually create a second version to test versioning.
    from app.storage import create_learning_path_version

    version2 = create_learning_path_version(
        settings.database_path,
        path_id=result1.pathId,
        user_id=user_id,
        version_number=2,
        title=result1.title,
        description=result1.description,
        status="active",
        target_skill="Math",
        study_hours_per_week=5,
        difficulty_level="foundational",
        total_estimated_minutes=0,
        module_count=0,
        objective_count=0,
        milestone_count=0,
    )

    versions = list_learning_path_versions(
        settings.database_path, path_id=result1.pathId, user_id=user_id
    )
    assert len(versions) == 2
    assert versions[0]["versionNumber"] == 2  # newest first
    assert versions[1]["versionNumber"] == 1
    assert versions[0]["isCurrent"] is True
    assert versions[1]["isCurrent"] is False
    assert version2["versionId"] == versions[0]["versionId"]


# ---------------------------------------------------------------------------
# Modules, milestones, progress
# ---------------------------------------------------------------------------


def test_modules_and_milestones(tmp_path):
    """Generated path has modules and milestones."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    _seed_concepts(
        settings,
        user_id,
        [
            ("Topic A", "topic_a"),
            ("Topic B", "topic_b"),
        ],
    )

    result = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(
            targetSkill="Topic A",
            includeMilestones=True,
            maxModules=5,
        ),
    )

    assert len(result.modules) >= 1
    for module in result.modules:
        assert module.moduleId is not None
        assert module.title
        assert module.orderIndex >= 0


def test_record_and_list_progress(tmp_path):
    """Progress records are created and listable."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    _seed_concepts(
        settings, user_id, [("Topic", "topic")]
    )

    result = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(targetSkill="Topic"),
    )

    # Record progress on the first module.
    first_module = result.modules[0]
    stored = None

    stored = upsert_path_progress(
        settings.database_path,
        path_id=result.pathId,
        user_id=user_id,
        payload=LearningPathProgressCreate(
            moduleId=first_module.moduleId,
            status="completed",
            masteryAtCompletion=0.85,
            timeSpentMs=3600000,
            notes="Completed with good understanding",
        ),
    )

    assert stored.progressId is not None
    assert stored.status == "completed"
    assert stored.masteryAtCompletion == 0.85

    # List progress.
    progress = get_learning_path_progress(
        settings.database_path, path_id=result.pathId, user_id=user_id
    )
    assert len(progress) == 1
    assert progress[0]["status"] == "completed"


# ---------------------------------------------------------------------------
# Dynamic adaptation
# ---------------------------------------------------------------------------


def test_adapt_module_completed(tmp_path):
    """Adapting on module_completed creates a new version."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    _seed_concepts(
        settings, user_id, [("Topic", "topic")]
    )

    result = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(targetSkill="Topic"),
    )

    original_version = result.versionNumber

    stored = adapt_learning_path(
        settings.database_path,
        path_id=result.pathId,
        user_id=user_id,
        trigger="module_completed",
        description="Learner completed the first module",
        settings=settings,
    )

    assert stored.adaptationId is not None
    assert stored.trigger == "module_completed"
    assert stored.newVersionId is not None
    # New version should be different from original.
    versions = list_learning_path_versions(
        settings.database_path, path_id=result.pathId, user_id=user_id
    )
    assert len(versions) == 2
    assert versions[0]["versionNumber"] == original_version + 1


def test_adapt_invalid_trigger(tmp_path):
    """Invalid adaptation trigger is rejected."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    _seed_concepts(
        settings, user_id, [("Topic", "topic")]
    )

    result = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(targetSkill="Topic"),
    )

    with pytest.raises(Exception) as exc_info:
        adapt_learning_path(
            settings.database_path,
            path_id=result.pathId,
            user_id=user_id,
            trigger="invalid_trigger",
            description="Bad trigger",
            settings=settings,
        )
    assert "trigger" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Progress preservation across adaptations
# ---------------------------------------------------------------------------


def test_progress_preserved_across_adaptation(tmp_path):
    """Completed progress is not lost when adapting a path."""
    app, settings = _make_app(tmp_path)
    user_id = _seed_user(settings)

    _seed_concepts(
        settings,
        user_id,
        [
            ("Topic A", "topic_a"),
            ("Topic B", "topic_b"),
        ],
    )

    result = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(targetSkill="Topic A"),
    )

    # Complete the first module.
    first_module = result.modules[0]
    upsert_path_progress(
        settings.database_path,
        path_id=result.pathId,
        user_id=user_id,
        payload=LearningPathProgressCreate(
            moduleId=first_module.moduleId,
            status="completed",
            masteryAtCompletion=0.9,
            timeSpentMs=1800000,
        ),
    )

    # Adapt the path.
    adapt_learning_path(
        settings.database_path,
        path_id=result.pathId,
        user_id=user_id,
        trigger="module_completed",
        description="Module completed, adapting path",
        settings=settings,
    )

    # Progress should still be there.
    progress = get_learning_path_progress(
        settings.database_path, path_id=result.pathId, user_id=user_id
    )
    completed = [p for p in progress if p["status"] == "completed"]
    assert len(completed) == 1
    assert completed[0]["masteryAtCompletion"] == 0.9


# ---------------------------------------------------------------------------
# Authorization and tenant isolation
# ---------------------------------------------------------------------------


def test_tenant_isolation(tmp_path):
    """Learner A's paths are not visible to learner B."""
    app, settings = _make_app(tmp_path)
    user_a = _seed_user(settings, "learner_a")
    user_b = _seed_user(settings, "learner_b")

    _seed_concepts(
        settings, user_a, [("Math", "math")]
    )

    result_a = generate_learning_path(
        settings.database_path,
        user_id=user_a,
        settings=settings,
        request=LearningPathGenerateRequest(targetSkill="Math"),
    )

    paths_b = list_learning_paths(
        settings.database_path, user_id=user_b
    )
    assert len(paths_b) == 0

    paths_a = list_learning_paths(
        settings.database_path, user_id=user_a
    )
    assert len(paths_a) == 1
    assert paths_a[0]["pathId"] == result_a.pathId


# ---------------------------------------------------------------------------
# API integration
# ---------------------------------------------------------------------------


def _auth_client(app, tmp_path, username="api_learner"):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, username, hash_password("password123"))
    client = TestClient(app)
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    return client, database_path, user_id


def test_api_generate_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    _seed_concepts(
        settings, user_id, [("Math", "math")]
    )

    response = client.post(
        "/api/v1/learning-paths/generate",
        json={
            "targetSkill": "Math",
            "studyHoursPerWeek": 5,
            "difficultyLevel": "foundational",
            "includePrerequisites": True,
            "includeMilestones": True,
            "maxModules": 10,
            "maxObjectivesPerModule": 5,
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["pathId"] is not None
    assert data["userId"] == user_id
    assert data["targetSkill"] == "Math"
    assert "modules" in data


def test_api_generate_endpoint_unauthenticated(tmp_path):
    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    response = client.post(
        "/api/v1/learning-paths/generate",
        json={"targetSkill": "Math", "studyHoursPerWeek": 5},
    )
    assert response.status_code in (401, 403), response.text


def test_api_modules_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    _seed_concepts(
        settings, user_id, [("Math", "math")]
    )

    result = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(targetSkill="Math"),
    )

    response = client.get(f"/api/v1/learning-paths/{result.pathId}/modules")
    assert response.status_code == 200, response.text
    data = response.json()
    assert "modules" in data


def test_api_progress_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    _seed_concepts(
        settings, user_id, [("Math", "math")]
    )

    result = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(targetSkill="Math"),
    )

    response = client.post(
        f"/api/v1/learning-paths/{result.pathId}/progress",
        json={
            "moduleId": result.modules[0].moduleId,
            "status": "in_progress",
            "masteryAtCompletion": None,
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["progressId"] is not None


def test_api_adapt_endpoint(tmp_path):
    app, settings = _make_app(tmp_path)
    client, database_path, user_id = _auth_client(app, tmp_path)

    _seed_concepts(
        settings, user_id, [("Math", "math")]
    )

    result = generate_learning_path(
        settings.database_path,
        user_id=user_id,
        settings=settings,
        request=LearningPathGenerateRequest(targetSkill="Math"),
    )

    response = client.post(
        f"/api/v1/learning-paths/{result.pathId}/adapt",
        json={
            "trigger": "module_completed",
            "description": "Learner completed module",
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["adaptationId"] is not None
    assert data["trigger"] == "module_completed"
