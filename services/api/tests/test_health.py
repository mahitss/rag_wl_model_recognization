import asyncio

import httpx

from app.config import Settings
from app.main import create_app


def make_app(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "local" / "wonderlearn.sqlite3",
        cors_origins="http://localhost:5173",
    )
    return create_app(settings)


async def get_with_lifespan(app, path):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.get(path)


def test_health_reports_only_implemented_capabilities(tmp_path):
    response = asyncio.run(get_with_lifespan(make_app(tmp_path), "/api/v1/health"))

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "wonderlearn-api",
        "capabilities": {
            "document_ingestion": False,
            "retrieval": False,
            "model_inference": False,
        },
    }


def test_readiness_checks_real_sqlite_and_fts5_dependencies(tmp_path):
    response = asyncio.run(get_with_lifespan(make_app(tmp_path), "/api/v1/ready"))

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"sqlite": True, "sqlite_fts5": True},
    }


def test_readiness_reports_failed_dependency_check(tmp_path, monkeypatch):
    import app.main

    monkeypatch.setattr(
        app.main,
        "check_storage",
        lambda _: {"sqlite": False, "sqlite_fts5": False},
    )
    response = asyncio.run(get_with_lifespan(make_app(tmp_path), "/api/v1/ready"))

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"sqlite": False, "sqlite_fts5": False},
    }
