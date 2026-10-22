"""Pytest fixtures for authentication-aware testing.

Provides an authenticated client fixture that can be used in tests
that require authentication. Tests that don't need authentication can
continue to use TestClient directly.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.auth import hash_password
from app.config import Settings
from app.main import create_app
from app.storage import (
    create_user,
    initialize_storage,
)


@pytest.fixture
def settings(tmp_path):
    """Create settings for a temp database."""
    return Settings(_env_file=None, database_path=tmp_path / "test.sqlite3")


@pytest.fixture
def app(settings):
    """Create a FastAPI app with initialized storage."""
    app = create_app(settings)
    initialize_storage(settings.database_path)
    return app


@pytest.fixture
def client(app):
    """Create an unauthenticated test client."""
    return TestClient(app)


@pytest.fixture
def auth_client(app, tmp_path):
    """Create an authenticated test client with a registered user.

    Registers a user, logs in, and returns a TestClient with session cookies
    set. Use this for tests that require authentication.
    """
    database_path = app.state.settings.database_path
    create_user(database_path, "testuser", hash_password("testpassword123"))

    client = TestClient(app)

    # Log in to get session cookie
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "testuser", "password": "testpassword123"},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"

    return client


@pytest.fixture
def auth_client_with_data(app, tmp_path):
    """Create an authenticated test client with a document uploaded.

    Returns (client, document_id, user_id).
    """
    database_path = app.state.settings.database_path
    user_id = create_user(database_path, "testuser2", hash_password("testpassword123"))

    client = TestClient(app)

    # Log in
    csrf = client.get("/api/v1/auth/csrf-token").json()["csrfToken"]
    client.headers.update({"X-CSRF-Token": csrf})
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "testuser2", "password": "testpassword123"},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"

    # Upload a PDF document
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
        b"BT /F1 12 Tf 72 720 Td (Test document content for authenticated user.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    pdf_bytes = output.getvalue()

    response = client.post(
        "/api/v1/documents",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 201, f"Upload failed: {response.text}"
    document_id = response.json()["documentId"]

    return client, document_id, user_id
