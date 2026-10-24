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
