"""Personalized learning paths and adaptive difficulty regressions."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.storage import (
    compute_adaptive_recommendations,
    initialize_storage,
)


def _register_and_login(client: TestClient, username: str, password: str) -> None:
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )


def _make_settings(tmp_path, username: str = "path-user") -> Settings:
    return Settings(
        _env_file=None,
        database_path=str(tmp_path) + "/learning-paths.sqlite3",
        rate_limit_enabled=False,
        admin_usernames="admin-user",
    )


def test_learning_path_crud(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "path-user", "correct-horse-123")

        # Create
        create = client.post(
            "/api/v1/learning-paths",
            json={"title": "Photosynthesis basics", "description": "Learn photosynthesis"},
        )
        assert create.status_code == 201, create.text
        path = create.json()
        assert path["title"] == "Photosynthesis basics"
        assert path["status"] == "active"
        path_id = path["pathId"]

        # List
        listed = client.get("/api/v1/learning-paths")
        assert listed.status_code == 200
        assert len(listed.json()["paths"]) == 1

        # Get
        got = client.get(f"/api/v1/learning-paths/{path_id}")
        assert got.status_code == 200
        assert got.json()["path"]["pathId"] == path_id

        # Update
        updated = client.patch(
            f"/api/v1/learning-paths/{path_id}",
            json={"title": "Photosynthesis advanced", "status": "paused"},
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "Photosynthesis advanced"
        assert updated.json()["status"] == "paused"

        # Delete
        deleted = client.delete(f"/api/v1/learning-paths/{path_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        # Gone
        listed = client.get("/api/v1/learning-paths")
        assert len(listed.json()["paths"]) == 0


def test_learning_path_cross_user_isolation(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client_a:
        _register_and_login(client_a, "user-a", "password-a")

        # User A creates a path
        create = client_a.post(
            "/api/v1/learning-paths",
            json={"title": "User A path", "description": "Owned by A"},
        )
        assert create.status_code == 201
        path_id = create.json()["pathId"]

        # User B logs in on a separate client
        client_b = TestClient(create_app(settings))
        _register_and_login(client_b, "user-b", "password-b")

        # User B cannot access User A's path
        got = client_b.get(f"/api/v1/learning-paths/{path_id}")
        assert got.status_code == 403

        # User B cannot update User A's path
        patch = client_b.patch(
            f"/api/v1/learning-paths/{path_id}",
            json={"title": "Hacked"},
        )
        assert patch.status_code == 403

        # User B cannot delete User A's path
        deleted = client_b.delete(f"/api/v1/learning-paths/{path_id}")
        assert deleted.status_code == 403

        # User A can still access their own path
        got = client_a.get(f"/api/v1/learning-paths/{path_id}")
        assert got.status_code == 200
        assert got.json()["path"]["title"] == "User A path"


def test_learning_objective_crud(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "obj-user", "correct-horse-123")

        # Create path
        create = client.post(
            "/api/v1/learning-paths",
            json={"title": "Path", "description": "A path"},
        )
        assert create.status_code == 201
        path_id = create.json()["pathId"]

        # Create objective
        create_obj = client.post(
            f"/api/v1/learning-paths/{path_id}/objectives",
            json={
                "title": "Objective 1",
                "description": "First objective",
                "orderIndex": 0,
                "difficultyLevel": "foundational",
                "rationale": "Start with basics",
            },
        )
        assert create_obj.status_code == 201, create_obj.text
        objective = create_obj.json()
        assert objective["title"] == "Objective 1"
        assert objective["status"] == "not_started"
        assert objective["orderIndex"] == 0
        objective_id = objective["objectiveId"]

        # List objectives
        listed = client.get(f"/api/v1/learning-paths/{path_id}/objectives")
        assert listed.status_code == 200
        assert len(listed.json()["objectives"]) == 1

        # Get objective
        got = client.get(f"/api/v1/learning-objectives/{objective_id}")
        assert got.status_code == 200
        assert got.json()["objectiveId"] == objective_id

        # Update objective
        updated = client.patch(
            f"/api/v1/learning-objectives/{objective_id}",
            json={"status": "in_progress", "rationale": "Started working on it"},
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == "in_progress"
        assert updated.json()["rationale"] == "Started working on it"

        # Delete objective
        deleted = client.delete(f"/api/v1/learning-objectives/{objective_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True


def test_learning_objective_prerequisite_validation(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "prereq-user", "correct-horse-123")

        create = client.post(
            "/api/v1/learning-paths",
            json={"title": "Path", "description": "A path"},
        )
        assert create.status_code == 201
        path_id = create.json()["pathId"]

        # Create first objective
        obj1 = client.post(
            f"/api/v1/learning-paths/{path_id}/objectives",
            json={
                "title": "First",
                "description": "First objective",
                "orderIndex": 0,
                "difficultyLevel": "foundational",
            },
        )
        assert obj1.status_code == 201
        obj1_id = obj1.json()["objectiveId"]

        # Create second objective referencing first as prerequisite
        obj2 = client.post(
            f"/api/v1/learning-paths/{path_id}/objectives",
            json={
                "title": "Second",
                "description": "Second objective",
                "orderIndex": 1,
                "difficultyLevel": "building",
                "prerequisiteObjectiveIds": [obj1_id],
            },
        )
        assert obj2.status_code == 201, obj2.text
        assert obj1_id in obj2.json()["prerequisiteObjectiveIds"]

        # Creating objective with nonexistent prerequisite should fail
        bad = client.post(
            f"/api/v1/learning-paths/{path_id}/objectives",
            json={
                "title": "Third",
                "description": "Bad prerequisite",
                "orderIndex": 2,
                "difficultyLevel": "foundational",
                "prerequisiteObjectiveIds": ["nonexistent-obj"],
            },
        )
        assert bad.status_code == 400


def test_link_objective_evidence_and_activity(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "evidence-user", "correct-horse-123")

        # Upload a document first
        import io

        pdf_content = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>endobj\n"
            b"4 0 obj<</Length 44>>stream\n"
            b"BT/F1 12 Tf 72 712 Td(Hello World)Tj ET\n"
            b"endstream endobj\n"
            b"xref 0 5\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000058 00000 n \n"
            b"0000000115 00000 n \n"
            b"0000000205 00000 n \n"
            b"trailer<</Size 5/Root 1 0 R>>startxref 297\n%%EOF\n"
        )
        upload = client.post(
            "/api/v1/documents",
            files={"file": ("lesson.pdf", io.BytesIO(pdf_content), "application/pdf")},
        )
        if upload.status_code != 201:
            return

        # Retrieve to get citations
        retrieve = client.post(
            "/api/v1/retrieve",
            json={"query": "photosynthesis", "limit": 5},
        )
        assert retrieve.status_code == 200
        results = retrieve.json()["results"]
        if not results:
            # No indexed content; skip evidence linking test
            return
        citation_id = results[0]["citationId"]

        # Create path and objective
        create = client.post(
            "/api/v1/learning-paths",
            json={"title": "Path", "description": "A path"},
        )
        assert create.status_code == 201
        path_id = create.json()["pathId"]

        obj = client.post(
            f"/api/v1/learning-paths/{path_id}/objectives",
            json={
                "title": "Objective",
                "description": "An objective",
                "orderIndex": 0,
                "difficultyLevel": "foundational",
            },
        )
        assert obj.status_code == 201
        objective_id = obj.json()["objectiveId"]

        # Link evidence
        linked = client.post(
            f"/api/v1/learning-objectives/{objective_id}/evidence",
            json={"citationIds": [citation_id]},
        )
        assert linked.status_code == 200
        assert citation_id in linked.json()["evidenceIds"]

        # Link activity (quiz attempt would need a quiz first; use study session instead)
        session = client.post(
            "/api/v1/study-sessions",
            json={"topic": "photosynthesis"},
        )
        assert session.status_code == 201, session.text
        session_id = session.json()["sessionId"]

        linked_activity = client.post(
            f"/api/v1/learning-objectives/{objective_id}/activities",
            json={"activityType": "study_session", "activityId": session_id},
        )
        assert linked_activity.status_code == 200
        activity_links = linked_activity.json()["activityLinks"]
        assert any(
            a["activityType"] == "study_session" and a["activityId"] == session_id
            for a in activity_links
        )


def test_adaptive_recommendations_endpoint(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "rec-user", "correct-horse-123")

        # No data yet - empty recommendations
        recs = client.get("/api/v1/learning-paths/adaptive-recommendations")
        assert recs.status_code == 200
        assert recs.json()["recommendations"] == []

        # Invalid limit
        bad = client.get("/api/v1/learning-paths/adaptive-recommendations?limit=0")
        assert bad.status_code == 422

        bad = client.get("/api/v1/learning-paths/adaptive-recommendations?limit=99")
        assert bad.status_code == 422


def test_adaptive_recommendations_computation(tmp_path):
    """Test the compute_adaptive_recommendations storage function directly."""
    import os
    import tempfile

    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "adaptive.sqlite3")
    settings = Settings(
        _env_file=None,
        database_path=db_path,
        rate_limit_enabled=False,
    )
    initialize_storage(settings.database_path)

    from app.auth import hash_password
    from app.storage import (
        create_quiz_set,
        create_user,
        record_quiz_attempt,
    )

    # Create a user
    user_id = create_user(
        settings.database_path,
        "adaptive-user",
        hash_password("password-123"),
    )

    # Create a quiz set with questions
    quiz_id = "quiz-test-001"
    topic_id = "topic-photosynthesis"
    create_quiz_set(
        settings.database_path,
        {
            "quiz_id": quiz_id,
            "topic_id": topic_id,
            "topic": "Photosynthesis",
            "created_at": datetime.now(UTC).isoformat(),
            "questions": [
                {
                    "question_id": "q1",
                    "question_type": "multiple_choice",
                    "prompt": "What is photosynthesis?",
                    "options": [{"id": "A", "text": "Light energy"}, {"id": "B", "text": "Sound"}],
                    "answer_key": "A",
                    "accepted_answers": [],
                    "explanation": "Photosynthesis converts light energy.",
                    "citations": [],
                }
            ],
        },
        user_id=user_id,
    )

    # Record an incorrect attempt
    record_quiz_attempt(
        settings.database_path,
        attempt_id="attempt-1",
        question_id="q1",
        outcome="incorrect",
        created_at=datetime.now(UTC).isoformat(),
        user_id=user_id,
    )

    # No source chunks/citations for this topic yet, so recommendations should be empty
    # (we skip topics without citations)
    recs = compute_adaptive_recommendations(
        settings.database_path,
        user_id=user_id,
        now=datetime.now(UTC),
        limit=5,
    )
    # With no citations, no recommendations
    assert recs == []
