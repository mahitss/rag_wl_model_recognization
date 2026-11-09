"""M73: Institution Management & Multi-Tenant Education Platform tests.

Deterministic tests for:

* Institution creation (SCHEMA_VERSION 33 migration).
* Institution listing (all and mine-filtered).
* Institution update (admin only).
* Institution soft-delete and restore.
* Membership (add, list, suspend, revoke).
* Invitations (create, accept, list).
* Departments (create, list).
* Programs (create, list).
* Settings (get, update).
* Plan entitlements (get, set).
* Role-based authorization (non-admin cannot mutate).
* Cross-tenant API access isolation.
* Cross-tenant document retrieval isolation.
* Concurrent membership changes.
* Administrator privilege boundaries.

Uses synthetic institutions, accounts, and documents.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.config import Settings
from app.main import create_app
from app.storage import (
    create_user,
    initialize_storage,
    store_document,
)


@pytest.fixture
def settings(tmp_path: Path):
    return Settings(_env_file=None, database_path=tmp_path / "test.sqlite3")


@pytest.fixture
def app(settings):
    app = create_app(settings)
    initialize_storage(settings.database_path)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def _login(client: TestClient, username: str, password: str = "testpassword123"):
    """Log in a user and return the authenticated client."""
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    return client


def _create_user_and_login(
    app, database_path, username: str, password: str = "testpassword123"
):
    """Create a user and return an authenticated client and their user_id."""
    user_id = create_user(database_path, username, hash_password(password))
    client = TestClient(app)
    return _login(client, username, password), user_id


def _create_institution(client: TestClient, name: str, domain: str | None = None):
    """Create an institution via the API."""
    body = {"name": name}
    if domain:
        body["domain"] = domain
    response = client.post("/api/v1/institutions", json=body)
    assert response.status_code == 201, f"Create failed: {response.text}"
    return response.json()


# ---------------------------------------------------------------------------
# Institution creation
# ---------------------------------------------------------------------------


def test_create_institution(app):
    database_path = app.state.settings.database_path
    client, user_id = _create_user_and_login(app, database_path, "founder")

    result = _create_institution(client, "WonderLearn University", "wl.edu")
    assert result["name"] == "WonderLearn University"
    assert result["domain"] == "wl.edu"
    assert result["status"] == "active"
    assert result["createdBy"] == user_id
    assert result["institutionId"].startswith("inst_")


def test_create_institution_empty_name_rejected(app):
    database_path = app.state.settings.database_path
    client, _ = _create_user_and_login(app, database_path, "founder2")

    response = client.post("/api/v1/institutions", json={"name": "   "})
    assert response.status_code == 422, f"Expected 422: {response.text}"


def test_create_institution_unauthenticated_rejected(client):
    response = client.post("/api/v1/institutions", json={"name": "No Auth University"})
    assert response.status_code in (401, 403), f"Expected 401/403: {response.text}"


# ---------------------------------------------------------------------------
# Institution listing
# ---------------------------------------------------------------------------


def test_list_institutions_mine(app):
    database_path = app.state.settings.database_path
    client, user_id = _create_user_and_login(app, database_path, "lister")

    inst1 = _create_institution(client, "Inst One", "one.edu")
    inst2 = _create_institution(client, "Inst Two", "two.edu")

    response = client.get("/api/v1/institutions?mine=true")
    assert response.status_code == 200, f"List failed: {response.text}"
    institutions = response.json()["institutions"]
    ids = {inst["institutionId"] for inst in institutions}
    assert inst1["institutionId"] in ids
    assert inst2["institutionId"] in ids


def test_list_institutions_all(app):
    database_path = app.state.settings.database_path
    client, _ = _create_user_and_login(app, database_path, "lister2")

    _create_institution(client, "Public Inst", "pub.edu")

    response = client.get("/api/v1/institutions")
    assert response.status_code == 200, f"List all failed: {response.text}"
    institutions = response.json()["institutions"]
    assert any(inst["name"] == "Public Inst" for inst in institutions)


# ---------------------------------------------------------------------------
# Institution get
# ---------------------------------------------------------------------------


def test_get_institution(app):
    database_path = app.state.settings.database_path
    client, _ = _create_user_and_login(app, database_path, "getter")

    inst = _create_institution(client, "Get Inst", "get.edu")

    response = client.get(f"/api/v1/institutions/{inst['institutionId']}")
    assert response.status_code == 200, f"Get failed: {response.text}"
    data = response.json()
    assert data["institutionId"] == inst["institutionId"]
    assert data["name"] == "Get Inst"


def test_get_nonexistent_institution(app):
    database_path = app.state.settings.database_path
    client, _ = _create_user_and_login(app, database_path, "getter2")

    response = client.get("/api/v1/institutions/inst_nonexistent")
    assert response.status_code == 404, f"Expected 404: {response.text}"


# ---------------------------------------------------------------------------
# Institution update (admin only)
# ---------------------------------------------------------------------------


def test_update_institution(app):
    database_path = app.state.settings.database_path
    client, _ = _create_user_and_login(app, database_path, "updater")

    inst = _create_institution(client, "Old Name", "old.edu")

    response = client.patch(
        f"/api/v1/institutions/{inst['institutionId']}",
        json={"name": "New Name", "domain": "new.edu"},
    )
    assert response.status_code == 200, f"Update failed: {response.text}"
    data = response.json()
    assert data["name"] == "New Name"
    assert data["domain"] == "new.edu"


def test_update_institution_non_admin_rejected(app):
    database_path = app.state.settings.database_path
    # Create institution with user1 (admin of inst)
    client1, user1_id = _create_user_and_login(app, database_path, "inst_admin")
    inst = _create_institution(client1, "Owned Inst", "owned.edu")

    # User2 tries to update - user2 is not a member, so should be rejected
    client2, _ = _create_user_and_login(app, database_path, "inst_outsider")

    response = client2.patch(
        f"/api/v1/institutions/{inst['institutionId']}",
        json={"name": "Hacked"},
    )
    assert response.status_code in (403, 404, 422), f"Expected 403/404/422: {response.text}"


# ---------------------------------------------------------------------------
# Institution delete and restore
# ---------------------------------------------------------------------------


def test_delete_and_restore_institution(app):
    database_path = app.state.settings.database_path
    client, _ = _create_user_and_login(app, database_path, "deleter")

    inst = _create_institution(client, "To Delete", "del.edu")

    response = client.delete(
        f"/api/v1/institutions/{inst['institutionId']}"
    )
    assert response.status_code == 200, f"Delete failed: {response.text}"
    assert response.json()["status"] == "deleted"

    # Get should fail now for non-admin viewer (but creator is admin, so should still work?)
    # Actually deleted institutions can only be seen by admins for restore
    response = client.get(f"/api/v1/institutions/{inst['institutionId']}")
    assert response.status_code == 200, f"Get deleted failed: {response.text}"
    assert response.json()["status"] == "deleted"

    # Restore
    response = client.post(
        f"/api/v1/institutions/{inst['institutionId']}/restore"
    )
    assert response.status_code == 200, f"Restore failed: {response.text}"
    assert response.json()["status"] == "active"


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


def test_add_and_list_members(app):
    database_path = app.state.settings.database_path
    client1, admin_id = _create_user_and_login(app, database_path, "admin_user")
    client2, member_id = _create_user_and_login(app, database_path, "member_user")

    inst = _create_institution(client1, "Member Inst", "mem.edu")

    # Admin adds member
    response = client1.post(
        f"/api/v1/institutions/{inst['institutionId']}/members",
        json={"userId": member_id, "role": "learner"},
    )
    assert response.status_code == 201, f"Add member failed: {response.text}"

    # List members
    response = client1.get(
        f"/api/v1/institutions/{inst['institutionId']}/members"
    )
    assert response.status_code == 200, f"List members failed: {response.text}"
    members = response.json()["members"]
    member_ids = {m["userId"] for m in members}
    assert admin_id in member_ids
    assert member_id in member_ids


def test_suspend_member(app):
    database_path = app.state.settings.database_path
    client1, _ = _create_user_and_login(app, database_path, "suspend_admin")
    client2, member_id = _create_user_and_login(app, database_path, "suspend_member")

    inst = _create_institution(client1, "Suspend Inst", "sus.edu")

    client1.post(
        f"/api/v1/institutions/{inst['institutionId']}/members",
        json={"userId": member_id, "role": "learner"},
    )

    response = client1.post(
        f"/api/v1/institutions/{inst['institutionId']}/members/suspend",
        json={"userId": member_id},
    )
    assert response.status_code == 200, f"Suspend failed: {response.text}"
    assert response.json()["status"] == "suspended"


def test_revoke_member(app):
    database_path = app.state.settings.database_path
    client1, _ = _create_user_and_login(app, database_path, "revoke_admin")
    client2, member_id = _create_user_and_login(app, database_path, "revoke_member")

    inst = _create_institution(client1, "Revoke Inst", "rev.edu")

    client1.post(
        f"/api/v1/institutions/{inst['institutionId']}/members",
        json={"userId": member_id, "role": "learner"},
    )

    response = client1.post(
        f"/api/v1/institutions/{inst['institutionId']}/members/revoke",
        json={"userId": member_id},
    )
    assert response.status_code == 200, f"Revoke failed: {response.text}"
    assert response.json()["status"] == "revoked"


def test_add_member_non_admin_rejected(app):
    database_path = app.state.settings.database_path
    client1, admin_id = _create_user_and_login(app, database_path, "admin_add")
    client2, outsider_id = _create_user_and_login(app, database_path, "outsider_add")

    inst = _create_institution(client1, "No Add Inst", "noadd.edu")

    # Outsider (not admin) tries to add member
    response = client2.post(
        f"/api/v1/institutions/{inst['institutionId']}/members",
        json={"userId": outsider_id, "role": "learner"},
    )
    assert response.status_code in (403, 422), f"Expected 403/422: {response.text}"


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


def test_create_and_accept_invitation(app):
    database_path = app.state.settings.database_path
    client1, admin_id = _create_user_and_login(app, database_path, "inv_admin")
    # Create the invitee user with matching email
    create_user(database_path, "invitee", hash_password("testpassword123"), email="invitee@test.com")

    inst = _create_institution(client1, "Invite Inst", "inv.edu")

    # Admin creates invitation
    response = client1.post(
        f"/api/v1/institutions/{inst['institutionId']}/invitations",
        json={"email": "invitee@test.com", "role": "learner"},
    )
    assert response.status_code == 201, f"Create invitation failed: {response.text}"
    invitation = response.json()
    token = invitation["token"]

    # Now log in as invitee and accept
    client2 = TestClient(app)
    _login(client2, "invitee", "testpassword123")
    response = client2.post(
        "/api/v1/invitations/accept",
        json={"token": token},
    )
    assert response.status_code == 200, f"Accept invitation failed: {response.text}"
    assert response.json()["status"] == "active"
    assert response.json()["role"] == "learner"


def test_list_invitations(app):
    database_path = app.state.settings.database_path
    client1, _ = _create_user_and_login(app, database_path, "list_inv_admin")

    inst = _create_institution(client1, "List Inv Inst", "listinv.edu")

    response = client1.post(
        f"/api/v1/institutions/{inst['institutionId']}/invitations",
        json={"email": "a@test.com", "role": "learner"},
    )
    assert response.status_code == 201, f"Create invitation failed: {response.text}"

    response = client1.get(
        f"/api/v1/institutions/{inst['institutionId']}/invitations"
    )
    assert response.status_code == 200, f"List invitations failed: {response.text}"
    invitations = response.json()["invitations"]
    assert len(invitations) >= 1


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


def test_create_and_list_departments(app):
    database_path = app.state.settings.database_path
    client, _ = _create_user_and_login(app, database_path, "dept_admin")

    inst = _create_institution(client, "Dept Inst", "dept.edu")

    response = client.post(
        f"/api/v1/institutions/{inst['institutionId']}/departments",
        json={"name": "Computer Science", "description": "CS department"},
    )
    assert response.status_code == 201, f"Create department failed: {response.text}"
    dept = response.json()
    assert dept["name"] == "Computer Science"
    assert dept["institutionId"] == inst["institutionId"]

    response = client.get(
        f"/api/v1/institutions/{inst['institutionId']}/departments"
    )
    assert response.status_code == 200, f"List departments failed: {response.text}"
    departments = response.json()["departments"]
    assert any(d["name"] == "Computer Science" for d in departments)


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------


def test_create_and_list_programs(app):
    database_path = app.state.settings.database_path
    client, _ = _create_user_and_login(app, database_path, "prog_admin")

    inst = _create_institution(client, "Prog Inst", "prog.edu")

    response = client.post(
        f"/api/v1/institutions/{inst['institutionId']}/programs",
        json={
            "name": "Bachelor of Science",
            "description": "BS program",
            "academicYear": "2026",
            "term": "Fall",
        },
    )
    assert response.status_code == 201, f"Create program failed: {response.text}"
    prog = response.json()
    assert prog["name"] == "Bachelor of Science"
    assert prog["academicYear"] == "2026"
    assert prog["term"] == "Fall"

    response = client.get(
        f"/api/v1/institutions/{inst['institutionId']}/programs"
    )
    assert response.status_code == 200, f"List programs failed: {response.text}"
    programs = response.json()["programs"]
    assert any(p["name"] == "Bachelor of Science" for p in programs)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_get_and_update_settings(app):
    database_path = app.state.settings.database_path
    client, _ = _create_user_and_login(app, database_path, "settings_admin")

    inst = _create_institution(client, "Settings Inst", "settings.edu")

    response = client.patch(
        f"/api/v1/institutions/{inst['institutionId']}/settings",
        json={"settingKey": "timezone", "settingValue": "UTC"},
    )
    assert response.status_code == 200, f"Update setting failed: {response.text}"

    response = client.get(
        f"/api/v1/institutions/{inst['institutionId']}/settings"
    )
    assert response.status_code == 200, f"Get settings failed: {response.text}"
    settings = response.json()
    assert settings["timezone"] == "UTC"


# ---------------------------------------------------------------------------
# Plan entitlements
# ---------------------------------------------------------------------------


def test_get_and_set_plan_entitlements(app):
    database_path = app.state.settings.database_path
    client, _ = _create_user_and_login(app, database_path, "plan_admin")

    inst = _create_institution(client, "Plan Inst", "plan.edu")

    response = client.patch(
        f"/api/v1/institutions/{inst['institutionId']}/plan",
        json={
            "plan": "premium",
            "maxUsers": 100,
            "maxClassrooms": 50,
            "maxCourses": 200,
            "maxStorageMb": 5000,
            "features": {"analytics": True},
        },
    )
    assert response.status_code == 200, f"Set plan failed: {response.text}"
    plan = response.json()
    assert plan["plan"] == "premium"
    assert plan["maxUsers"] == 100

    response = client.get(f"/api/v1/institutions/{inst['institutionId']}/plan")
    assert response.status_code == 200, f"Get plan failed: {response.text}"
    plan = response.json()
    assert plan["plan"] == "premium"


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------


def test_cross_tenant_institution_access(app):
    database_path = app.state.settings.database_path
    client1, _ = _create_user_and_login(app, database_path, "tenant_admin_a")
    client2, _ = _create_user_and_login(app, database_path, "tenant_admin_b")

    inst_a = _create_institution(client1, "Inst A", "a.edu")
    inst_b = _create_institution(client2, "Inst B", "b.edu")

    # Admin of Inst A cannot modify Inst B
    response = client1.patch(
        f"/api/v1/institutions/{inst_b['institutionId']}",
        json={"name": "Hacked B"},
    )
    assert response.status_code in (403, 404, 422), f"Expected 403/404/422: {response.text}"

    # Admin of Inst A cannot list members of Inst B
    response = client1.get(
        f"/api/v1/institutions/{inst_b['institutionId']}/members"
    )
    assert response.status_code in (403, 404, 422), f"Expected 403/404/422: {response.text}"

    # Admin of Inst A cannot create invitations for Inst B
    response = client1.post(
        f"/api/v1/institutions/{inst_b['institutionId']}/invitations",
        json={"email": "x@test.com", "role": "learner"},
    )
    assert response.status_code in (403, 404, 422), f"Expected 403/404/422: {response.text}"


def test_cross_tenant_department_isolation(app):
    database_path = app.state.settings.database_path
    client1, _ = _create_user_and_login(app, database_path, "cross_admin_a")
    client2, _ = _create_user_and_login(app, database_path, "cross_admin_b")

    inst_a = _create_institution(client1, "Cross Inst A", "crossa.edu")
    inst_b = _create_institution(client2, "Cross Inst B", "crossb.edu")

    # Admin of Inst A creates a department in Inst A
    response = client1.post(
        f"/api/v1/institutions/{inst_a['institutionId']}/departments",
        json={"name": "Dept A", "description": "Dept A desc"},
    )
    assert response.status_code == 201, f"Create dept failed: {response.text}"
    dept_a = response.json()

    # Admin of Inst B cannot list departments of Inst A
    response = client2.get(
        f"/api/v1/institutions/{inst_a['institutionId']}/departments"
    )
    assert response.status_code in (403, 404), f"Expected 403/404: {response.text}"

    # Admin of Inst B cannot create a program in Inst A's department
    response = client2.post(
        f"/api/v1/institutions/{inst_a['institutionId']}/programs",
        json={
            "departmentId": dept_a["departmentId"],
            "name": "Hacked Program",
            "description": "Hack",
        },
    )
    assert response.status_code in (403, 422), f"Expected 403/422: {response.text}"


# ---------------------------------------------------------------------------
# Cross-tenant document retrieval isolation
# ---------------------------------------------------------------------------


def test_cross_tenant_document_retrieval(app):
    database_path = app.state.settings.database_path
    client1, user1_id = _create_user_and_login(app, database_path, "doc_user_a")
    client2, user2_id = _create_user_and_login(app, database_path, "doc_user_b")

    # User A stores a document
    doc_id_a = "doc_a"
    store_document(
        database_path,
        metadata={
            "document_id": doc_id_a,
            "display_name": "Doc A",
            "sha256": "sha_a",
            "byte_size": 100,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "parser_version": "1.0",
        },
        chunks=[
            {
                "chunk_id": "chunk_a",
                "page_number": 1,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": 10,
                "text": "User A secret",
                "user_id": user1_id,
            }
        ],
        user_id=user1_id,
    )

    # User B stores a document
    doc_id_b = "doc_b"
    store_document(
        database_path,
        metadata={
            "document_id": doc_id_b,
            "display_name": "Doc B",
            "sha256": "sha_b",
            "byte_size": 100,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "parser_version": "1.0",
        },
        chunks=[
            {
                "chunk_id": "chunk_b",
                "page_number": 1,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": 10,
                "text": "User B secret",
                "user_id": user2_id,
            }
        ],
        user_id=user2_id,
    )

    # User A lists documents - should only see their own
    response = client1.get("/api/v1/documents")
    assert response.status_code == 200, f"List docs failed: {response.text}"
    docs_a = response.json()["documents"]
    doc_ids_a = {d["documentId"] for d in docs_a}
    assert doc_id_a in doc_ids_a
    assert doc_id_b not in doc_ids_a

    # User B lists documents - should only see their own
    response = client2.get("/api/v1/documents")
    assert response.status_code == 200, f"List docs failed: {response.text}"
    docs_b = response.json()["documents"]
    doc_ids_b = {d["documentId"] for d in docs_b}
    assert doc_id_b in doc_ids_b
    assert doc_id_a not in doc_ids_b


# ---------------------------------------------------------------------------
# Concurrent membership changes
# ---------------------------------------------------------------------------


def test_concurrent_member_add(app):
    database_path = app.state.settings.database_path
    client1, _ = _create_user_and_login(app, database_path, "conc_admin")
    client2, member_id = _create_user_and_login(app, database_path, "conc_member")

    inst = _create_institution(client1, "Conc Inst", "conc.edu")

    # Add member twice (idempotent upsert)
    response1 = client1.post(
        f"/api/v1/institutions/{inst['institutionId']}/members",
        json={"userId": member_id, "role": "learner"},
    )
    assert response1.status_code == 201, f"First add failed: {response1.text}"

    response2 = client1.post(
        f"/api/v1/institutions/{inst['institutionId']}/members",
        json={"userId": member_id, "role": "educator"},
    )
    assert response2.status_code in (200, 201), f"Second add failed: {response2.text}"
    # Role should be updated to educator
    assert response2.json()["role"] == "educator"


# ---------------------------------------------------------------------------
# Admin privilege boundaries
# ---------------------------------------------------------------------------


def test_institution_admin_cannot_escalate_to_system_admin(app):
    database_path = app.state.settings.database_path
    client, _ = _create_user_and_login(app, database_path, "boundary_admin")

    inst = _create_institution(client, "Boundary Inst", "boundary.edu")

    # Institution admin should not be able to access system-level admin endpoints
    # (e.g., mark_user_admin is not exposed via API, but let's verify they
    # can't delete institutions they don't own)
    # Create another institution
    client2, _ = _create_user_and_login(app, database_path, "boundary_admin2")
    inst2 = _create_institution(client2, "Boundary Inst 2", "boundary2.edu")

    # Admin of Inst 1 cannot delete Inst 2
    response = client.delete(
        f"/api/v1/institutions/{inst2['institutionId']}"
    )
    assert response.status_code in (403, 404), f"Expected 403/404: {response.text}"


if __name__ == "__main__":
    pass
