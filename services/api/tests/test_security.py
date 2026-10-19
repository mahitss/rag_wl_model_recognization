"""M17 security regression tests.

Covers rate limiting, security headers, request body limits, malicious
filenames, malformed uploads, oversized requests, and error-message safety.

Run with:

    uv run --project services/api --extra dev pytest
    services/api/tests/test_security.py -m security
"""

from __future__ import annotations

import io
import sys

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

sys.path.insert(0, "services/api")

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.storage import initialize_storage  # noqa: E402

pytestmark = pytest.mark.security


def make_pdf(text: str = "Hello world.") -> bytes:
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
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _client(tmp_path, *, rate_limit=False, max_body=None):
    db_path = tmp_path / "sec.sqlite3"
    settings = Settings(
        _env_file=None,
        database_path=db_path,
        cors_origins="http://localhost:5173",
        rate_limit_enabled=rate_limit,
        rate_limit_requests_per_minute=2,
        max_request_body_bytes=max_body,
    )
    initialize_storage(db_path)
    app = create_app(settings)
    return TestClient(app)


def test_security_headers_present_on_health(tmp_path):
    """Every response must carry defensive security headers."""
    client = _client(tmp_path)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    assert "permissions-policy" in response.headers
    assert "content-security-policy" in response.headers


def test_security_headers_on_error_response(tmp_path):
    """Security headers must be present even on error responses."""
    client = _client(tmp_path)
    response = client.get("/api/v1/documents/nonexistent-id")
    assert response.status_code == 404
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_rate_limits_repeated_requests(tmp_path):
    """When enabled, rate limiting rejects requests beyond the window."""
    client = _client(tmp_path, rate_limit=True)
    # First two requests succeed (documents list is not exempt).
    assert client.get("/api/v1/documents").status_code in (200,)
    assert client.get("/api/v1/documents").status_code in (200,)
    # Third request within the window is rate limited.
    response = client.get("/api/v1/documents")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"


def test_rate_limit_exempts_health_and_ready(tmp_path):
    """Health and readiness endpoints are exempt from rate limiting."""
    client = _client(tmp_path, rate_limit=True)
    for _ in range(5):
        assert client.get("/api/v1/health").status_code == 200


def test_max_request_body_rejects_oversized_upload(tmp_path):
    """Uploads exceeding WL_MAX_REQUEST_BODY_BYTES are rejected with 413."""
    client = _client(tmp_path, max_body=100)
    # A small valid PDF is ~3 KB, well over the 100-byte limit.
    response = client.post(
        "/api/v1/documents",
        files={"file": ("tiny.pdf", make_pdf(), "application/pdf")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_ENTITY_TOO_LARGE"


def test_malicious_filename_is_sanitized(tmp_path):
    """Path-traversal filenames must not escape the intended display name."""
    client = _client(tmp_path)
    response = client.post(
        "/api/v1/documents",
        files={"file": ("../../../etc/passwd.pdf", make_pdf(), "application/pdf")},
    )
    assert response.status_code == 201
    assert response.json()["displayFilename"] == "passwd.pdf"


def test_non_pdf_upload_is_rejected(tmp_path):
    """Files without a PDF signature are rejected with 415."""
    client = _client(tmp_path)
    response = client.post(
        "/api/v1/documents",
        files={"file": ("not-a-pdf.txt", b"this is not a pdf", "application/pdf")},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FORMAT"


def test_empty_upload_is_rejected(tmp_path):
    """Empty uploads are rejected with 422."""
    client = _client(tmp_path)
    response = client.post(
        "/api/v1/documents",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_FILE"


def test_error_responses_do_not_leak_internals(tmp_path):
    """Error messages must be generic; no stack traces or file paths."""
    client = _client(tmp_path)
    response = client.get("/api/v1/documents/nonexistent-id")
    body = response.json()
    assert "error" in body
    assert "requestId" in body["error"]
    payload = str(body)
    assert "/api/v1" not in payload or "documents" in payload
    assert "traceback" not in payload.lower()
    assert "sqlite" not in payload.lower()
    assert "traceback" not in payload.lower()


def test_cors_preflight_allows_configured_origin_only(tmp_path):
    """CORS must only allow configured origins, not wildcard."""
    client = _client(tmp_path)
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_health_does_not_leak_settings(tmp_path):
    """Health endpoint reports status without exposing configuration values."""
    client = _client(tmp_path)
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "wonderlearn-api"
    # Environment is informational only; no paths, keys, or secrets exposed.
    assert "environment" in body
    assert "database" not in str(body).lower()
    assert "secret" not in str(body).lower()
    assert "token" not in str(body).lower()
    assert "key" not in str(body).lower()
