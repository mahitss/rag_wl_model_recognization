"""Content validation regressions: ownership, citation provenance, claim support,
malformed output, insufficient evidence, conflicting evidence, human review,
and cross-user access."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import Settings
from app.main import create_app
from app.storage import (
    create_content_resource,
    create_content_section,
    get_content_resource,
    get_latest_content_validation,
    initialize_storage,
    list_content_revisions,
    store_document,
)
from app.content_validation import validate_section


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
        database_path=str(tmp_path) + "/content-validation.sqlite3",
        rate_limit_enabled=False,
    )


def _seed_resource_and_section(
    database_path, user_id: str, section_content: str, citation_text: str
):
    """Seed one document/chunk, one resource, and one section citing it."""
    document_id = "doc_valid"
    chunk_id = "chk_valid"
    store_document(
        database_path,
        {
            "document_id": document_id,
            "display_name": "study.pdf",
            "sha256": "0" * 64,
            "byte_size": 100,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "parser_version": "test/1.0",
        },
        [
            {
                "chunk_id": chunk_id,
                "document_id": document_id,
                "page_number": 1,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": len(citation_text),
                "text": citation_text,
                "user_id": user_id,
            }
        ],
        user_id=user_id,
    )
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
                "documentId": document_id,
                "displayFilename": "study.pdf",
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
        section_title="Claim section",
        content=section_content,
        citations=[
            {
                "citationId": chunk_id,
                "documentId": document_id,
                "displayFilename": "study.pdf",
                "pageNumber": 1,
                "chunkIndex": 0,
                "charStart": 0,
                "charEnd": len(citation_text),
                "text": citation_text,
                "retrieval": {"method": "fts5_bm25", "rank": 1},
            }
        ],
    )
    return resource_id, section["sectionId"]


def test_content_validation_valid_citations(tmp_path):
    """Valid citations referencing real, owned chunks pass validation."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        user_id = _register_and_login(client, "valid-user", "correct-horse-123")
        citation_text = "Photosynthesis converts light energy into chemical energy."
        resource_id, _ = _seed_resource_and_section(
            settings.database_path,
            user_id,
            "Light energy becomes chemical energy [E1].",
            citation_text,
        )

        response = client.post(f"/api/v1/content/resources/{resource_id}/validate")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["citationValidity"] is True
        assert body["schemaValidity"] is True
        assert body["unsupportedClaimCount"] == 0
        assert body["status"] in {"evidence_found", "verified"}

        # Persisted validation record.
        record = get_latest_content_validation(
            settings.database_path, resource_id=resource_id, user_id=user_id
        )
        assert record is not None
        assert record["citationValidity"] is True


def test_content_validation_invalid_citation(tmp_path):
    """A citation referencing a non-existent chunk fails validation."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        user_id = _register_and_login(client, "invalid-user", "correct-horse-123")
        resource_id, _ = _seed_resource_and_section(
            settings.database_path,
            user_id,
            "Light energy becomes chemical energy [E1].",
            "Photosynthesis converts light energy into chemical energy.",
        )

        # Cite a chunk that does not exist.
        create_content_section(
            settings.database_path,
            resource_id=resource_id,
            user_id=user_id,
            section_type="body",
            section_title="Bad citation",
            content="Something else [E9].",
            citations=[
                {
                    "citationId": "chk_nonexistent",
                    "documentId": "doc_valid",
                    "displayFilename": "study.pdf",
                    "pageNumber": 1,
                    "chunkIndex": 0,
                    "charStart": 0,
                    "charEnd": 10,
                    "text": "something",
                    "retrieval": {"method": "fts5_bm25", "rank": 1},
                }
            ],
        )

        response = client.post(f"/api/v1/content/resources/{resource_id}/validate")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["citationValidity"] is False
        assert body["status"] == "insufficient_evidence"
        invalid_ids = [c["citationId"] for c in body["details"]["invalid_citations"]]
        assert "chk_nonexistent" in invalid_ids


def test_content_validation_wrong_provenance(tmp_path):
    """A citation whose page/chunk/char range does not match the stored chunk fails."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        user_id = _register_and_login(client, "provenance-user", "correct-horse-123")
        resource_id, _ = _seed_resource_and_section(
            settings.database_path,
            user_id,
            "Light energy becomes chemical energy [E1].",
            "Photosynthesis converts light energy into chemical energy.",
        )

        # Cite the real chunk but with a wrong page number.
        create_content_section(
            settings.database_path,
            resource_id=resource_id,
            user_id=user_id,
            section_type="body",
            section_title="Wrong provenance",
            content="Light energy becomes chemical energy [E1].",
            citations=[
                {
                    "citationId": "chk_valid",
                    "documentId": "doc_valid",
                    "displayFilename": "study.pdf",
                    "pageNumber": 99,  # wrong page
                    "chunkIndex": 0,
                    "charStart": 0,
                    "charEnd": len("Photosynthesis converts light energy into chemical energy."),
                    "text": "Photosynthesis converts light energy into chemical energy.",
                    "retrieval": {"method": "fts5_bm25", "rank": 1},
                }
            ],
        )

        response = client.post(f"/api/v1/content/resources/{resource_id}/validate")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["citationValidity"] is False
        assert body["status"] == "insufficient_evidence"


def test_content_validation_unsupported_claim(tmp_path):
    """A claim not supported by the cited evidence is flagged as unsupported."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        user_id = _register_and_login(client, "unsupported-user", "correct-horse-123")
        # Evidence talks about photosynthesis; claim talks about something unrelated.
        resource_id, _ = _seed_resource_and_section(
            settings.database_path,
            user_id,
            "The moon is made of green cheese [E1].",
            "Photosynthesis converts light energy into chemical energy.",
        )

        response = client.post(f"/api/v1/content/resources/{resource_id}/validate")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["unsupportedClaimCount"] >= 1
        assert body["status"] == "needs_review"


def test_content_validation_schema_invalid(tmp_path):
    """A section with an undeclared citation marker fails schema validity."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        user_id = _register_and_login(client, "schema-user", "correct-horse-123")
        resource_id, _ = _seed_resource_and_section(
            settings.database_path,
            user_id,
            "Light energy becomes chemical energy [E9].",  # E9 not cited
            "Photosynthesis converts light energy into chemical energy.",
        )

        response = client.post(f"/api/v1/content/resources/{resource_id}/validate")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["schemaValidity"] is False
        assert body["status"] == "insufficient_evidence"


def test_content_validation_empty_content(tmp_path):
    """A section with empty content is treated as unsupported."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    # Empty content cannot be stored, so validate the section logic directly.
    result = validate_section(
        {
            "sectionType": "body",
            "sectionTitle": "Empty",
            "content": "",
            "citations": [],
        },
        {},
    )
    assert result["schema_validity"] is False
    assert result["citation_validity"] is True
    assert result["unsupported_claim_count"] == 0
    assert result["evidence_coverage"] == 0.0


def test_content_validation_human_review(tmp_path):
    """A human review decision is authoritative and recorded with the actor."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        user_id = _register_and_login(client, "review-user", "correct-horse-123")
        resource_id, section_id = _seed_resource_and_section(
            settings.database_path,
            user_id,
            "Light energy becomes chemical energy [E1].",
            "Photosynthesis converts light energy into chemical energy.",
        )

        # Initially deterministic (not yet reviewed).
        resource = get_content_resource(
            settings.database_path, resource_id=resource_id, user_id=user_id
        )
        section = next(s for s in resource["sections"] if s["sectionId"] == section_id)
        assert section["verificationMethod"] == "deterministic"

        # Human marks it verified.
        reviewed = client.patch(
            f"/api/v1/content/resources/{resource_id}/sections/{section_id}/verify",
            json={"verificationStatus": "verified", "verificationNotes": "Looks good to me."},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["verificationStatus"] == "verified"
        assert reviewed.json()["verificationMethod"] == "human"
        assert reviewed.json()["verifiedBy"] == user_id
        assert reviewed.json()["verifiedAt"] is not None
        assert "Looks good to me" in reviewed.json()["verificationDetails"].get("reviewerNotes", "")

        # Persists.
        resource = get_content_resource(
            settings.database_path, resource_id=resource_id, user_id=user_id
        )
        section = next(s for s in resource["sections"] if s["sectionId"] == section_id)
        assert section["verificationStatus"] == "verified"
        assert section["verifiedBy"] == user_id


def test_content_validation_cross_user_isolation(tmp_path):
    """User B cannot validate User A's resource."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client_a:
        user_id = _register_and_login(client_a, "content-owner-a", "password-a")
        resource_id, _ = _seed_resource_and_section(
            settings.database_path,
            user_id,
            "Light energy becomes chemical energy [E1].",
            "Photosynthesis converts light energy into chemical energy.",
        )

    with TestClient(create_app(settings)) as client_b:
        _register_and_login(client_b, "content-user-b", "password-b")
        response = client_b.post(f"/api/v1/content/resources/{resource_id}/validate")
        assert response.status_code == 404, response.text


def test_content_regenerate_preserves_revision_after_review(tmp_path):
    """Regenerating a reviewed section archives the previous version (never silently overwrites)."""
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        user_id = _register_and_login(client, "regen-user", "correct-horse-123")
        resource_id, section_id = _seed_resource_and_section(
            settings.database_path,
            user_id,
            "Original content [E1].",
            "Photosynthesis converts light energy into chemical energy.",
        )

        # Review the section.
        reviewed = client.patch(
            f"/api/v1/content/resources/{resource_id}/sections/{section_id}/verify",
            json={"verificationStatus": "verified"},
        )
        assert reviewed.status_code == 200

        # Regenerate with new content.
        regenerated = client.patch(
            f"/api/v1/content/resources/{resource_id}/sections/{section_id}/regenerate",
            json={
                "content": "Regenerated content [E1].",
                "citations": [
                    {
                        "citationId": "chk_valid",
                        "documentId": "doc_valid",
                        "displayFilename": "study.pdf",
                        "pageNumber": 1,
                        "chunkIndex": 0,
                        "charStart": 0,
                        "charEnd": 57,
                        "text": "Photosynthesis converts light energy into chemical energy.",
                        "retrieval": {"method": "fts5_bm25", "rank": 1},
                    }
                ],
            },
        )
        assert regenerated.status_code == 200, regenerated.text
        assert regenerated.json()["content"] == "Regenerated content [E1]."
        assert regenerated.json()["version"] == 2

        # Revisions preserve the previous (reviewed) version.
        revs = list_content_revisions(
            settings.database_path, resource_id=resource_id, section_id=section_id, user_id=user_id
        )
        assert revs is not None
        assert len(revs) == 1  # v1 archived
        assert revs[0]["content"] == "Original content [E1]."