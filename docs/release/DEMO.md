# WonderLearn Demo Script — 5 to 7 Minutes

**Audience:** stakeholder / product reviewer  
**Environment:** local-first, single-user. No network model calls; no external services.  
**Data:** seeded synthetic educational material (photosynthesis, water cycle, gravity, cell structure, Newton's laws, plant cells).  
**Distinguish:** demo data is seeded for the walkthrough; it is not real user activity and not a learning-effectiveness claim.

## Prerequisites (run once, before the demo)

From the repository root:

```powershell
Copy-Item .env.example .env
uv sync --project services/api --extra dev --extra ocr
npm ci --prefix apps/web
```

Start two terminals:

```powershell
# Terminal 1
uv run --project services/api uvicorn app.main:app --app-dir services/api --host 127.0.0.1 --port 8000

# Terminal 2
npm run dev --prefix apps/web
```

Open `http://127.0.0.1:5173`. Confirm `http://127.0.0.1:8000/api/v1/ready` returns HTTP 200 before beginning.

## Demo flow

### Minute 0–1: Upload educational material

**Goal:** show that a learner can index their own study material and that the system preserves page provenance.

1. Click **Add a learning PDF** in the evidence card.
2. Upload a PDF about photosynthesis (the demo corpus contains a seeded page: *"Photosynthesis is the process plants use to convert sunlight, water, and carbon dioxide into glucose and oxygen."*).
3. Confirm the upload returns HTTP 201 and the document appears in the **Search material** list.
4. Verify the document card shows `pageCount`, `chunkCount`, and byte size.

**What this proves:** PDF ingestion, per-page chunking, SHA-256 deduplication, safe upload limits (malformed/encrypted/empty files are rejected — covered by tests).

### Minute 1–2: Ask a question and inspect citations

**Goal:** show evidence-grounded, honest retrieval with honest abstention.

1. Select the **Chlorophyll in leaves** region in the photosynthesis diagram (or type *"What is photosynthesis?"* into the tutor question box).
2. Confirm the evidence panel returns a passage from page 1 with `documentId`, `pageNumber`, `chunkIndex`, and verbatim excerpt.
3. Type *"What is the boiling point of nitrogen?"* into the tutor box and click **Ask tutor**.
4. Confirm the system reports **insufficient evidence** and invents no answer.

**What this proves:** FTS5 retrieval, citation provenance (document/page/chunk), verbatim extractive answers, correct abstention on unanswerable questions — all covered by automated tests.

### Minute 2–3: Explore an image or image region

**Goal:** show local image upload, region selection, and region tutor (without camera footage claims).

1. Upload a PNG/JPEG educational image (the system accepts PNG/JPEG; validates signature, size, dimensions, pixels; rejects animated/malformed).
2. Draw a region on the image, or enter normalized coordinates (0–1).
3. Click **Save region and search evidence**, then **Ask grounded tutor**.
4. Confirm the tutor answer cites only the retrieved passage, with `[E1]` inline labels mapping to stored chunks.

**What this proves:** image validation, region evidence, citation allow-listing, no camera-frame persistence (camera frames are browser-only and never uploaded).

### Minute 3–4: Complete a quiz

**Goal:** show evidence-backed quiz generation and deterministic scoring.

1. Type *"photosynthesis"* into **Topic to practice** and click **Generate quiz from evidence**.
2. Confirm questions appear with multiple-choice options and citations to stored passages.
3. Select an incorrect option, click **Check answer**, and confirm it reports **incorrect** with the expected answer and explanation.
4. Select the correct option and confirm **correct**.
5. Click **Retry missed or uncertain questions** and confirm it filters to the incorrect item.

**What this proves:** quiz generation from retrieved evidence, citation-linked questions, deterministic scoring (exact option ID / token-set F1), attempt history — covered by automated tests.

### Minute 4–5: Review the study plan and analytics

**Goal:** show the deterministic study coach and local analytics.

1. Scroll to **AI study coach**. Confirm a recommendation exists (e.g., *"Review Photosynthesis"*) with reasons naming saved facts (overdue review, weak topic).
2. Click **Accept**, then **Mark complete**; confirm the status changes.
3. Scroll to **Learning analytics**. Confirm quiz accuracy, questions attempted, completed sessions, and overdue reviews reflect the activity just recorded.
4. Click **Practice this topic** on a weak topic and confirm a new quiz is generated.

**What this proves:** persisted quiz attempts, completed sessions, topic mastery (deterministic recency-weighted rule — not a validated learning measure), review scheduling.

### Minute 5–6: Knowledge graph (optional, if tutor configured)

**Goal:** show candidate concept/relationship extraction with human verify.

1. Click **Extract from study materials** in the Knowledge graph section (requires the optional local tutor; skip if not configured).
2. Confirm concepts (e.g., Photosynthesis, Chlorophyll) appear with candidate relationships.
3. Click **Mark as human-verified** on a relationship; confirm status flips to **Human verified**.
4. Click **Ask tutor about this concept** and confirm the answer cites only stored passages.

**What this proves:** graph extraction, candidate-vs-verified distinction, citation-to-source links — covered by automated tests.

### Minute 6–7: Delete and verify consistency

**Goal:** show deletion cascades and no orphaned citations.

1. Return to **Search material**, delete the uploaded PDF.
2. Confirm the document disappears and the study coach plan shows **no recommendations** (source deletion cascades dependent quiz questions and attempt history).
3. Confirm analytics attempt count resets while completed sessions remain (independent local records).

**What this proves:** deletion consistency across documents, chunks, FTS5, embeddings, quiz questions, attempts, and knowledge graph — covered by automated tests.

## Demo-data seeding (optional)

If you want a pre-populated corpus without uploading files, seed directly into SQLite:

```powershell
uv run --project services/api --extra dev python - <<'EOF'
from app.config import Settings
from app.storage import initialize_storage, store_document
from pathlib import Path
db = Path("data/local/wonderlearn.sqlite3")
initialize_storage(db)
store_document(db, {
    "document_id": "demo-photosynthesis",
    "display_name": "photosynthesis.txt",
    "sha256": "demo",
    "byte_size": 120,
    "page_count": 1,
    "chunk_count": 1,
    "created_at": "2026-01-01T00:00:00Z",
    "parser_version": "demo/1",
}, [{
    "chunk_id": "demo-chunk-1",
    "page_number": 1,
    "chunk_index": 0,
    "char_start": 0,
    "char_end": 120,
    "text": "Photosynthesis is the process plants use to convert sunlight, water, and carbon dioxide into glucose and oxygen.",
}])
print("Seeded.")
EOF
```

Then restart the API. The seeded document will appear in the UI immediately.

## What NOT to demonstrate

- **Camera tracking on real footage** — the prototype is validated only against the authored SVG; do not point it at unowned video.
- **Tutor answer quality as fact** — the tutor is optional and unreviewed; frame it as "grounded to retrieved passages, not a factual guarantee."
- **Mastery score as learning proof** — the mastery formula is an operational reporting rule, not a validated learning measure.
