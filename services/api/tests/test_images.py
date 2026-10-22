import asyncio
import io

import pytest
from auth_helpers import authenticated_request, prepare_test_account
from PIL import Image

from app.config import Settings
from app.errors import ApiError
from app.ingestion import image as image_ingestion
from app.main import create_app
from app.storage import delete_document, initialize_storage, store_document


def image_bytes(*, image_format="PNG", size=(32, 20), color="white"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format=image_format)
    return output.getvalue()


def make_settings(tmp_path, **overrides):
    return Settings(
        _env_file=None,
        database_path=tmp_path / "wonderlearn.sqlite3",
        cors_origins="http://localhost:5173",
        **overrides,
    )


async def request(app, method, path, **kwargs):
    return await authenticated_request(app, method, path, **kwargs)


def upload(app, data, *, filename="lesson.png", media_type="image/png"):
    return asyncio.run(
        request(
            app,
            "POST",
            "/api/v1/images",
            files={"file": (filename, data, media_type)},
        )
    )


def seed_document(database_path):
    initialize_storage(database_path)
    store_document(
        database_path,
        {
            "document_id": "doc_image_source",
            "display_name": "water-cycle.pdf",
            "sha256": "b" * 64,
            "byte_size": 80,
            "page_count": 1,
            "chunk_count": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "parser_version": "fixture",
        },
        [
            {
                "chunk_id": "chunk_image_source",
                "page_number": 3,
                "chunk_index": 0,
                "char_start": 0,
                "char_end": 70,
                "text": "Water evaporates from oceans during the water cycle.",
            }
        ],
    )


def test_normalize_png_and_jpeg_and_reject_malformed_content():
    png = image_ingestion.normalize_image(
        image_bytes(), max_bytes=100_000, max_dimension=100, max_pixels=10_000
    )
    jpeg = image_ingestion.normalize_image(
        image_bytes(image_format="JPEG"),
        max_bytes=100_000,
        max_dimension=100,
        max_pixels=10_000,
    )
    assert (png.content_type, png.width, png.height) == ("image/png", 32, 20)
    assert jpeg.content_type == "image/jpeg"
    with pytest.raises(ApiError) as error:
        image_ingestion.normalize_image(
            b"\x89PNG\r\nnot an image",
            max_bytes=100_000,
            max_dimension=100,
            max_pixels=10_000,
        )
    assert error.value.code == "INVALID_IMAGE"


def test_image_size_dimension_and_pixel_limits_are_enforced():
    sample = image_bytes(size=(40, 30))
    with pytest.raises(ApiError) as size_error:
        image_ingestion.normalize_image(sample, max_bytes=10, max_dimension=100, max_pixels=10_000)
    assert size_error.value.code == "IMAGE_TOO_LARGE"
    with pytest.raises(ApiError) as dimension_error:
        image_ingestion.normalize_image(
            sample, max_bytes=100_000, max_dimension=32, max_pixels=10_000
        )
    assert dimension_error.value.code == "IMAGE_DIMENSION_LIMIT_EXCEEDED"
    with pytest.raises(ApiError) as pixel_error:
        image_ingestion.normalize_image(
            sample, max_bytes=100_000, max_dimension=100, max_pixels=100
        )
    assert pixel_error.value.code == "IMAGE_PIXEL_LIMIT_EXCEEDED"


def test_ocr_success_empty_and_unavailable_are_explicit(monkeypatch):
    sample = image_bytes()
    monkeypatch.setattr(image_ingestion, "ocr_available", lambda: False)
    assert image_ingestion.recognize_text(sample) == ("unavailable", None)
    monkeypatch.setattr(image_ingestion, "ocr_available", lambda: True)
    monkeypatch.setattr("pytesseract.image_to_string", lambda _image: "  chlorophyll  ")
    assert image_ingestion.recognize_text(sample) == ("available", "chlorophyll")
    monkeypatch.setattr("pytesseract.image_to_string", lambda _image: "   ")
    assert image_ingestion.recognize_text(sample) == ("empty", "")
    monkeypatch.setattr(
        "pytesseract.image_to_string", lambda _image: (_ for _ in ()).throw(RuntimeError())
    )
    assert image_ingestion.recognize_text(sample) == ("failed", None)


def test_upload_validation_region_ocr_retrieval_provenance_and_deletion(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    seed_document(settings.database_path)
    app = create_app(settings)
    user_id = prepare_test_account(app)
    monkeypatch.setattr(
        "app.api.v1.routes.recognize_text", lambda _content: ("available", "water evaporates")
    )
    monkeypatch.setattr(
        "app.api.v1.routes.recognize_region",
        lambda _content, _geometry: ("available", "evaporates water cycle"),
    )

    created = upload(app, image_bytes(), filename="C:\\fake\\water.png")
    assert created.status_code == 201
    image = created.json()
    image_id = image["imageId"]
    assert image["displayFilename"] == "water.png"
    assert image["extractedText"] == "water evaporates"
    assert image["ocrStatus"] == "available"
    content = asyncio.run(request(app, "GET", f"/api/v1/images/{image_id}/content"))
    assert content.status_code == 200
    assert content.headers["cache-control"] == "private, no-store"
    assert Image.open(io.BytesIO(content.content)).format == "PNG"
    jpeg_upload = upload(
        app,
        image_bytes(image_format="JPEG"),
        filename="lesson.jpg",
        media_type="application/octet-stream",
    )
    assert jpeg_upload.status_code == 201
    assert jpeg_upload.json()["contentType"] == "image/jpeg"

    malformed = upload(app, b"not png", filename="spoof.png", media_type="image/png")
    too_big_app = create_app(make_settings(tmp_path / "limited", max_image_upload_bytes=10))
    too_big = upload(too_big_app, image_bytes(), filename="large.png")
    dimension_app = create_app(make_settings(tmp_path / "small-dims", max_image_dimension=16))
    too_wide = upload(dimension_app, image_bytes(), filename="wide.png")
    assert malformed.status_code == 422
    assert too_big.status_code == 413
    assert too_wide.status_code == 413

    bad_region = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/images/{image_id}/regions",
            json={"geometry": {"x": 0.8, "y": 0.1, "width": 0.3, "height": 0.2}},
        )
    )
    assert bad_region.status_code == 422
    boolean_region = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/images/{image_id}/regions",
            json={"geometry": {"x": True, "y": 0.1, "width": 0.3, "height": 0.2}},
        )
    )
    assert boolean_region.status_code == 422
    unknown_concept = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/images/{image_id}/regions",
            json={
                "geometry": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5},
                "diagramId": "photosynthesis-cycle",
                "conceptId": "client-invented-concept",
            },
        )
    )
    assert unknown_concept.status_code == 404

    created_region = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/images/{image_id}/regions",
            json={
                "geometry": {"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.5},
                "diagramId": "photosynthesis-cycle",
                "conceptId": "biology.photosynthesis.chlorophyll",
            },
        )
    )
    assert created_region.status_code == 201
    region = created_region.json()
    region_id = region["regionId"]
    assert region["extractedText"] == "evaporates water cycle"
    evidence = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/images/{image_id}/regions/{region_id}/evidence",
            json={"limit": 5},
        )
    )
    assert evidence.status_code == 200
    result = evidence.json()
    assert result["status"] == "evidence_found"
    citation = result["results"][0]
    assert citation["citationId"] == "chunk_image_source"
    assert citation["documentId"] == "doc_image_source"
    assert citation["displayFilename"] == "water-cycle.pdf"
    assert citation["pageNumber"] == 3
    fabricated_citations = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/images/{image_id}/regions/{region_id}/evidence",
            json={"limit": 5, "citations": [{"citationId": "invented"}]},
        )
    )
    assert fabricated_citations.status_code == 422

    def answer_from_evidence(*_args, **kwargs):
        return {"answer": "Water changes to vapor. [E1]", "citations": [kwargs["evidence"][0]]}

    monkeypatch.setattr("app.api.v1.routes.generate_grounded_answer", answer_from_evidence)
    tutor = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/images/{image_id}/regions/{region_id}/tutor",
            json={"question": "What happens here?"},
        )
    )
    assert tutor.status_code == 200
    assert tutor.json()["status"] == "answered"
    assert tutor.json()["imageId"] == image_id
    assert tutor.json()["regionId"] == region_id
    assert tutor.json()["citations"][0]["citationId"] == "chunk_image_source"

    assert delete_document(settings.database_path, "doc_image_source", user_id=user_id)
    after_source_delete = asyncio.run(request(app, "GET", f"/api/v1/images/{image_id}"))
    assert after_source_delete.status_code == 200
    assert after_source_delete.json()["regions"][0]["citations"] == []
    assert after_source_delete.json()["regions"][0]["extractedText"] == "evaporates water cycle"
    deleted = asyncio.run(request(app, "DELETE", f"/api/v1/images/{image_id}"))
    assert deleted.status_code == 200
    missing = asyncio.run(request(app, "GET", f"/api/v1/images/{image_id}"))
    assert missing.status_code == 404


def test_no_evidence_abstains_without_calling_tutor_and_ocr_unavailable_is_reported(
    tmp_path, monkeypatch
):
    app = create_app(make_settings(tmp_path))
    monkeypatch.setattr("app.api.v1.routes.recognize_text", lambda _content: ("unavailable", None))
    image = upload(app, image_bytes())
    image_id = image.json()["imageId"]
    assert image.json()["ocrStatus"] == "unavailable"
    region = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/images/{image_id}/regions",
            json={"geometry": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5}},
        )
    ).json()
    monkeypatch.setattr(
        "app.api.v1.routes.generate_grounded_answer",
        lambda *_args, **_kwargs: pytest.fail("No evidence must not call the tutor"),
    )
    tutor = asyncio.run(
        request(
            app,
            "POST",
            f"/api/v1/images/{image_id}/regions/{region['regionId']}/tutor",
            json={"question": "What is shown?"},
        )
    )
    assert tutor.status_code == 200
    assert tutor.json()["status"] == "insufficient_evidence"
    assert tutor.json()["citations"] == []
