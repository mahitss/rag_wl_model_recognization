"""Private-beta feedback and administrator review regressions."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.storage import initialize_storage


def _register_and_login(client: TestClient, username: str, password: str) -> None:
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 202
    login = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200


def test_feedback_submission_and_owner_updates(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "feedback.sqlite3",
        rate_limit_enabled=False,
    )
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "beta-user", "correct-horse-123")
        payload = {
            "category": "feature_request",
            "title": "Add beta feedback view",
            "description": "It would help to record beta notes without leaving the app.",
        }
        response = client.post("/api/v1/feedback", json=payload)
        assert response.status_code == 200
        result = response.json()
        assert result["message"] == "Your feedback was saved."
        feedback_id = result["feedback"]["feedback_id"]

        listed = client.get("/api/v1/feedback")
        assert listed.status_code == 200
        assert len(listed.json()["feedback"]) == 1

        updated = client.patch(
            f"/api/v1/feedback/{feedback_id}",
            json={"title": "Updated beta feedback view", "status": "withdrawn"},
        )
        assert updated.status_code == 200
        assert updated.json()["feedback"]["status"] == "withdrawn"

        overwritten = client.patch(
            f"/api/v1/feedback/{feedback_id}",
            json={"description": "tampered value"},
        )
        assert overwritten.status_code == 200

        another = client.post(
            "/api/v1/auth/logout",
        )
        assert another.status_code == 200

        _register_and_login(client, "beta-user-2", "different-horse-456")
        denied = client.patch(
            f"/api/v1/feedback/{feedback_id}",
            json={"title": "Pretend to edit someone else's feedback"},
        )
        assert denied.status_code == 403


def test_feedback_admin_review_requires_explicit_admin(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "feedback-admin.sqlite3",
        rate_limit_enabled=False,
        admin_usernames="admin-user",
    )
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "regular-user", "correct-horse-123")
        denied = client.get("/api/v1/feedback/admin")
        assert denied.status_code == 403

        client.post("/api/v1/auth/logout")
        _register_and_login(client, "admin-user", "secret-456-abc")
        create = client.post(
            "/api/v1/feedback",
            json={
                "category": "bug",
                "title": "Login bug",
                "description": "Bug report for beta testing.",
            },
        )
        assert create.status_code == 200
        feedback_id = create.json()["feedback"]["feedback_id"]

        admin_list = client.get("/api/v1/feedback/admin")
        assert admin_list.status_code == 200
        assert len(admin_list.json()["feedback"]) == 1

        admin_status = client.patch(
            f"/api/v1/feedback/{feedback_id}/status",
            json={"status": "reviewing"},
        )
        assert admin_status.status_code == 200
        assert admin_status.json()["feedback"]["status"] == "reviewing"


def test_feedback_anonymous_access_rejected(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "feedback-anon.sqlite3",
        rate_limit_enabled=False,
    )
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        create = client.post(
            "/api/v1/feedback",
            json={"category": "bug", "title": "Anon bug", "description": "Anon description."},
        )
        assert create.status_code == 403

        listed = client.get("/api/v1/feedback")
        assert listed.status_code == 401

        admin_list = client.get("/api/v1/feedback/admin")
        assert admin_list.status_code == 401

        patch = client.patch(
            "/api/v1/feedback/feedback_nonexistent",
            json={"title": "Anon edit"},
        )
        assert patch.status_code == 403

        admin_status = client.patch(
            "/api/v1/feedback/feedback_nonexistent/status",
            json={"status": "reviewing"},
        )
        assert admin_status.status_code == 403


def test_feedback_oversized_input_rejected(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "feedback-oversized.sqlite3",
        rate_limit_enabled=False,
    )
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "oversized-user", "correct-horse-123")

        oversized_title = "x" * 201
        oversized_description = "x" * 4001
        response = client.post(
            "/api/v1/feedback",
            json={
                "category": "bug",
                "title": oversized_title,
                "description": oversized_description,
            },
        )
        assert response.status_code == 422

        empty_title = client.post(
            "/api/v1/feedback",
            json={"category": "bug", "title": "", "description": "Valid description."},
        )
        assert empty_title.status_code == 422

        empty_description = client.post(
            "/api/v1/feedback",
            json={"category": "bug", "title": "Valid title", "description": ""},
        )
        assert empty_description.status_code == 422


def test_feedback_rate_limit_applies(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "feedback-ratelimit.sqlite3",
        rate_limit_enabled=True,
        rate_limit_requests_per_minute=3,
    )
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "ratelimit-user", "correct-horse-123")
        payload = {
            "category": "bug",
            "title": "Rate limit test",
            "description": "Testing rate limiting on feedback submission.",
        }
        for _ in range(3):
            response = client.post("/api/v1/feedback", json=payload)
            assert response.status_code == 200, response.text

        limited = client.post("/api/v1/feedback", json=payload)
        assert limited.status_code == 429


def _make_admin_client(tmp_path, admin_username="admin-user"):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "feedback-m32.sqlite3",
        rate_limit_enabled=False,
        admin_usernames=admin_username,
    )
    initialize_storage(settings.database_path)
    client = TestClient(create_app(settings))
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    client.post(
        "/api/v1/auth/register",
        json={"username": admin_username, "password": "secret-456-abc"},
    )
    client.post(
        "/api/v1/auth/login",
        json={"username": admin_username, "password": "secret-456-abc"},
    )
    return client


def _submit_feedback(
    client,
    category="bug",
    title="Feedback title",
    description="Feedback description.",
):
    return client.post(
        "/api/v1/feedback",
        json={"category": category, "title": title, "description": description},
    )


def test_feedback_admin_filtering_by_category_and_status(tmp_path):
    client = _make_admin_client(tmp_path)
    _submit_feedback(client, "bug", "Bug one", "First bug.")
    _submit_feedback(client, "usability", "Usability one", "Usability issue.")
    _submit_feedback(client, "bug", "Bug two", "Second bug.")

    # Filter by category
    result = client.get("/api/v1/feedback/admin", params={"category": "bug"})
    assert result.status_code == 200
    assert len(result.json()["feedback"]) == 2
    assert all(item["category"] == "bug" for item in result.json()["feedback"])

    # Filter by status
    result = client.get("/api/v1/feedback/admin", params={"status": "new"})
    assert result.status_code == 200
    assert len(result.json()["feedback"]) == 3
    assert all(item["status"] == "new" for item in result.json()["feedback"])

    # Combined filter
    result = client.get("/api/v1/feedback/admin", params={"category": "usability", "status": "new"})
    assert result.status_code == 200
    assert len(result.json()["feedback"]) == 1
    assert result.json()["feedback"][0]["title"] == "Usability one"


def test_feedback_admin_sorting_by_recency_and_created(tmp_path):
    client = _make_admin_client(tmp_path)
    _submit_feedback(client, "bug", "First", "First feedback.")
    _submit_feedback(client, "bug", "Second", "Second feedback.")

    # Default: recency descending (newest first)
    result = client.get("/api/v1/feedback/admin")
    assert result.status_code == 200
    items = result.json()["feedback"]
    assert items[0]["title"] == "Second"
    assert items[1]["title"] == "First"

    # Sort by created ascending
    result = client.get(
        "/api/v1/feedback/admin", params={"sort_by": "created", "sort_order": "asc"}
    )
    assert result.status_code == 200
    items = result.json()["feedback"]
    assert items[0]["title"] == "First"
    assert items[1]["title"] == "Second"


def test_feedback_status_transitions_include_new_statuses(tmp_path):
    client = _make_admin_client(tmp_path)
    create = _submit_feedback(client, "bug", "Status test", "Testing status flow.")
    assert create.status_code == 200
    feedback_id = create.json()["feedback"]["feedback_id"]

    # new -> reviewing
    result = client.patch(
        f"/api/v1/feedback/{feedback_id}/status",
        json={"status": "reviewing"},
    )
    assert result.status_code == 200
    assert result.json()["feedback"]["status"] == "reviewing"

    # reviewing -> in_progress (new M32 status)
    result = client.patch(
        f"/api/v1/feedback/{feedback_id}/status",
        json={"status": "in_progress"},
    )
    assert result.status_code == 200
    assert result.json()["feedback"]["status"] == "in_progress"

    # in_progress -> resolved
    result = client.patch(
        f"/api/v1/feedback/{feedback_id}/status",
        json={"status": "resolved"},
    )
    assert result.status_code == 200
    assert result.json()["feedback"]["status"] == "resolved"


def test_feedback_declined_status(tmp_path):
    client = _make_admin_client(tmp_path)
    create = _submit_feedback(client, "feature_request", "Declined feature", "Won't implement.")
    assert create.status_code == 200
    feedback_id = create.json()["feedback"]["feedback_id"]

    result = client.patch(
        f"/api/v1/feedback/{feedback_id}/status",
        json={"status": "declined"},
    )
    assert result.status_code == 200
    assert result.json()["feedback"]["status"] == "declined"


def test_feedback_admin_resolution_notes(tmp_path):
    client = _make_admin_client(tmp_path)
    create = _submit_feedback(client, "bug", "Resolution test", "Needs fixing.")
    assert create.status_code == 200
    feedback_id = create.json()["feedback"]["feedback_id"]

    result = client.patch(
        f"/api/v1/feedback/admin/{feedback_id}",
        json={"resolution_notes": "Fixed in v1.2.0."},
    )
    assert result.status_code == 200
    assert result.json()["feedback"]["resolution_notes"] == "Fixed in v1.2.0."

    # Verify resolution_notes appears in admin list (schema-serialized -> camelCase)
    listed = client.get("/api/v1/feedback/admin")
    assert listed.status_code == 200
    item = listed.json()["feedback"][0]
    assert item["resolutionNotes"] == "Fixed in v1.2.0."


def test_feedback_admin_cannot_set_invalid_status(tmp_path):
    client = _make_admin_client(tmp_path)
    create = _submit_feedback(client, "bug", "Invalid status test", "Testing.")
    assert create.status_code == 200
    feedback_id = create.json()["feedback"]["feedback_id"]

    result = client.patch(
        f"/api/v1/feedback/{feedback_id}/status",
        json={"status": "not_a_real_status"},
    )
    assert result.status_code == 422


def test_improvement_task_creation_and_duplicate_prevention(tmp_path):
    client = _make_admin_client(tmp_path)
    payload = {
        "title": "Fix login flow",
        "description": "The login flow is broken for new users.",
        "severity": 4,
        "impact": 4,
        "reproducibility": 5,
        "workflow_frequency": 4,
        "implementation_effort": 3,
        "regression_test": "test_login_flow",
    }
    create = client.post("/api/v1/feedback/admin/improvement-tasks", json=payload)
    assert create.status_code == 200
    task = create.json()
    assert task["taskId"].startswith("task_")
    assert task["title"] == "Fix login flow"
    assert task["status"] == "proposed"
    assert task["reportCount"] == 0  # measured: no feedback linked yet
    assert task["severity"] == 4  # estimated
    assert task["impact"] == 4  # estimated
    # Priority score: 4*20 + 4*15 + 0*10 + 5*10 + 4*10 - 3*5 = 80+60+0+50+40-15 = 215
    assert task["priorityScore"] == 215.0
    assert task["priorityFactors"]["measured"] == ["report_count"]
    assert "severity" in task["priorityFactors"]["estimated"]

    # Duplicate title (case-insensitive) should be rejected
    duplicate = client.post("/api/v1/feedback/admin/improvement-tasks", json=payload)
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["error"]["message"]


def test_improvement_task_priority_recompute_on_link(tmp_path):
    client = _make_admin_client(tmp_path)
    # Submit feedback first
    fb = _submit_feedback(client, "bug", "Linked bug", "A bug to link.")
    assert fb.status_code == 200
    feedback_id = fb.json()["feedback"]["feedback_id"]

    # Create task with no linked feedback (report_count = 0)
    create = client.post(
        "/api/v1/feedback/admin/improvement-tasks",
        json={
            "title": "Linked task",
            "description": "A task.",
            "severity": 3,
            "impact": 3,
            "reproducibility": 3,
            "workflowFrequency": 3,
            "implementationEffort": 3,
        },
    )
    assert create.status_code == 200
    task_id = create.json()["taskId"]
    assert create.json()["reportCount"] == 0

    # Link the feedback - report_count should become 1 (measured)
    link = client.post(f"/api/v1/feedback/admin/improvement-tasks/{task_id}/feedback/{feedback_id}")
    assert link.status_code == 200
    assert link.json()["reportCount"] == 1  # measured: 1 feedback linked
    # Priority: 3*20 + 3*15 + 1*10 + 3*10 + 3*10 - 3*5 = 60+45+10+30+30-15 = 160
    assert link.json()["priorityScore"] == 160.0

    # Unlink - report_count back to 0
    unlink = client.delete(
        f"/api/v1/feedback/admin/improvement-tasks/{task_id}/feedback/{feedback_id}"
    )
    assert unlink.status_code == 200
    assert unlink.json()["reportCount"] == 0


def test_improvement_task_creation_with_linked_feedback(tmp_path):
    client = _make_admin_client(tmp_path)
    fb1 = _submit_feedback(client, "bug", "Bug A", "First bug.")
    fb2 = _submit_feedback(client, "bug", "Bug B", "Second bug.")
    assert fb1.status_code == 200
    assert fb2.status_code == 200
    fid1 = fb1.json()["feedback"]["feedback_id"]
    fid2 = fb2.json()["feedback"]["feedback_id"]

    create = client.post(
        "/api/v1/feedback/admin/improvement-tasks",
        json={
            "title": "Multi-bug task",
            "description": "Addresses multiple bugs.",
            "severity": 5,
            "impact": 5,
            "reproducibility": 5,
            "workflowFrequency": 5,
            "implementationEffort": 5,
            "regressionTest": "test_multi_bug",
            "feedbackIds": [fid1, fid2],
        },
    )
    assert create.status_code == 200
    task = create.json()
    assert task["reportCount"] == 2  # measured: 2 feedback items
    assert set(task["feedbackIds"]) == {fid1, fid2}
    # Priority: 5*20 + 5*15 + 2*10 + 5*10 + 5*10 - 5*5 = 100+75+20+50+50-25 = 270
    assert task["priorityScore"] == 270.0

    # Verify feedback items now reference the task
    listed = client.get("/api/v1/feedback/admin")
    assert listed.status_code == 200
    for item in listed.json()["feedback"]:
        assert item["improvementTaskId"] == task["taskId"]


def test_improvement_task_list_and_update(tmp_path):
    client = _make_admin_client(tmp_path)
    # Empty state
    result = client.get("/api/v1/feedback/admin/improvement-tasks")
    assert result.status_code == 200
    assert result.json()["tasks"] == []

    create = client.post(
        "/api/v1/feedback/admin/improvement-tasks",
        json={
            "title": "Update test task",
            "description": "To be updated.",
            "severity": 2,
            "impact": 2,
            "reproducibility": 2,
            "workflowFrequency": 2,
            "implementationEffort": 2,
        },
    )
    assert create.status_code == 200
    task_id = create.json()["taskId"]

    # List with status filter
    result = client.get("/api/v1/feedback/admin/improvement-tasks", params={"status": "proposed"})
    assert result.status_code == 200
    assert len(result.json()["tasks"]) == 1

    # Update: change status and priority factors
    update = client.patch(
        f"/api/v1/feedback/admin/improvement-tasks/{task_id}",
        json={
            "status": "in_progress",
            "severity": 4,
            "implementationEffort": 1,
        },
    )
    assert update.status_code == 200
    assert update.json()["status"] == "in_progress"
    assert update.json()["severity"] == 4
    assert update.json()["implementationEffort"] == 1
    # Priority recomputed: 4*20 + 2*15 + 0*10 + 2*10 + 2*10 - 1*5 = 80+30+0+20+20-5 = 145
    assert update.json()["priorityScore"] == 145.0

    # Mark implemented
    complete = client.patch(
        f"/api/v1/feedback/admin/improvement-tasks/{task_id}",
        json={"implemented": True},
    )
    assert complete.status_code == 200
    assert complete.json()["implemented"] is True
    assert complete.json()["implementedAt"] is not None


def test_improvement_task_not_found(tmp_path):
    client = _make_admin_client(tmp_path)
    result = client.patch(
        "/api/v1/feedback/admin/improvement-tasks/nonexistent-task",
        json={"title": "New title"},
    )
    assert result.status_code == 404

    result = client.post(
        "/api/v1/feedback/admin/improvement-tasks/nonexistent-task/feedback/nonexistent-feedback"
    )
    assert result.status_code == 404


def test_feedback_admin_requires_admin(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "feedback-nonadmin.sqlite3",
        rate_limit_enabled=False,
    )
    initialize_storage(settings.database_path)
    client = TestClient(create_app(settings))
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    client.post(
        "/api/v1/auth/register",
        json={"username": "regular-user", "password": "correct-horse-123"},
    )
    client.post(
        "/api/v1/auth/login",
        json={"username": "regular-user", "password": "correct-horse-123"},
    )

    # Non-admin cannot access admin endpoints
    assert client.get("/api/v1/feedback/admin").status_code == 403
    assert client.post("/api/v1/feedback/admin/improvement-tasks", json={}).status_code == 403
