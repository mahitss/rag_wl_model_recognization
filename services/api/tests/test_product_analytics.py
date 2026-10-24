import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.auth import hash_password
from app.config import Settings
from app.main import create_app
from app.storage import connect_database, create_user, initialize_storage, record_product_event


def authenticate(client: TestClient, username: str) -> TestClient:
    csrf_token = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf_token})
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "test-password-123"},
    )
    assert response.status_code == 200
    return client


def register_user(database_path, username: str) -> str:
    return create_user(database_path, username, hash_password("test-password-123"))


def test_product_analytics_is_authenticated_user_scoped_and_sanitized(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "analytics.sqlite3",
        analytics_enabled=True,
        product_analytics_retention_days=30,
    )
    app = create_app(settings)
    initialize_storage(settings.database_path)
    first_user_id = register_user(settings.database_path, "analytics-one")
    register_user(settings.database_path, "analytics-two")

    with TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/product-analytics").status_code == 401

    with TestClient(app) as first_client, TestClient(app) as second_client:
        authenticate(first_client, "analytics-one")
        authenticate(second_client, "analytics-two")
        first_event = first_client.post(
            "/api/v1/product-analytics/events",
            json={
                "eventName": "document_upload_completed",
                "feature": "documents",
                "result": "success",
                "metadata": {
                    "byte_size": 1234,
                    "count": 1,
                    "duration_ms": 55,
                    "filename": "private-notes.pdf",
                    "documentId": "private-document-id",
                    "prompt": "private prompt text",
                    "notes": "sensitive free text",
                    "nested": {"token": "secret"},
                },
            },
        )
        assert first_event.status_code == 200
        assert first_event.json() == {"status": "recorded"}
        assert second_client.post(
            "/api/v1/product-analytics/events",
            json={
                "eventName": "image_upload_failed",
                "feature": "images",
                "result": "failure",
            },
        ).json() == {"status": "recorded"}
        for event_name, feature, result in (
            ("tutor_request_abstained", "tutor", "abstained"),
            ("quiz_started", "quizzes", "started"),
            ("study_coach_recommendation_dismissed", "study_coach", "dismissed"),
        ):
            event_response = first_client.post(
                "/api/v1/product-analytics/events",
                json={"eventName": event_name, "feature": feature, "result": result},
            )
            assert event_response.json() == {"status": "recorded"}

        first_summary = first_client.get("/api/v1/product-analytics").json()
        second_summary = second_client.get("/api/v1/product-analytics").json()
        assert first_summary["totals"] == {
            "eventCount": 4,
            "successCount": 1,
            "failureCount": 0,
            "featureCount": 4,
        }
        first_features = {feature["feature"]: feature for feature in first_summary["features"]}
        assert first_features["documents"]["positiveOutcomeRate"] == 1.0
        assert first_features["tutor"]["positiveOutcomeRate"] is None
        assert first_features["quizzes"]["positiveOutcomeRate"] is None
        assert first_features["study_coach"]["positiveOutcomeRate"] == 0.0
        assert second_summary["totals"]["eventCount"] == 1
        assert second_summary["features"][0]["feature"] == "images"

        with connect_database(settings.database_path) as connection:
            stored = connection.execute(
                "SELECT user_id, metadata_json FROM product_events WHERE user_id = ?",
                (first_user_id,),
            ).fetchone()
        assert stored["user_id"] == first_user_id
        assert json.loads(stored["metadata_json"]) == {
            "byte_size": 1234,
            "count": 1,
            "duration_ms": 55,
        }

        invalid_event = first_client.post(
            "/api/v1/product-analytics/events",
            json={
                "eventName": "private_document_name.pdf",
                "feature": "custom-feature",
                "result": "success",
            },
        )
        assert invalid_event.status_code == 422
        mismatched_event = first_client.post(
            "/api/v1/product-analytics/events",
            json={
                "eventName": "document_upload_completed",
                "feature": "images",
                "result": "success",
            },
        )
        assert mismatched_event.status_code == 422


def test_analytics_disabled_by_default_and_does_not_store_events(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "disabled.sqlite3")
    assert settings.analytics_enabled is False
    app = create_app(settings)
    initialize_storage(settings.database_path)
    register_user(settings.database_path, "analytics-disabled")

    with TestClient(app) as authenticated:
        authenticate(authenticated, "analytics-disabled")
        event_response = authenticated.post(
            "/api/v1/product-analytics/events",
            json={
                "eventName": "document_upload_completed",
                "feature": "documents",
                "result": "success",
            },
        )
        assert event_response.status_code == 200
        assert event_response.json() == {"status": "disabled"}
        summary = authenticated.get("/api/v1/product-analytics").json()
        assert summary["enabled"] is False
        assert summary["totals"]["eventCount"] == 0
        with connect_database(settings.database_path) as connection:
            assert connection.execute("SELECT count(*) FROM product_events").fetchone()[0] == 0


def test_analytics_retention_prunes_expired_events_on_summary_read(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "retention.sqlite3",
        analytics_enabled=True,
        product_analytics_retention_days=2,
    )
    app = create_app(settings)
    initialize_storage(settings.database_path)
    user_id = register_user(settings.database_path, "analytics-retention")
    record_product_event(
        settings.database_path,
        user_id=user_id,
        event_name="document_upload_completed",
        feature="documents",
        result="success",
    )
    expired_at = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    with connect_database(settings.database_path) as connection:
        with connection:
            connection.execute(
                "UPDATE product_events SET created_at = ? WHERE user_id = ?",
                (expired_at, user_id),
            )

    with TestClient(app) as authenticated:
        authenticate(authenticated, "analytics-retention")
        summary = authenticated.get("/api/v1/product-analytics").json()
        assert summary["totals"]["eventCount"] == 0
        with connect_database(settings.database_path) as connection:
            assert connection.execute("SELECT count(*) FROM product_events").fetchone()[0] == 0
