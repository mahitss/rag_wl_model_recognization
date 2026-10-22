import asyncio

import pytest
from auth_helpers import authenticated_request
from pydantic import ValidationError

from app.config import Settings
from app.diagrams.catalog import (
    DIAGRAMS,
    DiagramDefinition,
    RegionDefinition,
    resolve_concept_alias,
    resolve_region,
)
from app.main import create_app
from app.storage import initialize_storage, store_document


def make_settings(tmp_path, **overrides):
    return Settings(
        _env_file=None,
        database_path=tmp_path / "wonderlearn.sqlite3",
        cors_origins="http://localhost:5173",
        retrieval_mode="lexical",
        **overrides,
    )


async def request_with_lifespan(app, method, path, **kwargs):
    return await authenticated_request(app, method, path, **kwargs)


def seed_chunk(
    settings, *, text="Chlorophyll in leaves absorbs light energy during photosynthesis."
):
    initialize_storage(settings.database_path)
    store_document(
        settings.database_path,
        {
            "document_id": "doc_fixture_01",
            "display_name": "plant-study.pdf",
            "sha256": "a" * 64,
            "byte_size": 100,
            "page_count": 3,
            "chunk_count": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "parser_version": "fixture",
        },
        [
            {
                "chunk_id": "chk_fixture_01",
                "page_number": 2,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": len(text),
                "text": text,
            }
        ],
    )


def test_authored_diagram_definition_and_alias_resolution_are_valid():
    assert set(DIAGRAMS) == {"photosynthesis-cycle"}
    assert resolve_concept_alias("photosynthesis-cycle", "LEAF") == next(
        item
        for item in DIAGRAMS["photosynthesis-cycle"].concepts
        if item.concept_id == "biology.photosynthesis.chlorophyll"
    )
    resolved = resolve_region("photosynthesis-cycle", "chlorophyll")
    assert resolved is not None
    assert resolved[2].concept_id == "biology.photosynthesis.chlorophyll"
    assert resolve_concept_alias("photosynthesis-cycle", "unknown") is None


def test_region_geometry_and_stable_id_validation():
    with pytest.raises(ValidationError):
        RegionDefinition(
            region_id="bad/id",
            concept_id="biology.photosynthesis.sunlight",
            x=0.1,
            y=0.1,
            width=0.2,
            height=0.2,
        )
    with pytest.raises(ValidationError):
        RegionDefinition(
            region_id="out-of-bounds",
            concept_id="biology.photosynthesis.sunlight",
            x=0.9,
            y=0.1,
            width=0.2,
            height=0.2,
        )


def test_catalog_rejects_duplicate_or_overlapping_region_definitions():
    data = DIAGRAMS["photosynthesis-cycle"].model_dump()
    data["regions"] = [data["regions"][0], data["regions"][0]]
    with pytest.raises(ValidationError, match="Region IDs must be unique"):
        DiagramDefinition.model_validate(data)

    data = DIAGRAMS["photosynthesis-cycle"].model_dump()
    data["regions"][1]["x"] = 0.1
    data["regions"][1]["y"] = 0.1
    with pytest.raises(ValidationError, match="must not overlap"):
        DiagramDefinition.model_validate(data)


def test_catalog_rejects_missing_concepts_and_ambiguous_aliases():
    data = DIAGRAMS["photosynthesis-cycle"].model_dump()
    data["regions"][0]["concept_id"] = "biology.unknown"
    with pytest.raises(ValidationError, match="refer to a configured concept"):
        DiagramDefinition.model_validate(data)

    data = DIAGRAMS["photosynthesis-cycle"].model_dump()
    data["concepts"][1]["aliases"] = ["sunlight"]
    with pytest.raises(ValidationError, match="must not be ambiguous"):
        DiagramDefinition.model_validate(data)

    data = DIAGRAMS["photosynthesis-cycle"].model_dump()
    data["concepts"][1]["concept_id"] = data["concepts"][0]["concept_id"]
    with pytest.raises(ValidationError, match="Concept IDs must be unique"):
        DiagramDefinition.model_validate(data)


def test_diagram_list_schema_returns_normalized_curated_regions(tmp_path):
    app = create_app(make_settings(tmp_path))
    response = asyncio.run(request_with_lifespan(app, "GET", "/api/v1/diagrams"))

    assert response.status_code == 200
    diagram = response.json()["diagrams"][0]
    assert diagram["diagramId"] == "photosynthesis-cycle"
    assert diagram["imageUrl"] == "/diagrams/photosynthesis.svg"
    assert len(diagram["regions"]) == 5
    assert all(
        0 <= region["geometry"]["x"] < 1
        and region["geometry"]["x"] + region["geometry"]["width"] <= 1
        for region in diagram["regions"]
    )


def test_region_evidence_preserves_real_citation_provenance_and_server_query(tmp_path):
    settings = make_settings(tmp_path)
    seed_chunk(settings)
    app = create_app(settings)
    response = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/diagrams/photosynthesis-cycle/regions/chlorophyll/evidence",
            json={"limit": 3},
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "evidence_found"
    assert body["concept"]["conceptId"] == "biology.photosynthesis.chlorophyll"
    assert body["retrievalMode"] == "lexical"
    citation = body["results"][0]
    assert citation["citationId"] == "chk_fixture_01"
    assert citation["documentId"] == "doc_fixture_01"
    assert citation["displayFilename"] == "plant-study.pdf"
    assert citation["pageNumber"] == 2
    assert citation["chunkIndex"] == 0
    assert citation["charStart"] == 0
    assert citation["charEnd"] == len(
        "Chlorophyll in leaves absorbs light energy during photosynthesis."
    )
    assert citation["text"] == "Chlorophyll in leaves absorbs light energy during photosynthesis."
    assert citation["retrieval"]["method"] == "fts5_bm25"
    assert citation["retrieval"]["rank"] == 1


def test_unknown_diagram_and_region_return_safe_not_found_errors(tmp_path):
    app = create_app(make_settings(tmp_path))
    unknown_diagram = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/diagrams/not-supported/regions/chlorophyll/evidence",
            json={},
        )
    )
    unknown_region = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/diagrams/photosynthesis-cycle/regions/not-configured/evidence",
            json={},
        )
    )

    assert unknown_diagram.status_code == 404
    assert unknown_diagram.json()["error"]["code"] == "DIAGRAM_NOT_FOUND"
    assert unknown_region.status_code == 404
    assert unknown_region.json()["error"]["code"] == "DIAGRAM_REGION_NOT_FOUND"
    assert "C:" not in str(unknown_region.json())


def test_no_evidence_is_explicit_and_has_no_fabricated_citations(tmp_path):
    app = create_app(make_settings(tmp_path))
    response = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            "/api/v1/diagrams/photosynthesis-cycle/regions/glucose/evidence",
            json={},
        )
    )

    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_evidence"
    assert response.json()["results"] == []
    assert response.json()["concept"]["conceptId"] == "biology.photosynthesis.glucose"


def test_evidence_request_rejects_untrusted_filters_and_invalid_limits(tmp_path):
    app = create_app(make_settings(tmp_path))
    base = "/api/v1/diagrams/photosynthesis-cycle/regions/chlorophyll/evidence"
    extra_filter = asyncio.run(
        request_with_lifespan(
            app,
            "POST",
            base,
            json={"limit": 2, "documentIds": ["client-selected"]},
        )
    )
    invalid_limit = asyncio.run(request_with_lifespan(app, "POST", base, json={"limit": 0}))

    assert extra_filter.status_code == 422
    assert extra_filter.json()["error"]["code"] == "INVALID_REQUEST"
    assert invalid_limit.status_code == 422
