import asyncio
import json

import pytest
from auth_helpers import authenticated_request

from app.config import Settings
from app.knowledge_graph import parse_extraction, stable_concept_id
from app.main import create_app
from app.storage import (
    delete_document,
    get_knowledge_concept,
    initialize_storage,
    list_knowledge_concepts,
    store_knowledge_extraction,
)
from app.tutor import InvalidTutorResponse

SOURCE = "Photosynthesis uses chlorophyll to absorb light energy."
CHUNK = {
    "chunk_id": "chunk_kg_1",
    "document_id": "doc_kg_1",
    "display_name": "biology.pdf",
    "page_number": 3,
    "text": SOURCE,
}
EXTRACTION = {
    "concepts": [
        {
            "name": "Photosynthesis",
            "aliases": ["plant energy process"],
            "supportingText": "Photosynthesis uses chlorophyll",
        },
        {
            "name": "Chlorophyll",
            "aliases": ["green pigment"],
            "supportingText": "chlorophyll to absorb light energy",
        },
    ],
    "relationships": [
        {
            "source": "Photosynthesis",
            "target": "Chlorophyll",
            "relation": "uses",
            "supportingText": "Photosynthesis uses chlorophyll",
        }
    ],
}


def seed_db(path):
    initialize_storage(path)
    from app.storage import store_document

    store_document(
        path,
        {
            "document_id": CHUNK["document_id"],
            "display_name": CHUNK["display_name"],
            "sha256": "f" * 64,
            "byte_size": 101,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "parser_version": "fixture",
        },
        [
            {
                "chunk_id": CHUNK["chunk_id"],
                "page_number": CHUNK["page_number"],
                "chunk_index": 0,
                "char_start": 0,
                "char_end": len(SOURCE),
                "text": SOURCE,
            }
        ],
    )


def test_extraction_requires_exact_evidence_and_known_relationship_endpoints():
    parsed = parse_extraction(json.dumps(EXTRACTION), CHUNK)
    assert parsed["concepts"][0]["concept_id"] == stable_concept_id(" photosynthesis ")
    assert parsed["relationships"][0]["source_normalized"] == "photosynthesis"
    forged = json.loads(json.dumps(EXTRACTION))
    forged["relationships"][0]["supportingText"] = "Photosynthesis produces oxygen"
    with pytest.raises(InvalidTutorResponse):
        parse_extraction(json.dumps(forged), CHUNK)
    unknown = json.loads(json.dumps(EXTRACTION))
    unknown["relationships"][0]["target"] = "Unknown"
    with pytest.raises(InvalidTutorResponse):
        parse_extraction(json.dumps(unknown), CHUNK)


def test_aliases_are_explicit_but_never_used_to_merge_distinct_concepts(tmp_path):
    path = tmp_path / "graph.sqlite3"
    seed_db(path)
    parsed = parse_extraction(json.dumps(EXTRACTION), CHUNK)
    store_knowledge_extraction(path, CHUNK, parsed)
    second_chunk = dict(CHUNK, chunk_id="chunk_kg_2", page_number=4)
    from app.storage import store_document

    store_document(
        path,
        {
            "document_id": "doc_kg_2",
            "display_name": "biology-2.pdf",
            "sha256": "e" * 64,
            "byte_size": 101,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": "2026-01-02T00:00:00+00:00",
            "parser_version": "fixture",
        },
        [
            {
                "chunk_id": second_chunk["chunk_id"],
                "page_number": second_chunk["page_number"],
                "chunk_index": 0,
                "char_start": 0,
                "char_end": len(SOURCE),
                "text": SOURCE,
            }
        ],
    )
    other = {
        "concepts": [
            {
                "name": "Plant energy process",
                "aliases": ["plant energy process"],
                "supportingText": "Photosynthesis uses chlorophyll",
            }
        ],
        "relationships": [],
    }
    store_knowledge_extraction(
        path, second_chunk, parse_extraction(json.dumps(other), dict(second_chunk, text=SOURCE))
    )
    concepts = list_knowledge_concepts(path)
    assert len(concepts) == 3
    assert stable_concept_id("Photosynthesis") != stable_concept_id("Plant energy process")


def test_persistence_deduplicates_edges_and_document_deletion_cascades_provenance(tmp_path):
    path = tmp_path / "graph.sqlite3"
    seed_db(path)
    parsed = parse_extraction(json.dumps(EXTRACTION), CHUNK)
    store_knowledge_extraction(path, CHUNK, parsed)
    store_knowledge_extraction(path, CHUNK, parsed)
    detail = get_knowledge_concept(path, stable_concept_id("Photosynthesis"))
    assert detail is not None
    assert len(detail["relationships"]) == 1
    assert detail["relationships"][0]["status"] == "candidate"
    assert detail["relationships"][0]["documentId"] == CHUNK["document_id"]
    assert detail["relationships"][0]["chunkId"] == CHUNK["chunk_id"]
    assert delete_document(path, CHUNK["document_id"])
    assert list_knowledge_concepts(path) == []


async def request(app, method, path, **kwargs):
    return await authenticated_request(app, method, path, **kwargs)


def test_graph_api_extract_details_verify_and_tutor_use_curated_graph_node(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "graph.sqlite3",
        tutor_base_url="http://localhost:8080/v1",
    )
    seed_db(settings.database_path)
    app = create_app(settings)
    from app.storage import store_document

    store_document(
        settings.database_path,
        {
            "document_id": "doc_kg_2",
            "display_name": "biology-2.pdf",
            "sha256": "d" * 64,
            "byte_size": 101,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": "2026-01-02T00:00:00+00:00",
            "parser_version": "fixture",
        },
        [
            {
                "chunk_id": "chunk_kg_2",
                "page_number": 1,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": len(SOURCE),
                "text": SOURCE,
            }
        ],
    )
    monkeypatch.setattr(
        "app.api.v1.routes.extract_chunk",
        lambda _settings, chunk: parse_extraction(json.dumps(EXTRACTION), chunk),
    )
    extracted = asyncio.run(
        request(app, "POST", "/api/v1/knowledge-graph/extract", json={"chunkLimit": 1})
    )
    assert extracted.status_code == 200
    assert extracted.json()["nextAfterChunkId"] == "chunk_kg_1"
    second_batch = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/knowledge-graph/extract",
            json={"chunkLimit": 1, "afterChunkId": extracted.json()["nextAfterChunkId"]},
        )
    )
    assert second_batch.json()["nextAfterChunkId"] is None
    concepts = asyncio.run(request(app, "GET", "/api/v1/knowledge-graph")).json()["concepts"]
    concept_id = next(
        concept["conceptId"] for concept in concepts if concept["name"] == "Photosynthesis"
    )
    detail = asyncio.run(request(app, "GET", f"/api/v1/knowledge-graph/concepts/{concept_id}"))
    assert detail.status_code == 200
    assert detail.json()["relationships"][0]["supportingText"] == "Photosynthesis uses chlorophyll"
    relation_id = next(
        relation["relationshipId"]
        for relation in detail.json()["relationships"]
        if relation["chunkId"] == "chunk_kg_1"
    )
    verified = asyncio.run(
        request(app, "POST", f"/api/v1/knowledge-graph/relationships/{relation_id}/verify")
    )
    assert verified.json()["status"] == "human_verified"
    assert (
        asyncio.run(
            request(app, "POST", f"/api/v1/knowledge-graph/relationships/{relation_id}/verify")
        ).status_code
        == 200
    )
    detail_after_review = asyncio.run(
        request(app, "GET", f"/api/v1/knowledge-graph/concepts/{concept_id}")
    )
    reviewed_relation = next(
        relation
        for relation in detail_after_review.json()["relationships"]
        if relation["relationshipId"] == relation_id
    )
    assert reviewed_relation["status"] == "human_verified"
    missing = asyncio.run(request(app, "GET", "/api/v1/knowledge-graph/concepts/unknown"))
    assert missing.status_code == 404

    citations = [
        {
            "citationId": "real_chunk",
            "documentId": CHUNK["document_id"],
            "displayFilename": "biology.pdf",
            "pageNumber": 3,
            "chunkIndex": 0,
            "charStart": 0,
            "charEnd": 10,
            "text": SOURCE,
            "retrieval": {"method": "fts5_bm25", "rank": 1},
        }
    ]
    monkeypatch.setattr(
        "app.api.v1.routes.retrieve_with_mode",
        lambda *_args, **kwargs: {
            "results": citations,
            "retrievalMode": "lexical",
            "fallbackReason": None,
        },
    )
    monkeypatch.setattr(
        "app.api.v1.routes.generate_grounded_answer",
        lambda *_args, **_kwargs: {"answer": "Grounded [E1].", "citations": citations},
    )
    tutor = asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/tutor/answers",
            json={"question": "Explain it", "graphConceptId": concept_id},
        )
    )
    assert tutor.status_code == 200
    assert tutor.json()["citations"][0]["citationId"] == "real_chunk"


def test_graph_extraction_returns_safe_error_for_malformed_model_output(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "graph.sqlite3",
        tutor_base_url="http://localhost:8080/v1",
    )
    seed_db(settings.database_path)
    app = create_app(settings)
    monkeypatch.setattr(
        "app.api.v1.routes.extract_chunk",
        lambda *_args: (_ for _ in ()).throw(InvalidTutorResponse()),
    )
    response = asyncio.run(
        request(app, "POST", "/api/v1/knowledge-graph/extract", json={"chunkLimit": 1})
    )
    assert response.status_code == 502
    assert "stack" not in response.text.lower()
    assert list_knowledge_concepts(settings.database_path) == []


def test_graph_extraction_without_local_model_is_unavailable_not_fabricated(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "graph.sqlite3")
    seed_db(settings.database_path)
    app = create_app(settings)
    response = asyncio.run(
        request(app, "POST", "/api/v1/knowledge-graph/extract", json={"chunkLimit": 1})
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TUTOR_MODEL_UNAVAILABLE"
    assert list_knowledge_concepts(settings.database_path) == []
