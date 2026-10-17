# RAG architecture

## Initial scope

Text-based PDFs and plain text only. Preserve page provenance and prefer exact extractable text. OCR, DOCX, tables and figures are staged extensions. Retrieval quality and citation correctness are measured independently before adding generation.

## Ingestion and records

For each document store opaque `document_id`, original filename, SHA-256, media type, byte size, page count, ingest timestamp, parser/version, extraction status and user deletion state. Each passage stores stable `chunk_id`, document ID, text, page start/end, heading path where extractable, source offsets/locator, token/character count, and optional embedding model/revision. Do not store the original file unless needed for user re-download; default retention can keep only extracted content and user can delete it.

Validate extension plus MIME/signature, size and page limits; reject password-protected or malformed PDFs, isolate parser errors, and do not execute embedded content. Extract text page-by-page. Use headings/page boundaries and paragraph/sentence boundaries; target moderate chunks (roughly 300–600 tokens with small overlap) as a starting hypothesis, then compare on the evaluation set. Never split a table silently into misleading prose; mark extraction limitations.

Scanned PDFs: detect pages with little/no text and report them as unavailable initially. Add OCR later via an optional local OCR pipeline (e.g., PaddleOCR, Apache-2.0 source project; verify each model/dictionary license and footprint) only when representative scanned materials and OCR evaluation exist. [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)

## Index and retrieval

- **Lexical:** SQLite FTS5 with BM25 ranking; it is built into SQLite's full-text search facilities, subject to checking the actual bundled SQLite build. [SQLite FTS5](https://www.sqlite.org/fts5.html)
- **Dense:** optional local sentence encoder plus FAISS CPU or a simple exact cosine scan for small corpora. FAISS project metadata identifies MIT licensing; check wheel/platform compatibility on Windows before selecting it. [FAISS](https://github.com/facebookresearch/faiss)
- **Hybrid:** fetch top N from each branch, normalize ranks, combine using reciprocal-rank fusion, deduplicate overlap, preserve source metadata. Avoid arbitrary similarity thresholds until calibrated on real labeled questions.
- **Reranking:** defer. Train/evaluate a local reranker only after retrieval judgments demonstrate a ranking gap.

FTS5-only is the first working baseline and does not require a separate vector database. SQLite FTS5 uses BM25 ranking; lexical search is often strong for exact diagram labels and textbook terms. Dense search helps paraphrases but introduces model download/license/latency and may be worse on domain terms. Keep both replaceable.

## Citations and answer generation

Return evidence passages with stable IDs and exact document/page/section locators. The answer generator receives only retrieved evidence plus a prompt that labels it untrusted source text. It must not obey source instructions, reveal hidden prompts, or treat quoted instructions as policy. Build citations in application code from evidence IDs; validate every cited ID against the current evidence pack. If evidence is weak or absent, abstain and offer a search refinement. Keep fact claims attributable; generated examples must be labeled as examples. A simple template is the initial answer path.

## Deletion and updates

Re-ingesting identical content is idempotent by content hash. Replacing a document creates a new indexed revision or transactionally replaces chunks. Deletion removes database metadata, FTS rows, vectors, derived OCR assets, and document copies if retained; document this scope to the user. SQLite backups and OS-level deleted-file recovery are outside application guarantees and should be disclosed in local privacy notes.

## Multimodal future

Store figure assets by document/page/figure ID with caption text, crop coordinates, extraction method, and source locator. Add a visual encoder only after image-question evaluation data exists. Connect a detected component to evidence via a curated alias/concept map first; later include figure-caption and diagram-region relations. A text embedding over an image caption is text retrieval, not image understanding. Any multimodal model must be named with its actual image-input capability and tested separately.
