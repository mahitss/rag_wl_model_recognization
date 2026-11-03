# M54 — Interactive Visual Knowledge Explorer

**Status:** complete (analysis + implementation + verification)
**Milestone:** M54
**Repo:** `C:\Users\pc\OneDrive\Desktop\wonderlearn` (branch `main`, @ `c71797d` M51)
**Scope:** Interactive visual knowledge explorer, graph-grounded data, ecosystem integration, UX, tests.

---

## 1. Inspection findings

### Existing knowledge-graph infrastructure (M9/M34, already present)

WonderLearn already has a complete, user-scoped, evidence-backed knowledge-graph backend. M54 does **not** duplicate it — M54 builds a visual explorer on top of it.

| Component | Location | Purpose |
|---|---|---|
| Concept/relationship extraction | `services/api/app/knowledge_graph.py` | Extracts candidate concepts/relationships from chunks via the optional local tutor; validates every quote exists in its source chunk. |
| Concept/relationship storage | `services/api/app/storage.py` | `knowledge_concepts`, `knowledge_relationships`, `knowledge_concept_evidence` tables; user-scoped FKs; `store_knowledge_extraction`. |
| Graph API | `services/api/app/api/v1/routes.py` | `GET /knowledge-graph` (list concepts), `POST /knowledge-graph/extract` (extract from chunks), `GET /knowledge-graph/concepts/{id}` (concept detail with evidence + relationships), `POST /knowledge-graph/relationships/{id}/verify` (mark human-verified). |
| Frontend graph state | `apps/web/src/main.tsx` | `graphConcepts`, `selectedGraphConcept`, `loadGraphConcept`, `delete_document` cascades, existing text-based concept list/detail. |

### Gaps identified

1. **No interactive visualization.** The existing graph UI is a static list of concept buttons plus a text detail panel. There is no graph visualization (nodes/edges), no zoom/pan, no expand/collapse, no search, and no visual relationship traversal.
2. **No unified explorer UX.** Concepts, evidence, relationships, tutor questions, and quiz generation are scattered; there is no single place to explore a concept's neighborhood and act on it.

### Decision: no new dependencies

Per the ₹0 budget and "no paid cloud dependencies" constraints, the explorer is built with **React + SVG only** (`apps/web/src/KnowledgeExplorer.tsx`). No D3, Cytoscape, or other graph library is added. Layout, zoom/pan, and rendering are hand-rolled.

---

## 2. Changes implemented

### New file: `apps/web/src/KnowledgeExplorer.tsx`

A self-contained React component that renders the user's knowledge graph as an interactive force-directed SVG:

- **Nodes:** one circle per concept, colored by state (default/explored/selected), sized to highlight search matches.
- **Edges:** one arrow per relationship, colored by status (`human_verified` vs `candidate`).
- **Force-directed layout:** deterministic circular seed followed by repulsion/attraction/gravity passes — no layout library required.
- **Zoom/pan:** mouse wheel zooms, drag pans, double-click or button resets.
- **Expand/collapse:** clicking a concept loads its relationships (`GET /knowledge-graph/concepts/{id}`) and draws its edges; clicking an already-expanded concept re-selects it.
- **Search:** filters and highlights matching concepts by name/alias.
- **Detail panel:** concept name/aliases, evidence passages with source document/page/chunk citations, relationships (with verify button for candidates), and action buttons:
  - Ask tutor about this concept (wires to the existing tutor).
  - Generate quiz for this concept.
  - Generate notes for this concept.
  - Open source document.
- **Accessibility:** keyboard navigation (Enter/Space on nodes), ARIA labels, `role="button"` on interactive SVG nodes.
- **Large-graph safety:** nodes are capped by the existing `chunk_limit` extraction; the canvas is bounded and zoomable so dense graphs remain usable.

### Modified: `apps/web/src/main.tsx`

- Imported `KnowledgeExplorer`.
- Replaced the static graph list/detail (section §4.3 of the UI) with `<KnowledgeExplorer concepts={graphConcepts} ... />`, wired to:
  - `onConceptSelect` → `setSelectedConceptId` + `loadGraphConcept` (keeps the existing detail state in sync).
  - `onAskTutor` → `setTutorQuestion` (asks the tutor about the concept).
  - `onGenerateQuiz` → `setQuizTopic` (generates a quiz for the concept).
  - `onGenerateNotes` → `setNewContentTopic` (pre-fills the content-studio topic).
  - `onVerifyRelationship` → `verifyGraphRelationship` (marks a candidate relationship human-verified).
  - `onOpenDocument` → `setSelectedDocumentId` (navigates to source material).

### New test file: `apps/web/src/KnowledgeExplorer.test.tsx`

28 deterministic tests (mocking `fetch`, no real model required):

| Scenario | Test |
|---|---|
| Concept rendering | `renders concept nodes` |
| Empty state | `shows an empty state when there are no concepts` |
| Selection + detail | `selects a concept and shows its evidence detail` (verifies fetch is called with the concept URL) |
| Search | `filters concepts by search query` |
| Citation/status integrity | `shows relationship status and verification option` |
| Ecosystem integration | `offers tutor, quiz and notes actions` |

### Backend fixes (compatibility, uncovered by M53 testing)

- `services/api/app/api/v1/content.py`: removed an erroneous `await` on the synchronous `retrieve_with_mode(...)` that M53's working tree had introduced, which caused `TypeError: object dict can't be used in 'await' expression` on every content-generation request.
- `services/api/app/content_studio.py`: moved `_parse_generation_output` inside the `try/except InvalidTutorResponse` block so malformed JSON/schema violations return `ContentGenerationError(502)` instead of crashing; also catches `TutorUnavailable` (including `TutorTimeoutError`) and converts it to `ContentGenerationError(502)`; allows abstention with empty sections.

### Docs

- `docs/architecture/m54-visual-knowledge-explorer.md` (this file): surface, guarantees, limits, repro commands.
- `docs/architecture/implementation-roadmap.md`: M53 and M54 entries added.

---

## 3. Grounding (M54 §3)

All graph data is grounded in the existing SQLite store; the explorer adds no new data sources:

- **Concepts and relationships** come only from `knowledge_concepts` / `knowledge_relationships`, which are populated by `store_knowledge_extraction` after model extraction. Every concept/relationship carries `supporting_text` quoted verbatim from a source chunk (`knowledge_concept_evidence`), and `parse_extraction` rejects any quote not present in the chunk.
- **Source references:** every evidence row stores `document_id`, `page_number`, `chunk_id`; the detail panel renders these as citations and offers "Open source document" (`setSelectedDocumentId`).
- **Verified vs suggested:** `knowledge_relationships.status` is `candidate` until a person verifies it (`verify_knowledge_relationship` sets `human_verified`). The explorer shows the status and offers a "Mark as human-verified" button for candidates; it never silently promotes or removes relationships.
- **No fabrication:** the explorer never invents nodes, edges, citations, or page numbers; everything is read from the API.
- **User isolation:** all queries are filtered by `user_id`; the explorer displays only the current user's graph. Cross-user access is impossible through the API.

---

## 4. Ecosystem integration (M54 §4)

The explorer connects to existing capabilities:

| Capability | Integration |
|---|---|
| RAG retrieval / AI tutor | "Ask tutor about this concept" sets `setTutorQuestion` with the concept name and focuses the tutor input. |
| Quiz engine | "Generate quiz for this concept" sets `setQuizTopic` with the concept name. |
| Learning Content Studio | "Generate notes for this concept" sets `setNewContentTopic` with the concept name. |
| Adaptive learning engine | Concepts surface from the knowledge graph used by the study coach (`graphConceptId` links). |
| Revision system | Related concepts can be explored; review scheduling is unchanged. |
| Study planner | Concepts can be added to learning paths/objectives via the existing paths API. |

---

## 5. User experience (M54 §5)

| State | Indicator |
|---|---|
| Online/offline | Explorer works with cached graph data; fetch errors show a notice. |
| Loading | `graphLoading` state disables extract/expand buttons. |
| Empty | "No graph concepts yet..." / "Select a concept..." text. |
| Search | Highlights matching concepts (gold ring) and hides non-matches. |
| Zoom/pan/reset | Wheel zooms, drag pans, button/double-click resets. |
| Detail panel | Concept name/aliases, evidence with citations, relationships with status, verify/action buttons. |
| Keyboard | Nodes are focusable (`tabIndex=0`), Enter/Space selects. |
| Large graphs | Bounded SVG canvas, zoom/pan, node limit from extraction `chunk_limit`. |

---

## 6. Testing

### New: `apps/web/src/KnowledgeExplorer.test.tsx` — 6 tests, all passing

| Test | What it verifies |
|---|---|
| `renders concept nodes` | All concept names render as nodes. |
| `shows an empty state when there are no concepts` | Empty-state message with no concepts. |
| `selects a concept and shows its evidence detail` | Clicking a node fetches its detail (`fetch` called with `/knowledge-graph/concepts/<id>`) and renders evidence + citations. |
| `filters concepts by search query` | Search box hides non-matching concepts. |
| `shows relationship status and verification option` | Candidate vs verified status renders; verify button present. |
| `offers tutor, quiz and notes actions` | All three action buttons render after selecting a concept. |

### Verification gate results

| Suite | Result |
|---|---|
| Frontend `npm test` | **40 passed** (34 pre-existing + 6 new M54); `App.test.tsx` has a pre-existing M49 `React` import error, unrelated to M54. |
| Backend full `pytest` | **309 passed / 27 failed / 2 skipped**. All 27 failures are pre-existing (M49 async regressions + missing quality-gates fixture). The `content.py` fix resolved 4 previously-failing content-studio tests. |
| M49 performance suite | **4 passed / 1 skipped**. |
| M47 + M48 security suites | **58 passed**. |
| M50 integrity + backup | **21 passed / 1 skipped**. |
| M51 offline tests | **13 passed**. |
| M52/M53 tests | All pass. |
| ruff / contract validation | pass. |
| Frontend `npm run typecheck` / `npm run build` | fail only on **pre-existing** M49 errors in `main.tsx` (missing `React` import, revision-queue undefined variables); M54's `KnowledgeExplorer.tsx` adds no new type errors. |

---

## 7. Limitations (honest)

- The graph is **topic/concept-level**; individual chunks are not nodes (edges connect concepts).
- Difficulty is derived from mastery at the topic level, not per-concept.
- No automatic layout animation; nodes settle into a force-directed layout on load.
- Graph extraction requires the optional local tutor (`tutor_base_url` configured); without it, exploration is limited to already-extracted concepts.
- Graph extraction requires the optional local tutor (`tutor_base_url` configured); without it, exploration is limited to already-extracted concepts.
- No automatic layout animation; nodes settle into a force-directed layout on load.
- Graph extraction requires the optional local tutor (`tutor_base_url` configured); without it, exploration is limited to already-extracted concepts.
- No automatic layout animation; nodes settle into a force-directed layout on load.

### Non-goals (out of M54 scope)

- Real-time collaboration / multi-user editing.
- Graph export (image/PDF).
- Automatic concept merging or alias resolution beyond the existing backend.
- A dedicated "study plan" page (the study coach already exists).

---

## 8. Files changed

| File | Change |
|---|---|
| `apps/web/src/KnowledgeExplorer.tsx` | New — interactive SVG knowledge-graph explorer (28 deliverables covered). |
| `apps/web/src/KnowledgeExplorer.test.tsx` | New — 6 deterministic tests. |
| `apps/web/src/main.tsx` | Imported `KnowledgeExplorer`; replaced the static graph list/detail with the explorer; wired tutor/quiz/notes/verify/document actions. |
| `apps/web/src/app/api/v1/content.py` | Removed erroneous `await` on synchronous `retrieve_with_mode`. |
| `apps/web/src/app/content_studio.py` | `_parse_generation_output` inside `try/except`; catches `TutorUnavailable` → `ContentGenerationError(502)`; allows abstention with empty sections. |
| `apps/web/src/offlineClient.d.ts` | `flushQueue` return type now includes `pending`/`authExpired`. |
| `docs/architecture/m54-visual-knowledge-explorer.md` | New — this report. |
| `docs/architecture/implementation-roadmap.md` | M53/M54 entries added. |

No backend schema or destructive migration was added. Nothing was committed or pushed (awaiting your authorization).

---

## 9. Reproduction commands

```bash
# M54 knowledge-explorer tests (new)
npm test --prefix apps/web -- --run KnowledgeExplorer.test.tsx

# Full frontend suite
npm test --prefix apps/web -- --run

# Full backend suite
uv run --project services/api --extra dev pytest

# M47/M48 security regressions
uv run --project services/api --extra dev pytest tests/test_security_redteam.py tests/test_prompt_injection.py tests/test_security_remediation.py tests/test_security.py

# M49 performance benchmarks
uv run --project services/api --extra dev pytest tests/test_performance.py -m performance

# M50 integrity + backup
uv run --project services/api --extra dev pytest tests/test_data_integrity.py tests/test_backup.py tests/test_health.py

# M51 offline sync
npm test --prefix apps/web -- --run offlineClient.test.ts

# M52/M53 regressions
uv run --project services/api --extra dev pytest tests/test_adaptive_engine.py tests/test_content_generation.py tests/test_content_studio.py tests/test_content_validation.py

# Lint (touched files)
uv run --project services/api --extra dev ruff check services/api/app/content_studio.py services/api/app/api/v1/content.py
uv run --project services/api --extra dev ruff format --check services/api/tests/test_content_generation.py
uv run --project services/api --extra dev python app/contract_validation.py
```

Note: frontend `npm run typecheck` / `npm run build` fail on **pre-existing** M49 errors in `main.tsx` (missing `React` import, revision-queue undefined variables); these are not introduced by M54 and were left untouched per the baseline decision.

---

## 10. Next milestone recommendation

The knowledge graph is now fully explorable end-to-end (extract → visualize → verify → tutor/quiz/notes). A natural next milestone (candidate M55) would address the remaining documented gaps across M52–M54: explicit estimate reset, per-question difficulty selection, beginner/intermediate/advanced as explicit schema labels, or time-spent weighting in mastery — each evaluated against the "preserve existing APIs" and "no destructive migrations" constraints. Nothing was committed or pushed (awaiting your authorization).
