"""Shared study space (workspace) regressions: permissions, sharing, isolation, deletion."""

from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.storage import initialize_storage


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
        database_path=str(tmp_path) + "/workspaces.sqlite3",
        rate_limit_enabled=False,
    )


def _make_workspace(client: TestClient, title: str = "Shared biology") -> str:
    response = client.post(
        "/api/v1/workspaces",
        json={"title": title, "description": "A shared study space."},
    )
    assert response.status_code == 201, response.text
    return response.json()["spaceId"]


def test_workspace_crud_and_membership(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        owner_id = _register_and_login(client, "workspace-owner", "correct-horse-123")

        # Create
        created = client.post(
            "/api/v1/workspaces",
            json={"title": "Biology study group", "description": "Shared biology notes."},
        )
        assert created.status_code == 201, created.text
        space = created.json()
        assert space["title"] == "Biology study group"
        assert space["ownerId"] == owner_id
        assert space["userRole"] == "owner"
        assert space["memberCount"] == 1
        space_id = space["spaceId"]

        # List
        listed = client.get("/api/v1/workspaces")
        assert listed.status_code == 200
        assert len(listed.json()["spaces"]) == 1

        # Get as member
        got = client.get(f"/api/v1/workspaces/{space_id}")
        assert got.status_code == 200
        assert got.json()["spaceId"] == space_id

        # Update (owner only)
        updated = client.patch(
            f"/api/v1/workspaces/{space_id}",
            json={"title": "Biology advanced", "status": "archived"},
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "Biology advanced"
        assert updated.json()["status"] == "archived"

        # List active only excludes archived
        active = client.get("/api/v1/workspaces?status=active")
        assert active.status_code == 200
        assert len(active.json()["spaces"]) == 0

        archived = client.get("/api/v1/workspaces?status=archived")
        assert archived.status_code == 200
        assert len(archived.json()["spaces"]) == 1


def test_workspace_cross_user_isolation(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client_a:
        _register_and_login(client_a, "user-a", "password-a")
        space_id = _make_workspace(client_a, "User A space")

        # User B cannot see or touch User A's workspace
        client_b = TestClient(create_app(settings))
        _register_and_login(client_b, "user-b", "password-b")

        got = client_b.get(f"/api/v1/workspaces/{space_id}")
        assert got.status_code == 404

        listed = client_b.get("/api/v1/workspaces")
        assert len(listed.json()["spaces"]) == 0

        members = client_b.get(f"/api/v1/workspaces/{space_id}/members")
        assert members.status_code == 403

        invite = client_b.post(
            f"/api/v1/workspaces/{space_id}/members",
            json={"username": "user-a"},
        )
        assert invite.status_code == 403

        # User A still owns it
        got = client_a.get(f"/api/v1/workspaces/{space_id}")
        assert got.status_code == 200


def test_workspace_invite_and_remove_member(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client_a:
        owner_id = _register_and_login(client_a, "owner-a", "password-a")
        space_id = _make_workspace(client_a)

        # Invite user B
        client_b = TestClient(create_app(settings))
        _register_and_login(client_b, "user-b", "password-b")

        invite = client_a.post(
            f"/api/v1/workspaces/{space_id}/members",
            json={"username": "user-b"},
        )
        assert invite.status_code == 201, invite.text
        assert invite.json()["userId"] != owner_id
        assert invite.json()["role"] == "member"

        # User B can now list and see the workspace
        listed = client_b.get("/api/v1/workspaces")
        assert len(listed.json()["spaces"]) == 1

        # Members list
        members = client_a.get(f"/api/v1/workspaces/{space_id}/members")
        assert members.status_code == 200
        usernames = {m["userId"] for m in members.json()}
        assert owner_id in usernames
        assert invite.json()["userId"] in usernames

        # Owner removes member B
        removed = client_a.delete(
            f"/api/v1/workspaces/{space_id}/members/{invite.json()['userId']}"
        )
        assert removed.status_code == 200
        assert removed.json()["removed"] is True

        # User B can no longer access
        got = client_b.get(f"/api/v1/workspaces/{space_id}")
        assert got.status_code == 404


def test_workspace_owner_cannot_remove_self(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        owner_id = _register_and_login(client, "owner-self", "password-a")
        space_id = _make_workspace(client)

        removed = client.delete(
            f"/api/v1/workspaces/{space_id}/members/{owner_id}"
        )
        assert removed.status_code == 403


def test_workspace_document_share_consent_and_isolation(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    import pypdf
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/F1"): font})
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 12 Tf 72 720 Td (Hello World.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    pdf_content = output.getvalue()

    with TestClient(create_app(settings)) as client_a:
        _register_and_login(client_a, "doc-owner", "password-a")
        space_id = _make_workspace(client_a)

        # User A uploads a document
        upload = client_a.post(
            "/api/v1/documents",
            files={"file": ("lesson.pdf", io.BytesIO(pdf_content), "application/pdf")},
        )
        assert upload.status_code == 201, upload.text
        document_id = upload.json()["documentId"]

        # User A shares it with the workspace
        shared = client_a.post(
            f"/api/v1/workspaces/{space_id}/documents",
            json={"documentId": document_id},
        )
        assert shared.status_code == 201, shared.text

        # User B joins, then can see the shared document
        client_b = TestClient(create_app(settings))
        _register_and_login(client_b, "doc-user-b", "password-b")
        client_a.post(
            f"/api/v1/workspaces/{space_id}/members",
            json={"username": "doc-user-b"},
        )

        docs_b = client_b.get(f"/api/v1/workspaces/{space_id}/documents")
        assert docs_b.status_code == 200
        assert len(docs_b.json()["documents"]) == 1
        assert docs_b.json()["documents"][0]["documentId"] == document_id

        # User B cannot unshare (not the sharer, not owner)
        unshare = client_b.delete(
            f"/api/v1/workspaces/{space_id}/documents/{document_id}"
        )
        assert unshare.status_code == 403

        # User A unshares successfully
        unshare_a = client_a.delete(
            f"/api/v1/workspaces/{space_id}/documents/{document_id}"
        )
        assert unshare_a.status_code == 200
        assert unshare_a.json()["removed"] is True

        docs_b_again = client_b.get(f"/api/v1/workspaces/{space_id}/documents")
        assert len(docs_b_again.json()["documents"]) == 0


def test_workspace_document_share_non_member_rejected(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    import pypdf
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/F1"): font})
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 12 Tf 72 720 Td (Hello World.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    pdf_content = output.getvalue()

    with TestClient(create_app(settings)) as client_a:
        _register_and_login(client_a, "doc-owner-iso", "password-a")
        space_id = _make_workspace(client_a)

        upload = client_a.post(
            "/api/v1/documents",
            files={"file": ("lesson.pdf", io.BytesIO(pdf_content), "application/pdf")},
        )
        assert upload.status_code == 201
        document_id = upload.json()["documentId"]

        # Non-member user B cannot share into the workspace
        client_b = TestClient(create_app(settings))
        _register_and_login(client_b, "doc-user-c", "password-b")

        shared = client_b.post(
            f"/api/v1/workspaces/{space_id}/documents",
            json={"documentId": document_id},
        )
        assert shared.status_code == 403


def test_workspace_delete_cascades_members_and_shares(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client_a:
        _register_and_login(client_a, "del-owner", "password-a")
        space_id = _make_workspace(client_a)

        client_b = TestClient(create_app(settings))
        _register_and_login(client_b, "del-user-b", "password-b")
        client_a.post(
            f"/api/v1/workspaces/{space_id}/members",
            json={"username": "del-user-b"},
        )

        deleted = client_a.delete(f"/api/v1/workspaces/{space_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        # Workspace gone for everyone
        got = client_a.get(f"/api/v1/workspaces/{space_id}")
        assert got.status_code == 404
        got = client_b.get(f"/api/v1/workspaces/{space_id}")
        assert got.status_code == 404

        # Members gone
        members = client_a.get(f"/api/v1/workspaces/{space_id}/members")
        assert members.status_code == 403


def test_workspace_discussions_and_replies(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "disc-user", "password-a")
        space_id = _make_workspace(client)

        created = client.post(
            f"/api/v1/workspaces/{space_id}/discussions",
            json={"content": "What should we study next?"},
        )
        assert created.status_code == 201, created.text
        discussion = created.json()
        discussion_id = discussion["discussionId"]
        assert discussion["content"] == "What should we study next?"

        listed = client.get(f"/api/v1/workspaces/{space_id}/discussions")
        assert listed.status_code == 200
        assert len(listed.json()["discussions"]) == 1

        reply = client.post(
            f"/api/v1/workspaces/{space_id}/discussions/{discussion_id}/replies",
            json={"content": "Photosynthesis next week."},
        )
        assert reply.status_code == 201, reply.text
        assert reply.json()["content"] == "Photosynthesis next week."

        replies = client.get(
            f"/api/v1/workspaces/{space_id}/discussions/{discussion_id}/replies"
        )
        assert replies.status_code == 200
        assert len(replies.json()["replies"]) == 1


def test_workspace_learning_objective(tmp_path):
    settings = _make_settings(tmp_path)
    initialize_storage(settings.database_path)

    with TestClient(create_app(settings)) as client:
        _register_and_login(client, "obj-owner", "password-a")
        space_id = _make_workspace(client)

        updated = client.patch(
            f"/api/v1/workspaces/{space_id}",
            json={"learningObjective": "Master photosynthesis by reading chapter 3."},
        )
        assert updated.status_code == 200
        assert (
            updated.json()["learningObjective"]
            == "Master photosynthesis by reading chapter 3."
        )

        got = client.get(f"/api/v1/workspaces/{space_id}")
        assert got.json()["learningObjective"] == "Master photosynthesis by reading chapter 3."
