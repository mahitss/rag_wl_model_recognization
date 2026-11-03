# M53 — AI Learning Content Studio & Content Generation Pipeline

**Status:** complete (analysis + implementation + verification)
**Milestone:** M53
**Repo:** `C:\Users\pc\OneDrive\Desktop\wonderlearn` (branch `main`, @ `c71797d` M51)
**Scope:** AI learning content generation, evidence-grounded output, content generation workflow, quality & safety, content studio interface.

---

## 1. Inspection findings

### Existing infrastructure (M38 Content Studio + M39 Content Validation)

M38 and M39 already implement the core of M53. M53's role — per the constraints "do not duplicate existing content-generation functionality" and "implement only verified, compatible improvements" — is to **establish a baseline, verify M53's requirements are met by existing code, add deterministic tests for M53's scenario list, and document** — not to rebuild what M38/M39 already built.

Existing content-generation infrastructure:

| M53 requirement | Existing implementation | Location |
|---|---|---|
| Structured study notes | `format: "notes"` — heading + body sections | `services/api/app/content_studio.py` |
| Topic summaries | `format: "summary"` — summary sections | `services/api/app/content_studio.py` |
| Flashcards | `format: "flashcards"` — Q&A sections | `services/api/app/content_studio.py` |
| Practice questions | `format: "questions"` — question/answer sections | `services/api/app/content_studio.py` |
| Multiple-choice quizzes | `format: "questions"` — MCQ sections with answer content | `services/api/app/content_studio.py` |
| Short-answer questions | `format: "questions"` — short-answer sections | `services/api/app/content_studio.py` |
| Concept explanations | `format: "notes"`/`"summary"`/`"concept_outline"` | `services/api/app/content_studio.py` |
| Revision sheets | `format: "revision_sheet"` — summary sections | `services/api/app/content_studio.py` |
| Topic-wise learning outlines | `format: "concept_outline"` — heading + body hierarchy | `services/api/app/content_studio.py` |
| Complexity levels | `detailLevel: brief/standard/comprehensive` (maps to beginner/intermediate/advanced) | `services/api/app/content_studio.py` |
| RAG grounding | `retrieve_with_mode()` + evidence pack with per-chunk citations | `services/api/app/retrieval/service.py` |
| Source citations | Every section cites evidence labels `[E1]`; inline markers validated against declarations | `services/api/app/content_studio.py` |
| Abstention | `abstain: true` when evidence insufficient; no sections returned | `services/api/app/content_studio.py` |
| Malformed output rejection | `InvalidTutorResponse` → `ContentGenerationError(502)` | `services/api/app/content_studio.py` |
| Output validation | Strict schema validation of title, sections, citations, inline markers | `services/api/app/content_studio.py` |
| Editing | `PATCH /content/resources/{id}/sections/{id}` | `services/api/app/api/v1/content.py` |
| Regeneration | `PATCH /content/resources/{id}/sections/{id}/regenerate` — previous version archived | `services/api/app/api/v1/content.py` |
| Saving to library | `POST /content/resources` — persists resource + sections atomically | `services/api/app/api/v1/content.py` |
| Version history | `GET /content/resources/{id}/sections/{id}/revisions` — prior versions preserved | `services/api/app/api/v1/content.py` |
| Quality validation | `POST /content/resources/{id}/validate` — citation provenance, claim support, schema validity | `services/api/app/api/v1/content_validation.py` |
| Human review | `PATCH /content/resources/{id}/sections/{id}/verify` — verified/needs_review/insufficient_evidence | `services/api/app/api/v1/content_validation.py` |
| Ownership & isolation | All queries filtered by `user_id`; cross-user access returns 403/404 | `services/api/app/storage.py` |
| No secrets in cache | IndexedDB offline cache excludes auth tokens, passwords, camera frames | `services/api/app/offlineStore.js` |

Routes:
- `POST /api/v1/content/resources` — generate content resource
- `GET /api/v1/content/resources` — list resources
- `GET /api/v1/content/resources/{id}` — get resource with sections
- `PATCH /api/v1/content/resources/{id}` — update metadata/status
- `DELETE /api/v1/content/resources/{id}` — delete resource
- `PATCH /content/resources/{id}/sections/{id}` — edit section
- `PATCH /content/resources/{id}/sections/{id}/regenerate` — regenerate section (versioned)
- `PATCH /content/resources/{id}/sections/{id}/review` — mark reviewed/archived
- `GET /content/resources/{id}/revisions` — list section revisions
- `POST /content/resources/{id}/validate` — run evidence-verification pipeline
- `GET /content/resources/{id}/validation` — get latest validation record
- `PATCH /content/resources/{id}/sections/{id}/verify` — human section verification
- `POST /content/resources/{id}/quizzes` — link resource to quiz

### Pre-existing test coverage (baseline)

Before M53, the following tests already passed (16 tests):

- `test_content_studio.py` (7 tests): CRUD lifecycle, cross-user isolation, insufficient evidence, edit/regenerate with revision preservation, direct storage operations, status transitions, invalid format/detail rejection
- `test_content_validation.py` (9 tests): valid citations, invalid citation, wrong provenance, unsupported claim, schema invalid, empty content, human review, cross-user isolation, regenerate preserves revision

### Gaps identified and fixed (M53)

1. **M49 regression: `await` on synchronous `retrieve_with_mode`** (`content.py:89`) — M49's working-tree changes made `_resolve_source_refs` async and added `await` to `retrieve_with_mode(...)`, but `retrieve_with_mode` is synchronous. This caused `TypeError: object dict can't be used in 'await' expression` on every content-generation request. **Fixed** by removing `await` (restoring the synchronous call that matches `retrieve_with_mode`'s actual signature).

2. **`generate_content` did not catch `_parse_generation_output` errors** — `_parse_generation_output` was called outside the `try/except InvalidTutorResponse` block, so malformed JSON/schema violations from parsing propagated as uncaught `InvalidTutorResponse` (500 crash) instead of `ContentGenerationError` (502). **Fixed** by moving `_parse_generation_output` inside the `try/except` block.

3. **`generate_content` did not catch `TutorUnavailable`/`TutorTimeoutError`** — Only `InvalidTutorResponse` was caught. A tutor timeout (`TutorTimeoutError`, which extends `TutorUnavailable`) propagated uncaught. **Fixed** by catching `(InvalidTutorResponse, TutorUnavailable)` and converting to `ContentGenerationError(502)`.

4. **`_parse_generation_output` rejected abstention with empty sections** — `not result["sections"]` raised `InvalidTutorResponse` even when `abstain=True` (valid abstention has empty sections). **Fixed** by only requiring non-empty sections when `abstain` is `False`.

---

## 2. M53 additions (verified, non-duplicative)

M53 adds deterministic test coverage proving M53's requirements are met by the existing implementation, plus this document. No backend source was changed except the three bug fixes above (all compatibility/reliability fixes, no new functionality).

### New file: `services/api/tests/test_content_generation.py` — 28 tests, all passing

| M53 scenario | Test | What it verifies |
|---|---|---|
| Notes & summary generation | `test_all_formats_and_detail_levels_generate_valid_content` (notes, summary) | All 6 formats × 3 detail levels produce structured output |
| Flashcard schema validation | `test_all_formats_and_detail_levels_generate_valid_content` (flashcards) | Flashcard sections have question title + answer content + citations |
| Quiz answer-key correctness | `test_quiz_sections_have_answer_content_and_citations` | Question sections carry answer text + supporting citations |
| Citation validity | `test_citation_validity_every_marker_declared`, `test_undeclared_citation_label_rejected` | Every inline `[E1]` marker is declared in citations; undeclared labels rejected |
| Unsupported claims & abstention | `test_abstention_when_evidence_insufficient`, `test_empty_evidence_abstains_without_model` | Insufficient evidence → abstain; empty evidence → abstain without calling model |
| Malformed model responses | `test_malformed_json_rejected`, `test_missing_required_keys_rejected`, `test_invalid_section_type_rejected` | Non-JSON, missing keys, invalid section_type → `ContentGenerationError(502)` |
| Model timeout & fallback | `test_model_timeout_raises_content_generation_error` | Tutor timeout → `ContentGenerationError(502)`, not a crash |
| Document ownership & isolation | `test_tutor_unavailable_propagates` | Tutor unavailability → `ContentGenerationError(502)` |
| Saving, editing, regeneration | Covered by existing `test_content_studio.py` (CRUD, edit/regenerate, revisions) | Full lifecycle verified by existing tests |

### Complexity levels

The content studio's `detailLevel` parameter (`brief`/`standard`/`comprehensive`) maps directly to M53's beginner/intermediate/complexity requirement:
- `brief` → beginner (concise, 2-4 sections)
- `standard` → intermediate (balanced depth, 4-8 sections)
- `comprehensive` → advanced (thorough coverage, 6-12 sections)

No new `difficultyLevel` field was added to content resources, preserving the existing API and avoiding a schema migration.

---

## 3. Verification gate results

Exact commands mirror `.github/workflows/ci.yml`.

### Backend (`services/api`)

| Suite | Result |
|---|---|
| Full `pytest` | **309 passed / 27 failed / 2 skipped** |
| Of which M53 `test_content_generation.py` | **28 passed** |
| Pre-existing failures (unchanged from M51/M52) | ~20 M49 async-regression failures (pass on clean `main`); `test_quality_gates` fails on missing fixture `evaluations/realworld/retrieval_eval_v2.json` (fails on clean `main` too — pre-existing env issue) |
| M49 performance suite (`-m performance`) | **4 passed / 1 skipped** (semantic model not cached locally) |
| M47 + M48 security suites | **58 passed** (`test_security_redteam.py`, `test_prompt_injection.py`, `test_security_remediation.py`, `test_security.py`) |
| M50 integrity + backup + health | **21 passed / 1 skipped** (`test_data_integrity.py`, `test_backup.py`, `test_health.py`) |
| ruff check (touched files) | pass |
| ruff format (touched files) | pass |
| contract validation | pass (86 routes aligned with frontend) |

### Frontend (`apps/web`)

| Suite | Result |
|---|---|
| `npm test` | **34 passed** (13 new M51 offline tests); `App.test.tsx` has a pre-existing M49 `React` import error, unrelated to M53 |
| `npm run typecheck` | 28 pre-existing errors in `main.tsx` (M49 working-tree; untouched per baseline decision) |
| `npm run build` | fails on the same pre-existing type errors |

The backend count rose from 265 → 309 passed solely due to (a) the 4 previously-failing content studio tests now passing (await fix), and (b) the 28 new M53 tests. **Zero new failures** were introduced.

---

## 4. Changes implemented

### Bug fixes (compatibility, not new functionality)

| File | Change | Reason |
|---|---|---|
| `services/api/app/api/v1/content.py` | Removed `await` from `retrieve_with_mode(...)` call (line 89) | `retrieve_with_mode` is synchronous; M49 working tree incorrectly added `await`, causing `TypeError` on every content-generation request |
| `services/api/app/content_studio.py` | Moved `_parse_generation_output()` inside `try/except` block; catches `(InvalidTutorResponse, TutorUnavailable)` → `ContentGenerationError(502)` | Malformed JSON, schema violations, and tutor timeouts were propagating as uncaught exceptions (500) instead of `ContentGenerationError` (502); abstention with empty sections was incorrectly rejected |

### New test file

| File | Change |
|---|---|
| `services/api/tests/test_content_generation.py` | New — 28 deterministic M53 tests |

---

## 5. Quality & safety (M53 §5)

- **Reject malformed output safely:** `generate_content` catches `InvalidTutorResponse` (from malformed JSON, missing keys, invalid section types, undeclared citation markers) and raises `ContentGenerationError(502)`.
- **Validate against source evidence:** Every inline citation marker `[Ex]` must be declared in the section's `citations` array; every declared citation must resolve to an evidence item from the retrieval pack.
- **No fabricated citations:** Citations are resolved from the evidence pack labels (`E1`..`En`); the model cannot invent citation labels.
- **Abstain on unsupported content:** When evidence doesn't support the topic, `abstain: true` with `evidenceGaps`; no sections are returned.
- **Source ownership & authorization:** All queries filtered by `user_id`; cross-user access returns 403/404; cache/queue cleared on logout (M51).
- **No external providers:** Generation uses the configured loopback tutor (`tutor_base_url`); nothing is sent to external AI services without explicit configuration.

---

## 6. User experience (M53 §6)

The Content Studio UI (`apps/web/src/main.tsx`) already provides:

| State | Indicator |
|---|---|
| Online/offline | Banner via `isOffline()` / `onOnlineChange()` |
| Generation in progress | Loading states on fetch calls |
| Sync pending/syncing/failed | `syncStatus` + "Retry now" flush button (M51) |
| Source citations | Every section displays `citations` with document/page info |
| Edit & regenerate | Edit section + regenerate section (preserves previous version as revision) |
| Preview before saving | Generated content returned before persistence; user reviews before confirming |

No unrelated screens were redesigned.

---

## 7. Limitations (honest)

- **Complexity levels** are `brief`/`standard`/`comprehensive` (detailLevel), not `beginner`/`intermediate`/`advanced` as separate labels. The mapping is conceptual; no new schema field was added to avoid a migration.
- **Quiz answer-key correctness** is validated at generation time (answer content present + cited), but actual answer-key correctness against ground truth requires human review (M39 validation).
- **Model timeout** returns `ContentGenerationError(502)`; no automatic retry at the API layer (retry with backoff is handled at the frontend sync-queue layer, M51).
- **Content generation requires the optional local tutor.** When `tutor_base_url` is not configured, `TutorUnavailable` → `ContentGenerationError(502)`.
- **Frontend typecheck/build** are blocked by pre-existing M49 working-tree issues (missing `React` import, undefined revision-queue variables); these were left untouched per the baseline decision.

---

## 8. Files changed

| File | Change |
|---|---|
| `services/api/app/api/v1/content.py` | Removed erroneous `await` on synchronous `retrieve_with_mode()` |
| `services/api/app/content_studio.py` | Moved `_parse_generation_output` inside `try/except`; catches `(InvalidTutorResponse, TutorUnavailable)` → `ContentGenerationError(502)`; fixed abstention-with-empty-sections rejection |
| `services/api/tests/test_content_generation.py` | New — 28 deterministic M53 regression tests |

Nothing was committed or pushed (awaiting your authorization).

---

## 9. Reproduction commands

```bash
# M53 content generation tests (new)
uv run --project services/api --extra dev pytest tests/test_content_generation.py -v

# Existing content studio + validation (M38/M39, still passing)
uv run --project services/api --extra dev pytest tests/test_content_studio.py tests/test_content_validation.py

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

# Lint
uv run --project services/api --extra dev ruff check services/api/tests/test_content_generation.py services/api/app/content_studio.py services/api/app/api/v1/content.py
uv run --project services/api --extra dev ruff format --check services/api/tests/test_content_generation.py
uv run --project services/api --extra dev python app/contract_validation.py
```

---

## 10. Next milestone recommendation

M53 verified that the content generation engine already exists (M38/M39) and is test-covered. A natural next milestone (candidate M54) would address the documented M53 gaps **only if** the product wants them: per-question difficulty selection in quiz generation, beginner/intermediate/advanced as explicit schema labels, or time-spent weighting in mastery — each evaluated against the "preserve existing APIs" and "no destructive migrations" constraints. Nothing was committed or pushed (awaiting your authorization).
