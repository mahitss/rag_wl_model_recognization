import asyncio
import io
import math
import sqlite3
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.config import Settings
from app.embeddings.service import (
    EmbeddingService,
    ModelUnavailable,
    validate_and_normalize_vectors,
)
from app.errors import ApiError
from app.main import create_app
from app.retrieval.service import reciprocal_rank_fusion, retrieve_with_mode
from app.storage import delete_document, initialize_storage, store_document


class ConceptFakeModel:
    def __init__(self):
        self.batch_sizes = []

    def embed(self, texts, *, batch_size):
        self.batch_sizes.append((len(texts), batch_size))
        for text in texts:
            lowered = text.lower()
            if any(word in lowered for word in ("photosynthesis", "sunlight", "plant", "glucose")):
                yield [1.0, 0.0, 0.0, 0.0]
            elif any(word in lowered for word in ("evaporation", "vaporization", "water vapor")):
                yield [0.0, 1.0, 0.0, 0.0]
            elif any(word in lowered for word in ("gravity", "mass")):
                yield [0.0, 0.0, 1.0, 0.0]
            else:
                yield [0.0, 0.0, 0.0, 1.0]


def make_settings(tmp_path, **overrides):
    values = {
        "_env_file": None,
        "database_path": tmp_path / "wonderlearn.sqlite3",
        "cors_origins": "http://localhost:5173",
        "embedding_dimensions": 4,
        "embedding_batch_size": 2,
        "lexical_candidate_limit": 10,
        "semantic_candidate_limit": 10,
    }
    values.update(overrides)
    return Settings(**values)


def seed_chunks(database_path):
    initialize_storage(database_path)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    documents = [
        (
            "doc-plant",
            "plant.pdf",
            [
                ("chunk-plant-p1", 1, "Photosynthesis converts sunlight into glucose for plants."),
                ("chunk-plant-p2", 2, "Chlorophyll absorbs light and supports glucose production."),
            ],
        ),
        (
            "doc-water",
            "water.pdf",
            [("chunk-water-p1", 1, "Evaporation changes liquid water into water vapor.")],
        ),
        (
            "doc-gravity",
            "gravity.pdf",
            [("chunk-gravity-p1", 1, "Gravity attracts objects with mass.")],
        ),
    ]
    for document_id, filename, page_chunks in documents:
        combined = "\n".join(text for _, _, text in page_chunks)
        chunks = [
            {
                "chunk_id": chunk_id,
                "page_number": page_number,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": len(text),
                "text": text,
            }
            for chunk_id, page_number, text in page_chunks
        ]
        store_document(
            database_path,
            {
                "document_id": document_id,
                "display_name": filename,
                "sha256": f"hash-{document_id}",
                "byte_size": len(combined.encode()),
                "page_count": len(chunks),
                "chunk_count": len(chunks),
                "created_at": now,
                "parser_version": "test/1",
            },
            chunks,
        )


def fake_service(settings):
    model = ConceptFakeModel()
    return EmbeddingService(settings, model_factory=lambda _: model), model


def make_text_pdf(text: str) -> bytes:
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
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_embedding_validation_checks_dimensions_finiteness_and_norm():
    normalized = validate_and_normalize_vectors([[3.0, 4.0]], expected_count=1, dimensions=2)
    assert normalized[0] == pytest.approx((0.6, 0.8))
    assert math.hypot(*normalized[0]) == pytest.approx(1.0)
    with pytest.raises(ValueError, match="dimensional"):
        validate_and_normalize_vectors([[1.0]], expected_count=1, dimensions=2)
    with pytest.raises(ValueError, match="non-finite"):
        validate_and_normalize_vectors([[float("nan"), 1.0]], expected_count=1, dimensions=2)
    with pytest.raises(ValueError, match="zero"):
        validate_and_normalize_vectors([[0.0, 0.0]], expected_count=1, dimensions=2)
    with pytest.raises(ValueError, match="unexpected number"):
        validate_and_normalize_vectors([], expected_count=1, dimensions=2)


def test_semantic_configuration_rejects_invalid_modes_and_limits(tmp_path):
    with pytest.raises(ValidationError):
        make_settings(tmp_path, retrieval_mode="vector")
    with pytest.raises(ValidationError):
        make_settings(tmp_path, semantic_min_similarity=1.1)
    with pytest.raises(ValidationError):
        make_settings(tmp_path, semantic_candidate_limit=10_001)


def test_rebuild_is_batched_persistent_and_idempotent(tmp_path):
    settings = make_settings(tmp_path)
    seed_chunks(settings.database_path)
    service, model = fake_service(settings)
    first = service.rebuild(settings.database_path)
    assert first["complete"] is True
    assert first["indexedThisRun"] == 4
    assert first["index"]["current_chunks"] == 4
    assert max(size for size, _ in model.batch_sizes) <= settings.embedding_batch_size
    second = service.rebuild(settings.database_path)
    assert second["indexedThisRun"] == 0
    assert second["complete"] is True
    assert len(model.batch_sizes) == 2

    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0] == 4
    semantic = retrieve_with_mode(
        settings.database_path,
        query="How do plants make sugar from light?",
        document_ids=None,
        limit=3,
        mode="semantic",
        settings=settings,
        embeddings=service,
    )
    assert semantic["retrievalMode"] == "semantic"
    assert semantic["results"][0]["citationId"] == "chunk-plant-p1"
    assert semantic["results"][0]["pageNumber"] == 1
    assert semantic["results"][0]["retrieval"]["method"] == "cosine_similarity"


def test_rebuild_repairs_invalid_vector_with_matching_config_hash(tmp_path):
    settings = make_settings(tmp_path)
    seed_chunks(settings.database_path)
    service, model = fake_service(settings)
    service.rebuild(settings.database_path)
    initial_status = service.status(settings.database_path)["index"]
    assert initial_status["current_chunks"] == 4

    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "UPDATE chunk_embeddings SET vector = ? WHERE chunk_id = ?",
            ("x" * (4 * settings.embedding_dimensions), "chunk-plant-p1"),
        )

    invalid_status = service.status(settings.database_path)
    assert invalid_status["index"]["stale_chunks"] == 1
    assert invalid_status["index"]["missing_chunks"] == 1
    with pytest.raises(ApiError) as error:
        retrieve_with_mode(
            settings.database_path,
            query="photosynthesis",
            document_ids=None,
            limit=3,
            mode="semantic",
            settings=settings,
            embeddings=service,
        )
    assert error.value.code == "EMBEDDING_INDEX_INCOMPLETE"

    batches_before_repair = len(model.batch_sizes)
    repaired = service.rebuild(settings.database_path)
    assert repaired["indexedThisRun"] == 1
    assert repaired["index"]["current_chunks"] == 4
    assert repaired["index"]["stale_chunks"] == 0
    assert repaired["index"]["missing_chunks"] == 0
    assert len(model.batch_sizes) == batches_before_repair + 1


def test_model_revision_change_marks_index_stale_until_rebuild(tmp_path):
    settings = make_settings(tmp_path)
    seed_chunks(settings.database_path)
    old_service, _ = fake_service(settings)
    old_service.rebuild(settings.database_path)
    changed = make_settings(
        tmp_path,
        embedding_model_revision="another-model-revision",
    )
    new_service, _ = fake_service(changed)
    status = new_service.status(changed.database_path)
    assert status["index"]["stale_chunks"] == 4
    assert status["index"]["missing_chunks"] == 4
    with pytest.raises(ApiError) as error:
        retrieve_with_mode(
            changed.database_path,
            query="photosynthesis",
            document_ids=None,
            limit=3,
            mode="semantic",
            settings=changed,
            embeddings=new_service,
        )
    assert error.value.code == "EMBEDDING_INDEX_INCOMPLETE"
    rebuilt = new_service.rebuild(changed.database_path)
    assert rebuilt["index"]["stale_chunks"] == 0
    assert rebuilt["index"]["missing_chunks"] == 0

    changed_chunking = make_settings(tmp_path, chunk_size_chars=1500)
    chunking_service, _ = fake_service(changed_chunking)
    assert chunking_service.status(changed_chunking.database_path)["index"]["missing_chunks"] == 4


def test_hybrid_rrf_deduplicates_and_has_deterministic_ties():
    lexical = [
        {"citationId": "c1", "documentId": "d1", "pageNumber": 1, "chunkIndex": 0},
        {"citationId": "c2", "documentId": "d1", "pageNumber": 2, "chunkIndex": 0},
    ]
    semantic = [
        {
            "citationId": "c2",
            "documentId": "d1",
            "pageNumber": 2,
            "chunkIndex": 0,
            "retrieval": {"similarity": 0.9},
        },
        {
            "citationId": "c3",
            "documentId": "d2",
            "pageNumber": 1,
            "chunkIndex": 0,
            "retrieval": {"similarity": 0.8},
        },
    ]
    first = reciprocal_rank_fusion(lexical, semantic, k=10)
    second = reciprocal_rank_fusion(lexical, semantic, k=10)
    assert first == second
    assert [row["citationId"] for row in first] == ["c2", "c1", "c3"]
    assert first[0]["retrieval"]["method"] == "rrf_hybrid"
    assert first[0]["retrieval"]["lexicalRank"] == 2
    assert first[0]["retrieval"]["semanticRank"] == 1


def test_hybrid_uses_lexical_fallback_if_model_unavailable(tmp_path):
    settings = make_settings(tmp_path)
    seed_chunks(settings.database_path)
    working, _ = fake_service(settings)
    working.rebuild(settings.database_path)

    def unavailable(_):
        raise ModelUnavailable("offline")

    offline = EmbeddingService(settings, model_factory=unavailable)
    result = retrieve_with_mode(
        settings.database_path,
        query="photosynthesis",
        document_ids=None,
        limit=3,
        mode="hybrid",
        settings=settings,
        embeddings=offline,
    )
    assert result["retrievalMode"] == "lexical_fallback"
    assert result["fallbackReason"] == "embedding_model_unavailable"
    assert result["results"][0]["documentId"] == "doc-plant"
    with pytest.raises(ApiError) as error:
        retrieve_with_mode(
            settings.database_path,
            query="photosynthesis",
            document_ids=None,
            limit=3,
            mode="semantic",
            settings=settings,
            embeddings=offline,
        )
    assert error.value.code == "EMBEDDING_MODEL_UNAVAILABLE"


def test_partial_embedding_failure_keeps_fts_and_reports_incomplete_index(tmp_path):
    settings = make_settings(tmp_path)
    seed_chunks(settings.database_path)

    class FailsOnSecondBatch:
        def __init__(self):
            self.calls = 0

        def embed(self, texts, *, batch_size):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("injected inference failure")
            return ConceptFakeModel().embed(texts, batch_size=batch_size)

    service = EmbeddingService(settings, model_factory=lambda _: FailsOnSecondBatch())
    with pytest.raises(ModelUnavailable):
        service.rebuild(settings.database_path)
    status = service.status(settings.database_path)
    assert status["index"]["indexed_chunks"] == 2
    assert status["index"]["missing_chunks"] == 2
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 4
        assert connection.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == 4
    lexical = retrieve_with_mode(
        settings.database_path,
        query="photosynthesis",
        document_ids=None,
        limit=3,
        mode="lexical",
        settings=settings,
        embeddings=service,
    )
    assert lexical["results"][0]["citationId"] == "chunk-plant-p1"


def test_no_semantic_candidate_abstains_or_falls_back_lexically(tmp_path):
    settings = make_settings(tmp_path)
    seed_chunks(settings.database_path)
    service, _ = fake_service(settings)
    service.rebuild(settings.database_path)
    result = retrieve_with_mode(
        settings.database_path,
        query="tectonic earthquakes",
        document_ids=None,
        limit=3,
        mode="semantic",
        settings=settings,
        embeddings=service,
    )
    assert result["results"] == []
    assert result["fallbackReason"] == "no_candidates_above_threshold"
    hybrid = retrieve_with_mode(
        settings.database_path,
        query="tectonic earthquakes",
        document_ids=None,
        limit=3,
        mode="hybrid",
        settings=settings,
        embeddings=service,
    )
    assert hybrid["retrievalMode"] == "lexical_fallback"


def test_deletion_cascades_vectors_and_reingestion_indexes_again(tmp_path):
    settings = make_settings(tmp_path)
    seed_chunks(settings.database_path)
    service, _ = fake_service(settings)
    service.rebuild(settings.database_path)
    assert delete_document(settings.database_path, "doc-plant") is True
    status = service.status(settings.database_path)
    assert status["index"]["indexed_chunks"] == 2
    assert status["index"]["missing_chunks"] == 0

    store_document(
        settings.database_path,
        {
            "document_id": "doc-plant-again",
            "display_name": "plant-again.pdf",
            "sha256": "hash-plant-again",
            "byte_size": 25,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "parser_version": "test/1",
        },
        [
            {
                "chunk_id": "chunk-plant-again-p1",
                "page_number": 1,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": len("Photosynthesis from sunlight"),
                "text": "Photosynthesis from sunlight",
            }
        ],
    )
    assert service.rebuild(settings.database_path)["index"]["missing_chunks"] == 0


def test_embedding_status_and_rebuild_endpoints_use_injected_offline_model(tmp_path):
    settings = make_settings(tmp_path)
    seed_chunks(settings.database_path)
    app = create_app(settings)
    service, _ = fake_service(settings)
    app.state.embedding_service = service

    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                before = await client.get("/api/v1/embeddings/status")
                rebuilt = await client.post("/api/v1/embeddings/rebuild", json={})
                after = await client.get("/api/v1/embeddings/status")
                semantic = await client.post(
                    "/api/v1/retrieve",
                    json={"query": "plants use sunlight", "mode": "semantic"},
                )
        return before, rebuilt, after, semantic

    before, rebuilt, after, semantic = asyncio.run(run())
    assert before.status_code == 200
    assert before.json()["index"]["missingChunks"] == 4
    assert rebuilt.status_code == 200
    assert rebuilt.json()["complete"] is True
    assert after.json()["modelState"] == "available"
    assert semantic.status_code == 200
    assert semantic.json()["results"][0]["documentId"] == "doc-plant"


def test_semantic_mode_upload_attempts_embedding_without_breaking_ingestion(tmp_path):
    settings = make_settings(tmp_path, retrieval_mode="hybrid")
    app = create_app(settings)
    service, _ = fake_service(settings)
    app.state.embedding_service = service

    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await client.post(
                    "/api/v1/documents",
                    files={
                        "file": (
                            "plant.pdf",
                            make_text_pdf("Photosynthesis uses sunlight."),
                            "application/pdf",
                        )
                    },
                )

    response = asyncio.run(run())
    assert response.status_code == 201
    assert response.json()["embeddingStatus"] == "indexed"
    assert service.status(settings.database_path)["index"]["current_chunks"] == 1
