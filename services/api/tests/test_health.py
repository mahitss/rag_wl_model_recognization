import asyncio

import httpx

from app.config import Settings
from app.main import create_app
from app.storage import check_storage


def make_app(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "local" / "wonderlearn.sqlite3",
        cors_origins="http://localhost:5173",
    )
    return create_app(settings)


async def get_with_lifespan(app, path, before_request=None):
    async with app.router.lifespan_context(app):
        if before_request is not None:
            before_request()
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


def test_missing_database_fails_check_and_readiness_without_creating_file(tmp_path):
    app = make_app(tmp_path)
    database_path = tmp_path / "local" / "wonderlearn.sqlite3"

    assert check_storage(database_path) == {"sqlite": False, "sqlite_fts5": False}
    assert not database_path.exists()

    response = asyncio.run(
        get_with_lifespan(
            app,
            "/api/v1/ready",
            before_request=lambda: database_path.unlink(),
        )
    )

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"sqlite": False, "sqlite_fts5": False},
    }
    assert not database_path.exists()
