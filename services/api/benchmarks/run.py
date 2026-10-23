"""M16 performance benchmark harness for WonderLearn.

Measures end-to-end latency for ingestion, retrieval, embedding, and database
operations using synthetic fixtures. Results are printed as JSON for easy
comparison before/after optimization.

Run from the repository root:

    uv run --project services/api --extra dev python services/api/benchmarks/run.py

Requires the optional semantic extra for embedding benchmarks:

    uv run --project services/api --extra dev --extra semantic python services/api/benchmarks/run.py
"""

from __future__ import annotations

import io
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "services/api")

from pypdf import PdfWriter  # noqa: E402
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject  # noqa: E402

from app.config import Settings  # noqa: E402
from app.embeddings.service import (  # noqa: E402
    EmbeddingService,
    ModelUnavailable,
    embedding_config_hash,
)
from app.ingestion.pdf import ingest_pdf  # noqa: E402
from app.storage import (  # noqa: E402
    get_document,
    initialize_storage,
    list_documents,
    search_chunks,
    search_semantic_chunks,
)


def make_pdf_pages(page_texts: list[str]) -> bytes:
    writer = PdfWriter()
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        if text:
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


def lorem_paragraphs(count: int) -> list[str]:
    base = (
        "Chlorophyll absorbs light energy in leaves. Plants use this energy to make food "
        "during photosynthesis. Carbon dioxide enters through stomata and water is absorbed "
        "through roots. Oxygen is released as a byproduct. The Calvin cycle fixes carbon "
        "into glucose using ATP and NADPH produced in the light reactions. "
    )
    return [base * (i + 1) for i in range(count)]


class Timer:
    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.elapsed = time.perf_counter() - self.start


def bench(fn, *, number: int = 5, warmup: int = 1) -> dict[str, float]:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(number):
        with Timer() as t:
            fn()
        samples.append(t.elapsed)
    return {
        "mean_s": round(statistics.mean(samples), 6),
        "median_s": round(statistics.median(samples), 6),
        "min_s": round(min(samples), 6),
        "max_s": round(max(samples), 6),
    }


def fresh_db() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="wl-bench-"))
    db_path = tmp / "wonderlearn.sqlite3"
    settings = Settings(
        _env_file=None,
        database_path=db_path,
        cors_origins="http://localhost:5173",
        retrieval_mode="lexical",
        chunk_size_chars=2000,
        chunk_overlap_chars=200,
    )
    initialize_storage(db_path)
    return db_path, settings, tmp


def ingest_fixture(db_path: Path, settings: Settings, pages: list[str], filename: str) -> str:
    pdf_bytes = make_pdf_pages(pages)
    metadata = ingest_pdf(
        io.BytesIO(pdf_bytes),
        filename=filename,
        database_path=db_path,
        settings=settings,
    )
    return metadata["document_id"]


def main() -> int:
    results: dict[str, object] = {}

    # --- Fixtures ---
    fixtures = {
        "small": lorem_paragraphs(3),
        "medium": lorem_paragraphs(25),
        "large": lorem_paragraphs(100),
    }

    # --- Ingestion benchmarks (fresh DB per run) ---
    ingestion: dict[str, dict[str, float]] = {}
    for name, pages in fixtures.items():
        pdf_bytes = make_pdf_pages(pages)

        def ingest_one(pb: bytes = pdf_bytes, fn: str = f"{name}.pdf") -> None:
            db_path, settings, _tmp = fresh_db()
            ingest_pdf(
                io.BytesIO(pb),
                filename=fn,
                database_path=db_path,
                settings=settings,
            )
            shutil.rmtree(_tmp, ignore_errors=True)

        ingestion[name] = bench(ingest_one, number=3, warmup=1)
        results[f"fixture_{name}_bytes"] = len(pdf_bytes)
        results[f"fixture_{name}_pages"] = len(pages)

    results["ingestion_s"] = ingestion

    # --- Set up medium DB for search benchmarks ---
    db_path, settings, _keep_tmp = fresh_db()
    document_id = ingest_fixture(db_path, settings, fixtures["medium"], "medium.pdf")

    # --- FTS5 search benchmarks ---
    search_queries = ["chlorophyll", "photosynthesis", "Calvin cycle", "stomata oxygen glucose"]
    fts_results: dict[str, dict[str, float]] = {}
    for query in search_queries:

        def search_one(q: str = query) -> list[dict]:
            return search_chunks(db_path, f'"{q}"', None, 10)

        fts_results[query] = bench(search_one, number=20, warmup=3)
    results["fts5_search_s"] = fts_results

    # --- Document list/get benchmarks ---
    results["list_documents_s"] = bench(lambda: list_documents(db_path), number=20, warmup=3)
    results["get_document_s"] = bench(
        lambda: get_document(db_path, document_id), number=20, warmup=3
    )

    # --- Semantic / embedding benchmarks (optional) ---
    semantic: dict[str, object] = {}
    try:
        embeddings = EmbeddingService(settings)

        rebuild_first = bench(lambda: embeddings.rebuild(db_path), number=1, warmup=0)
        semantic["rebuild_first_s"] = rebuild_first

        rebuild_second = bench(lambda: embeddings.rebuild(db_path), number=1, warmup=0)
        semantic["rebuild_second_s"] = rebuild_second

        status = embeddings.status(db_path)
        semantic["index_status"] = status["index"]

        query_vector = embeddings.query_vector("photosynthesis light reactions")
        cfg_hash = embedding_config_hash(settings)

        def semantic_search_one() -> list[dict]:
            return search_semantic_chunks(
                db_path,
                config_hash=cfg_hash,
                dimensions=settings.embedding_dimensions,
                query_vector=query_vector,
                limit=10,
            )

        semantic["semantic_search_s"] = bench(semantic_search_one, number=20, warmup=3)
        semantic["model_available"] = True

    except ModelUnavailable:
        semantic["model_available"] = False
        semantic["note"] = "Embedding model not cached; semantic benchmarks skipped."
    except Exception as exc:  # noqa: BLE001
        semantic["model_available"] = False
        semantic["note"] = f"Semantic benchmarks errored: {exc}"

    results["semantic"] = semantic

    print(json.dumps(results, indent=2, default=str))

    shutil.rmtree(_keep_tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
