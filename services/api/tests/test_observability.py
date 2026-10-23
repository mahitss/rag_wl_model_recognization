import asyncio

import httpx

from app.config import Settings
from app.main import create_app


def make_app(tmp_path, **overrides):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "local" / "wonderlearn.sqlite3",
        cors_origins="http://localhost:5173",
        diagnostics_token="supersecret-token",
        **overrides,
    )
    return create_app(settings)


async def get_with_lifespan(app, path, *, headers=None):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers or {},
        ) as client:
            return await client.get(path)


def test_metrics_endpoint_reports_privacy_safe_snapshot(tmp_path):
    app = make_app(tmp_path)
    async def invoke():
        return await get_with_lifespan(app, "/api/v1/metrics")

    response = asyncio.run(invoke())
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["requests_total"] >= 0
    assert payload["latency_ms"]["count"] >= 0
    assert "queries" not in payload
    assert "userId" not in str(payload)


def test_diagnostics_require_valid_token(tmp_path):
    app = make_app(tmp_path)

    response = asyncio.run(get_with_lifespan(app, "/api/v1/diagnostics"))
    assert response.status_code == 401

    response = asyncio.run(
        get_with_lifespan(app, "/api/v1/diagnostics", headers={"X-Diagnostics-Token": "wrong"})
    )
    assert response.status_code == 401

    response = asyncio.run(
        get_with_lifespan(app, "/api/v1/diagnostics", headers={"X-Diagnostics-Token": "supersecret-token"})
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_security_logging_redacts_sensitive_fields():
    from app.logging import _SECRET_KEY_RE

    assert _SECRET_KEY_RE.search("password") is not None
    assert _SECRET_KEY_RE.search("x-session-token") is not None
    assert _SECRET_KEY_RE.search("authorization") is not None


def test_health_endpoints_do_not_expose_internal_paths_or_tokens(tmp_path):
    app = make_app(tmp_path)
    response = asyncio.run(get_with_lifespan(app, "/api/v1/health"))
    body = str(response.json())
    assert "database" not in body.lower()
    assert "secret" not in body.lower()
    assert "token" not in body.lower()
