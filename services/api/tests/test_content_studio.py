"""AI Learning Content Studio regressions: ownership, citations, insufficient evidence,
malformed output, regeneration, persistence, editing, deletion."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.auth import hash_password
from app.config import Settings
from app.main import create_app
from app.storage import (
    create_content_resource,
    create_content_section,
    create_user,
    delete_content_resource,
    get_content_resource,
    initialize_storage,
    list_content_resources,
    list_content_revisions,
    regenerate_content_section,
    update_content_resource,
    update_content_section,
)


def _register_and_login(client: TestClient, username: str, password: str) -> str:
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    client.post("/api/v1/auth/register", json={"username": username, "password": password})
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["userId"]


def _make_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_path=str(tmp_path) + "/content-studio.sqlite3",
        rate_limit_enabled=False,
    )


# A valid tutor-generated content response for mocking.
_MOCK_GENERATION = {
    "title": "Photosynthesis Study Notes",
    "sections": [
        {
            "sectionType": "heading",
            "sectionTitle": "Overview",
            "content": "Photosynthesis converts light energy into chemical energy [E1].",
            "citations": [
                {
                    "citationId": "chk_1",
                    "documentId": "doc_1",
                    "displayFilename": "photosynthesis.pdf",
                    "pageNumber": 1,
                    "chunkIndex": 0,
                    "charStart": 0,
                    "charEnd": 50,
                    "text": "Photosynthesis converts light energy into chemical energy.",
                    "retrieval": {"method": "fts5_bm25", "rank": 1},
                }
            ],
        },
        {
            "sectionType": "body",
            "sectionTitle": "Inputs and outputs",
            "content": "Carbon dioxide and water are inputs; oxygen is released [E2].",
            "citations": [
                {
                    "citationId": "chk_2",
                    "documentId": "doc_1",
                    "displayFilename": "photosynthesis.pdf",
                    "pageNumber": 1,
                    "chunkIndex": 1,
                    "charStart": 50,
                    "charEnd": 100,
                    "text": (
                        "Carbon dioxide and water are inputs; oxygen is released as a byproduct."
                    ),
                    "retrieval": {"method": "fts5_bm25", "rank": 2},
                }
            ],
        },
    ],
    "abstain": False,
    "evidenceGaps": [],
}


def _mock_local_chat_completion(*args, **kwargs):
    return json.dumps(_MOCK_GENERATION)


def _mock_generate_content(settings, *, topic, format, detail_level, evidence):
    return _MOCK_GENERATION.copy()


def _upload_test_pdf(client: TestClient) -> str:
    """Upload a minimal PDF with photosynthesis text and return its document ID."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 12 Tf 72 720 Td (Photosynthesis converts light energy into chemical energy."
        b" Carbon dioxide and water are inputs; oxygen is released as a byproduct.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    pdf_bytes = output.getvalue()
    response = client.post(
        "/api/v1/documents",
        files={"file": ("photosynthesis.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()["documentId"]


def test_content_resource_crud_with_mocked_tutor(tmp_path):
    """Full CRUD lifecycle with a mocked tutor."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        user_id = _register_and_login(client, "content-user", "correct-horse-123")
        _upload_test_pdf(client)

        # Mock the tutor to return valid content.
        with patch("app.api.v1.content.generate_content", side_effect=_mock_generate_content):
            create = client.post(
                "/api/v1/content/resources",
                json={
                    "title": "Photosynthesis Notes",
                    "description": "Study notes on photosynthesis",
                    "format": "notes",
                    "detailLevel": "standard",
                    "topic": "photosynthesis",
                },
            )
        assert create.status_code == 201, create.text
        body = create.json()
        assert body["status"] == "draft"
        assert body["title"] == "Photosynthesis Study Notes"
        assert body["format"] == "notes"
        assert len(body["sections"]) == 2
        resource_id = body["resourceId"]

        # Each section should carry citations.
        for section in body["sections"]:
            assert "sectionId" in section
            assert "citations" in section
            assert isinstance(section["citations"], list)

        # List resources.
        listed = client.get("/api/v1/content/resources")
        assert listed.status_code == 200
        assert len(listed.json()["resources"]) == 1

        # Get single resource.
        got = client.get(f"/api/v1/content/resources/{resource_id}")
        assert got.status_code == 200
        assert got.json()["resourceId"] == resource_id
        assert len(got.json()["sections"]) == 2

        # Update metadata.
        updated = client.patch(
            f"/api/v1/content/resources/{resource_id}",
            json={"title": "Updated Notes", "status": "reviewed"},
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "Updated Notes"
        assert updated.json()["status"] == "reviewed"
        assert updated.json()["reviewedAt"] is not None
        assert updated.json()["reviewedBy"] == user_id

        # Delete.
        deleted = client.delete(f"/api/v1/content/resources/{resource_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        # Gone.
        got = client.get(f"/api/v1/content/resources/{resource_id}")
        assert got.status_code == 404


def test_content_resource_cross_user_isolation(tmp_path):
    """User B cannot access, edit, or delete User A's content resources."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client_a:
        _register_and_login(client_a, "content-owner-a", "password-a")
        _upload_test_pdf(client_a)
        with patch("app.api.v1.content.generate_content", side_effect=_mock_generate_content):
            create = client_a.post(
                "/api/v1/content/resources",
                json={
                    "title": "Private notes",
                    "description": "Only for A",
                    "format": "summary",
                    "detailLevel": "brief",
                    "topic": "photosynthesis",
                },
            )
        assert create.status_code == 201
        resource_id = create.json()["resourceId"]

        # User B cannot see or touch it.
        client_b = TestClient(create_app(settings))
        _register_and_login(client_b, "content-user-b", "password-b")

        got = client_b.get(f"/api/v1/content/resources/{resource_id}")
        assert got.status_code == 404

        listed = client_b.get("/api/v1/content/resources")
        assert len(listed.json()["resources"]) == 0

        updated = client_b.patch(
            f"/api/v1/content/resources/{resource_id}",
            json={"title": "Hacked"},
        )
        assert updated.status_code == 403

        deleted = client_b.delete(f"/api/v1/content/resources/{resource_id}")
        assert deleted.status_code == 403

        # User A still owns it.
        got = client_a.get(f"/api/v1/content/resources/{resource_id}")
        assert got.status_code == 200


def test_content_insufficient_evidence(tmp_path):
    """When no documents match the topic, generation abstains with insufficient evidence."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "no-evidence-user", "password-a")

        # No documents uploaded at all.
        create = client.post(
            "/api/v1/content/resources",
            json={
                "title": "Unknown topic",
                "format": "notes",
                "detailLevel": "standard",
                "topic": "quantum chromodynamics",
            },
        )
        assert create.status_code == 201, create.text
        body = create.json()
        assert body["status"] == "insufficient_evidence"
        assert body["resourceId"] is None
        assert len(body["evidenceGaps"]) > 0


def test_content_section_edit_and_regenerate(tmp_path):
    """Edit a section and regenerate it; revisions are preserved."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    database_path = settings.database_path
    user_id = create_user(database_path, "regen-user", hash_password("password-123"))

    # Create a resource and section directly via storage (bypassing tutor).
    resource = create_content_resource(
        database_path,
        user_id=user_id,
        title="Test Resource",
        description=None,
        format="notes",
        detail_level="standard",
        source_type="document",
        source_refs=[
            {
                "type": "document",
                "documentId": "doc_1",
                "displayFilename": "test.pdf",
                "pageNumber": 1,
                "chunkIndex": 0,
                "retrieval": {"method": "fts5_bm25", "rank": 1},
            }
        ],
    )
    resource_id = resource["resourceId"]

    section = create_content_section(
        database_path,
        resource_id=resource_id,
        user_id=user_id,
        section_type="body",
        section_title="Original section",
        content="Original content about photosynthesis [E1].",
        citations=[
            {
                "citationId": "chk_1",
                "documentId": "doc_1",
                "displayFilename": "test.pdf",
                "pageNumber": 1,
                "chunkIndex": 0,
                "charStart": 0,
                "charEnd": 40,
                "text": "Photosynthesis converts light energy.",
                "retrieval": {"method": "fts5_bm25", "rank": 1},
            }
        ],
    )
    section_id = section["sectionId"]
    assert section["version"] == 1

    # Edit the section.
    updated = update_content_section(
        database_path,
        resource_id=resource_id,
        section_id=section_id,
        user_id=user_id,
        section_title="Edited section",
        content="Edited content about photosynthesis [E1].",
        citations=None,
    )
    assert updated is not None
    assert updated["sectionTitle"] == "Edited section"
    assert updated["content"] == "Edited content about photosynthesis [E1]."
    assert updated["version"] == 2

    # Regenerate the section (preserves v2 as a revision).
    regenerated = regenerate_content_section(
        database_path,
        resource_id=resource_id,
        section_id=section_id,
        user_id=user_id,
        content="Regenerated content about photosynthesis [E1].",
        citations=None,
    )
    assert regenerated is not None
    assert regenerated["content"] == "Regenerated content about photosynthesis [E1]."
    assert regenerated["version"] == 3

    # List revisions: should have v1 and v2 (original + edited).
    revisions = list_content_revisions(
        database_path,
        resource_id=resource_id,
        section_id=section_id,
        user_id=user_id,
    )
    assert revisions is not None
    assert len(revisions) == 2  # v1 and v2 archived before regeneration

    # Cross-user cannot edit or regenerate.
    user_id_b = create_user(database_path, "regen-user-b", hash_password("password-123"))
    updated_b = update_content_section(
        database_path,
        resource_id=resource_id,
        section_id=section_id,
        user_id=user_id_b,
        content="Hacked content",
    )
    assert updated_b is None

    regenerated_b = regenerate_content_section(
        database_path,
        resource_id=resource_id,
        section_id=section_id,
        user_id=user_id_b,
        content="Hacked content",
    )
    assert regenerated_b is None


def test_content_storage_direct_operations(tmp_path):
    """Test storage functions directly without the tutor."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    database_path = settings.database_path
    user_id = create_user(database_path, "direct-user", hash_password("password-123"))

    # Create resource.
    resource = create_content_resource(
        database_path,
        user_id=user_id,
        title="Direct resource",
        description="A test resource",
        format="flashcards",
        detail_level="brief",
        source_type="document",
        source_refs=[],
    )
    assert resource["status"] == "draft"
    assert resource["format"] == "flashcards"
    assert resource["reviewedAt"] is None

    # List.
    resources = list_content_resources(database_path, user_id=user_id)
    assert len(resources) == 1
    assert resources[0]["resourceId"] == resource["resourceId"]

    # Get with sections (no sections yet).
    got = get_content_resource(
        database_path, resource_id=resource["resourceId"], user_id=user_id
    )
    assert got is not None
    assert got["sections"] == []

    # Create section.
    section = create_content_section(
        database_path,
        resource_id=resource["resourceId"],
        user_id=user_id,
        section_type="flashcard",
        section_title="Q: What is photosynthesis?",
        content="A: It converts light energy to chemical energy.",
        citations=[],
    )
    assert section["version"] == 1

    # Get with sections now.
    got = get_content_resource(
        database_path, resource_id=resource["resourceId"], user_id=user_id
    )
    assert len(got["sections"]) == 1
    assert got["sections"][0]["sectionId"] == section["sectionId"]

    # Update resource metadata.
    updated = update_content_resource(
        database_path,
        resource_id=resource["resourceId"],
        user_id=user_id,
        title="Updated direct resource",
        status="reviewed",
    )
    assert updated["title"] == "Updated direct resource"
    assert updated["status"] == "reviewed"
    assert updated["reviewedAt"] is not None

    # Delete.
    deleted = delete_content_resource(
        database_path, resource_id=resource["resourceId"], user_id=user_id
    )
    assert deleted is True

    # Gone.
    got = get_content_resource(
        database_path, resource_id=resource["resourceId"], user_id=user_id
    )
    assert got is None

    # Cross-user cannot delete.
    resource2 = create_content_resource(
        database_path,
        user_id=user_id,
        title="Another resource",
        description=None,
        format="summary",
        detail_level="standard",
        source_type="document",
        source_refs=[],
    )
    user_id_b = create_user(database_path, "direct-user-b", hash_password("password-123"))
    deleted_b = delete_content_resource(
        database_path, resource_id=resource2["resourceId"], user_id=user_id_b
    )
    assert deleted_b is False


def test_content_review_and_archive_status_transitions(tmp_path):
    """Review and archive status transitions work correctly."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "review-user", "password-a")
        _upload_test_pdf(client)
        with patch("app.api.v1.content.generate_content", side_effect=_mock_generate_content):
            create = client.post(
                "/api/v1/content/resources",
                json={
                    "title": "To review",
                    "format": "notes",
                    "detailLevel": "standard",
                    "topic": "photosynthesis",
                },
            )
        assert create.status_code == 201
        resource_id = create.json()["resourceId"]
        assert create.json()["status"] == "draft"

        # Review it.
        reviewed = client.patch(
            f"/api/v1/content/resources/{resource_id}",
            json={"status": "reviewed"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["status"] == "reviewed"
        assert reviewed.json()["reviewedAt"] is not None

        # Archive it.
        archived = client.patch(
            f"/api/v1/content/resources/{resource_id}",
            json={"status": "archived"},
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"

        # Invalid status rejected.
        bad = client.patch(
            f"/api/v1/content/resources/{resource_id}",
            json={"status": "published"},
        )
        assert bad.status_code == 422


def test_content_invalid_format_and_detail_level(tmp_path):
    """Invalid format or detail level values are rejected by schema validation."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "invalid-user", "password-a")

        bad_format = client.post(
            "/api/v1/content/resources",
            json={
                "title": "Bad format",
                "format": "poem",
                "detailLevel": "standard",
                "topic": "photosynthesis",
            },
        )
        assert bad_format.status_code == 422

        bad_detail = client.post(
            "/api/v1/content/resources",
            json={
                "title": "Bad detail",
                "format": "notes",
                "detailLevel": "expert",
                "topic": "photosynthesis",
            },
        )
        assert bad_detail.status_code == 422
