"""M80: Educational Standards & Learning Interoperability tests.

Covers:
* xAPI statement store, list, validation
* SCORM 1.2 package validation, import, runtime tracking
* LTI 1.3 launch shape validation and audit recording
* Standards matrix endpoint
* Security: ZIP traversal, oversized archives, XML DOCTYPE bombing
* Multi-tenant isolation
* Authorization (unauthenticated requests rejected)
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.config import Settings
from app.interoperability import (
    LTI_VERSION,
    SCORM12_VERSION,
    XAPI_VERSION,
    _parse_imsmanifest,
    detect_standard,
    import_scorm12_package,
    validate_lti13_launch_shape,
    validate_scorm12_package,
    validate_xapi_statement,
)
from app.main import create_app
from app.storage import (
    create_user,
    initialize_storage,
    list_interoperability_audit,
    list_lti_launches,
    list_scorm_packages,
    list_scorm_sessions,
    list_xapi_statements,
    store_interoperability_audit,
    store_lti_launch,
    store_scorm_package,
    store_scorm_session,
    store_xapi_statement,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path):
    return Settings(_env_file=None, database_path=tmp_path / f"test_{tmp_path.name}.sqlite3")


@pytest.fixture
def app(settings):
    app = create_app(settings)
    initialize_storage(settings.database_path)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth_client(app, tmp_path):
    database_path = app.state.settings.database_path
    create_user(database_path, "interop_user", hash_password("testpassword123"))
    client = TestClient(app)
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    client.post(
        "/api/v1/auth/login",
        json={"username": "interop_user", "password": "testpassword123"},
    )
    return client, database_path


def _make_zip_archive(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_scorm12_manifest() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="SCORM12_Course" version="1.0"
    xmlns="http://www.imsglobal.org/xsd/imscp_rootv1p1p2"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:schemaLocation="http://www.imsglobal.org/xsd/imscp_rootv1p1p2 imscp_rootv1p1p2.xsd">
  <metadata>
    <schema>ADL SCORM</schema>
    <schemaversion>1.2</schemaversion>
  </metadata>
  <organizations default="ORG_1">
    <organization identifier="ORG_1">
      <title>Sample SCORM Course</title>
      <item identifier="ITEM_1" identifierref="RES_1" isvisible="true">
        <title>Introduction</title>
      </item>
    </organization>
  </organizations>
  <resources>
    <resource identifier="RES_1" type="webcontent" href="index.html">
      <file href="index.html"/>
    </resource>
  </resources>
</manifest>"""


# ---------------------------------------------------------------------------
# xAPI tests
# ---------------------------------------------------------------------------


def test_xapi_store_and_list(app):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "xapi_user", hash_password("pw"))

    stored = store_xapi_statement(
        database_path,
        user_id=user_id,
        actor={"objectType": "Agent", "mbox": "mailto:learner@example.com"},
        verb={
            "id": "http://adlnet.gov/expapi/verbs/experienced",
            "display": {"en-US": "experienced"},
        },
        object={"objectType": "Activity", "id": "http://example.com/activity/1"},
        result={"completion": True, "success": True},
        context={"registration": "reg-123"},
        timestamp="2026-01-01T00:00:00Z",
    )
    assert stored["statementId"] is not None
    assert stored["userId"] == user_id

    statements = list_xapi_statements(database_path, user_id=user_id)
    assert len(statements) == 1
    assert statements[0]["statementId"] == stored["statementId"]
    assert statements[0]["actor"]["mbox"] == "mailto:learner@example.com"
    assert statements[0]["verb"]["id"].endswith("/experienced")


def test_xapi_validation_rejects_missing_fields(app):
    settings_obj = Settings(_env_file=None, database_path=app.state.settings.database_path)
    result = validate_xapi_statement(
        {"actor": {}, "verb": {}, "object": {}}, settings_obj
    )
    assert result.valid is False
    assert len(result.errors) > 0


def test_xapi_validation_accepts_valid(app):
    settings_obj = Settings(_env_file=None, database_path=app.state.settings.database_path)
    result = validate_xapi_statement(
        {
            "actor": {"objectType": "Agent", "mbox": "mailto:a@b.com"},
            "verb": {
                "id": "http://adlnet.gov/expapi/verbs/completed",
                "display": {"en-US": "completed"},
            },
            "object": {"objectType": "Activity", "id": "http://example.com/act"},
        },
        settings_obj,
    )
    assert result.valid is True


def test_xapi_api_endpoints(auth_client):
    client, _ = auth_client

    # The API endpoint uses current_user (the logged-in "interop_user").
    payload = {
        "actor": {"objectType": "Agent", "mbox": "mailto:api@example.com"},
        "verb": {
            "id": "http://adlnet.gov/expapi/verbs/completed",
            "display": {"en-US": "completed"},
        },
        "object": {"objectType": "Activity", "id": "http://example.com/api-act"},
    }
    response = client.post("/api/v1/interoperability/xapi/statements", json=payload)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["statementId"] is not None
    assert data["actor"]["mbox"] == "mailto:api@example.com"

    response = client.get("/api/v1/interoperability/xapi/statements")
    assert response.status_code == 200
    data = response.json()
    assert len(data["statements"]) >= 1


def test_xapi_api_unauthenticated(client):
    # GET has no body so CSRF doesn't apply; require_auth returns 401.
    response = client.get("/api/v1/interoperability/xapi/statements")
    assert response.status_code == 401


def test_xapi_api_invalid_statement(auth_client):
    client, _ = auth_client
    # Passes Pydantic shape but fails business validation (verb.id empty).
    payload = {
        "actor": {"objectType": "Agent", "mbox": "mailto:a@b.com"},
        "verb": {"id": "", "display": {"en-US": "completed"}},
        "object": {"objectType": "Activity", "id": "http://example.com/act"},
    }
    response = client.post("/api/v1/interoperability/xapi/statements", json=payload)
    assert response.status_code == 400, response.text


# ---------------------------------------------------------------------------
# SCORM tests
# ---------------------------------------------------------------------------


def test_scorm12_validation_valid(app):
    settings_obj = Settings(_env_file=None, database_path=app.state.settings.database_path)
    manifest = _make_scorm12_manifest()
    archive = _make_zip_archive({"imsmanifest.xml": manifest.encode()})
    result = validate_scorm12_package(archive, settings_obj)
    assert result.valid is True, result.errors
    assert result.details["archiveSha256"] is not None


def test_scorm12_validation_no_manifest(app):
    settings_obj = Settings(_env_file=None, database_path=app.state.settings.database_path)
    archive = _make_zip_archive({"readme.txt": b"hello"})
    result = validate_scorm12_package(archive, settings_obj)
    assert result.valid is False
    assert any("imsmanifest.xml" in e for e in result.errors)


def test_scorm12_validation_doctypes_rejected(app):
    settings_obj = Settings(_env_file=None, database_path=app.state.settings.database_path)
    bad_xml = b"""<?xml version="1.0"?>
<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
<manifest identifier="x" version="1.0">
  <metadata><schema>ADL SCORM</schema><schemaversion>1.2</schemaversion></metadata>
  <organizations></organizations>
  <resources></resources>
</manifest>"""
    archive = _make_zip_archive({"imsmanifest.xml": bad_xml})
    result = validate_scorm12_package(archive, settings_obj)
    assert result.valid is False
    assert any("DOCTYPE" in e for e in result.errors)


def test_scorm12_import(app):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "scorm_user", hash_password("pw"))
    manifest = _make_scorm12_manifest()
    archive = _make_zip_archive(
        {"imsmanifest.xml": manifest.encode(), "index.html": b"<html></html>"}
    )

    result = import_scorm12_package(
        database_path,
        user_id=user_id,
        display_name="My SCORM Course",
        archive_bytes=archive,
        settings=Settings(_env_file=None, database_path=database_path),
    )
    assert result.status == "imported", result.errors
    assert result.packageId is not None

    packages = list_scorm_packages(database_path, user_id=user_id)
    assert len(packages) == 1
    assert packages[0]["packageId"] == result.packageId
    assert packages[0]["displayName"] == "My SCORM Course"
    assert packages[0]["sourceType"] == "scorm12"


def test_scorm12_import_invalid(app):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "scorm_bad_user", hash_password("pw"))
    archive = _make_zip_archive({"readme.txt": b"no manifest"})

    result = import_scorm12_package(
        database_path,
        user_id=user_id,
        display_name="Bad",
        archive_bytes=archive,
        settings=Settings(_env_file=None, database_path=database_path),
    )
    assert result.status == "failed"
    assert len(result.errors) > 0


def test_scorm_session_record_and_list(app):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "scormsess_user", hash_password("pw"))
    package = store_scorm_package(
        database_path,
        user_id=user_id,
        display_name="Pkg",
        source_type="scorm12",
        manifest_sha256="a" * 64,
        archive_sha256="b" * 64,
        manifest={"identifier": "x"},
    )

    stored = store_scorm_session(
        database_path,
        package_id=package["packageId"],
        user_id=user_id,
        learner_id="learner_1",
        lesson_status="completed",
        score_raw=85.0,
        score_min=0.0,
        score_max=100.0,
        total_time_ms=120000,
        lesson_location="sco/1",
        suspend_data={"key": "value"},
    )
    assert stored["sessionId"] is not None
    assert stored["lessonStatus"] == "completed"
    assert stored["scoreRaw"] == 85.0

    sessions = list_scorm_sessions(database_path, user_id=user_id)
    assert len(sessions) == 1
    assert sessions[0]["sessionId"] == stored["sessionId"]
    assert sessions[0]["suspendData"]["key"] == "value"


# ---------------------------------------------------------------------------
# LTI tests
# ---------------------------------------------------------------------------


def test_lti13_shape_validation_valid(app):
    params = {
        "lti_message_type": "LtiResourceLinkRequest",
        "lti_version": "1.3.0",
        "resource_link_id": "rl-123",
        "deployment_id": "dep-123",
        "target_link_uri": "https://example.com/launch",
        "roles": ["Learner"],
    }
    result = validate_lti13_launch_shape(params)
    assert result.valid is True, result.errors


def test_lti13_shape_validation_missing_fields(app):
    result = validate_lti13_launch_shape({})
    assert result.valid is False
    assert any("resource_link_id" in e for e in result.errors)
    assert any("deployment_id" in e for e in result.errors)
    assert any("target_link_uri" in e for e in result.errors)


def test_lti13_launch_record(app):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "lti_user", hash_password("pw"))
    tenant_id = f"tenant_{__import__('hashlib').sha256(user_id.encode()).hexdigest()[:24]}"

    stored = store_lti_launch(
        database_path,
        user_id=user_id,
        tenant_id=tenant_id,
        tool_consumer_instance_guid="tc.example.com",
        lti_message_type="LtiResourceLinkRequest",
        lti_version="1.3.0",
        resource_link_id="rl-123",
        user_id_lms="lms_user_1",
        roles=["Learner"],
        audit={"note": "test"},
    )
    assert stored["launchId"] is not None

    launches = list_lti_launches(database_path, user_id=user_id)
    assert len(launches) == 1
    assert launches[0]["launchId"] == stored["launchId"]


def test_lti13_api_endpoint(auth_client):
    client, _ = auth_client
    payload = {
        "lti_message_type": "LtiResourceLinkRequest",
        "lti_version": "1.3.0",
        "resource_link_id": "rl-api",
        "deployment_id": "dep-api",
        "target_link_uri": "https://example.com/launch",
        "roles": ["Learner"],
    }
    response = client.post("/api/v1/interoperability/lti/launch", json=payload)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["launchId"] is not None

    response = client.get("/api/v1/interoperability/lti/launches")
    assert response.status_code == 200
    assert len(response.json()) >= 1


# ---------------------------------------------------------------------------
# Standards matrix
# ---------------------------------------------------------------------------


def test_standards_matrix(auth_client):
    client, _ = auth_client
    response = client.get("/api/v1/interoperability/standards")
    assert response.status_code == 200
    data = response.json()
    assert "standards" in data
    names = {s["standard"] for s in data["standards"]}
    assert "xAPI (Experience API)" in names
    assert "SCORM 1.2" in names
    assert "SCORM 2004" in names
    assert "LTI 1.3" in names

    scorm2004 = next(s for s in data["standards"] if s["standard"] == "SCORM 2004")
    assert scorm2004["supported"] is False
    lti = next(s for s in data["standards"] if s["standard"] == "LTI 1.3")
    assert lti["supported"] is False


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_interoperability_audit(app):
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "audit_user", hash_password("pw"))
    tenant_id = f"tenant_{__import__('hashlib').sha256(user_id.encode()).hexdigest()[:24]}"

    stored = store_interoperability_audit(
        database_path,
        user_id=user_id,
        tenant_id=tenant_id,
        operation="import_package",
        standard="SCORM 1.2",
        target_id="pkg-1",
        success=True,
        details={"packageId": "pkg-1"},
    )
    assert stored["auditId"] is not None
    assert stored["success"] is True

    rows = list_interoperability_audit(database_path, user_id=user_id)
    assert len(rows) == 1
    assert rows[0]["auditId"] == stored["auditId"]
    assert rows[0]["standard"] == "SCORM 1.2"


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------


def test_security_zip_traversal_rejected(app):
    settings_obj = Settings(_env_file=None, database_path=app.state.settings.database_path)
    # Build a zip with a traversal member name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.xml", b"<x/>")
        zf.writestr("imsmanifest.xml", _make_scorm12_manifest().encode())
    archive = buf.getvalue()

    result = validate_scorm12_package(archive, settings_obj)
    assert result.valid is False
    assert any("Unsafe archive member" in e for e in result.errors)


def test_security_oversized_archive_rejected(app):
    settings_obj = Settings(
        _env_file=None,
        database_path=app.state.settings.database_path,
        max_interop_upload_bytes=100,
    )
    archive = _make_zip_archive({"imsmanifest.xml": _make_scorm12_manifest().encode()})
    result = validate_scorm12_package(archive, settings_obj)
    assert result.valid is False
    assert any("exceeds" in e.lower() for e in result.errors)


def test_security_xml_depth_rejected(app):
    settings_obj = Settings(
        _env_file=None,
        database_path=app.state.settings.database_path,
        max_interop_xml_depth=2,
    )
    # Deeply nested XML
    xml = "<a>" * 10 + "</a>" * 10
    archive = _make_zip_archive({"imsmanifest.xml": xml.encode()})
    result = validate_scorm12_package(archive, settings_obj)
    assert result.valid is False


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_tenant_isolation(app):
    database_path = app.state.settings.database_path
    user_a = create_user(database_path, "iso_a", hash_password("pw"))
    user_b = create_user(database_path, "iso_b", hash_password("pw"))

    store_xapi_statement(
        database_path,
        user_id=user_a,
        actor={"objectType": "Agent", "mbox": "mailto:a@b.com"},
        verb={"id": "http://adlnet.gov/expapi/verbs/completed", "display": {"en-US": "completed"}},
        object={"objectType": "Activity", "id": "http://example.com/a"},
    )
    store_xapi_statement(
        database_path,
        user_id=user_b,
        actor={"objectType": "Agent", "mbox": "mailto:c@d.com"},
        verb={"id": "http://adlnet.gov/expapi/verbs/completed", "display": {"en-US": "completed"}},
        object={"objectType": "Activity", "id": "http://example.com/b"},
    )

    stmts_a = list_xapi_statements(database_path, user_id=user_a)
    stmts_b = list_xapi_statements(database_path, user_id=user_b)
    assert len(stmts_a) == 1
    assert len(stmts_b) == 1
    assert stmts_a[0]["actor"]["mbox"] == "mailto:a@b.com"
    assert stmts_b[0]["actor"]["mbox"] == "mailto:c@d.com"


# ---------------------------------------------------------------------------
# Detect standard
# ---------------------------------------------------------------------------


def test_detect_standard_scorm12(app):
    manifest, _, _ = _parse_imsmanifest(
        _make_scorm12_manifest().encode(),
        Settings(_env_file=None, database_path=app.state.settings.database_path),
    )
    standard, version, _, _ = detect_standard(manifest)
    assert standard == SCORM12_VERSION


def test_versions_constants():
    assert XAPI_VERSION == "1.0.3"
    assert SCORM12_VERSION == "1.2"
    assert LTI_VERSION == "1.3.0"
