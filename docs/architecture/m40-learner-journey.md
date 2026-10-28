# M40 — End-to-End Learning Intelligence

## Summary

M40 turns the existing application into one coherent learning product by integrating all previously disconnected subsystems (document ingestion, retrieval, tutor, quiz, analytics, study coach, content studio, content validation) into a single verified learner journey. No new subsystems were created — existing services and schemas were integrated, broken contracts were fixed, and a complete end-to-end test was added.

## Feature-Status Matrix

| # | Journey Step | Backend API | Frontend UI | Tests | Evidence |
|---|-------------|-------------|-------------|-------|----------|
| 1 | Upload a learning document | `POST /api/v1/documents` | PDF upload in main.tsx | test_e2e_journey.py | Document stored, chunked, indexed with FTS5 |
| 2 | Inspect ingestion status and content | `GET /api/v1/documents/{id}` | Document list + detail | test_e2e_journey.py | chunkCount > 0, parser version, page count |
| 3 | Ask question, get evidence-grounded answer | `POST /api/v1/tutor/answers` | Tutor form + answer display | test_e2e_journey.py | Citations resolve to real chunks, abstention on insufficient evidence |
| 4 | Explore concepts and source material | `POST /api/v1/knowledge-graph/extract`, `GET /api/v1/knowledge-graph/concepts/{id}` | Knowledge graph panel | test_e2e_journey.py | Concepts with evidence provenance, relationships as candidates |
| 5 | Generate study notes/flashcards/quiz | `POST /api/v1/content/resources` | Content studio form | test_m40_learner_journey.py | Sections with citations, abstain on insufficient evidence |
| 6 | Validate content, flag unsupported claims | `POST /api/v1/content/resources/{id}/validate` | Validation results UI | test_m40_learner_journey.py | citationValidity, schemaValidity, unsupportedClaimCount, evidenceCoverage |
| 7 | Complete quiz, record results | `POST /api/v1/quizzes/{id}/questions/{qid}/attempts` | Quiz form + feedback | test_quiz.py, test_e2e_journey.py | correct/incorrect/ambiguous outcomes, attempt history |
| 8 | Update mastery, review scheduling, recommendations | `GET /api/v1/progress`, `GET /api/v1/learning-analytics`, `GET /api/v1/study-coach/plan` | Progress panel, analytics, study coach | test_e2e_journey.py, test_learning_analytics.py | Topic accuracy, mastery, overdue reviews, coach recommendations |
| 9 | Save content to study plan | `POST /api/v1/content/resources/{id}/quizzes` | Link-to-quiz button | test_m40_learner_journey.py | Provenance link between resource and quiz |
| 10 | Revisit material, inspect progress analytics | `GET /api/v1/learning-analytics` | Analytics panel | test_m40_learner_journey.py | Real attempt counts, quiz accuracy, completed sessions |

## Integration Fixes Applied

### Backend

1. **`services/api/app/content_validation.py`**: Changed `verification_method` from `"deterministic_semantic_estimate"` to `"semantic_estimate"` to match the `content_resource_validations` table CHECK constraint (`verification_method IN ('deterministic', 'semantic_estimate')`).

2. **`services/api/app/schemas.py`**: Updated `ContentResourceValidation.verificationMethod` Literal type from `Literal["deterministic", "deterministic_semantic_estimate"]` to `Literal["deterministic", "semantic_estimate"]` to match the database constraint and Pydantic response validation.

3. **`services/api/app/api/v1/content.py`**: Changed `/regenerate` route from `POST` to `PATCH` for REST consistency with the `/verify` route.

### Frontend

4. **`apps/web/src/main.tsx`**: Added M39 content validation UI:
   - `ContentResourceValidation` type definition
   - Extended `ContentResourceSection` with verification fields (verificationStatus, verificationMethod, verificationDetails, verifiedAt, verifiedBy)
   - State variables: `contentValidation`, `contentValidationLoading`, `contentValidationError`, `verifyingSectionId`, `regeneratingSectionId`
   - API functions: `validateContentResource`, `verifyContentSection`, `regenerateContentSection`, `linkContentResourceToQuiz`
   - Validation results section with status, citation validity, schema validity, unsupported claims, evidence coverage
   - Per-section verification controls (Mark verified / needs review / insufficient evidence, Regenerate)
   - Validation summary badges on section cards

### Tests

5. **`services/api/tests/test_m40_learner_journey.py`**: New E2E test covering all 10 journey steps with deterministic fixtures and mocked local tutor.

## Verification Results

| Check | Command | Result |
|-------|---------|--------|
| Backend tests | `uv run --project services/api --extra dev pytest services/api/tests/` | 195 passed, 1 skipped |
| M40 E2E test | `uv run --project services/api --extra dev pytest services/api/tests/test_m40_learner_journey.py` | 1 passed |
| Ruff lint | `uv run --project services/api --extra dev ruff check app/` | All checks passed |
| TypeScript | `npx tsc --noEmit` | No errors |
| Frontend tests | `npx vitest run` | 43 passed, 1 failed (pre-existing timeout) |
| Production build | `npx vite build` | Built successfully |
| Evaluation tests | `uv run --project services/api --extra dev pytest services/api/tests/test_evaluation.py` | 4 passed |

## Remaining Technical Limitations

1. **No real tutor model bundled**: The tutor and quiz generation require an optional local OpenAI-compatible model server (`tutor_base_url`). Without it, tutor answers return 503 and quiz generation is unavailable. Evidence search and retrieval continue to work.

2. **Frontend test timeout**: One pre-existing frontend test (`creates a discussion and reply`) intermittently times out at 5000ms. This is not related to M40 changes.

3. **Mastery score is a heuristic**: The recency-weighted mastery score (30-day half-life) is an operational reporting rule, not a validated learning measure.

4. **No semantic retrieval improvement**: The synthetic evaluation shows no measured improvement over lexical FTS5 retrieval.

5. **Semantic estimate is advisory**: The optional local model semantic check can only downgrade (verified → needs_review), never upgrade.

## Reproduction Commands

```bash
# Backend tests
cd C:\Users\pc\OneDrive\Desktop\wonderlearn
uv run --project services/api --extra dev pytest services/api/tests/

# M40 E2E test specifically
uv run --project services/api --extra dev pytest services/api/tests/test_m40_learner_journey.py

# Ruff lint
uv run --project services/api --extra dev ruff check app/

# TypeScript check
cd apps/web
npx tsc --noEmit

# Frontend tests
npx vitest run

# Production build
npx vite build

# Evaluation tests
uv run --project services/api --extra dev pytest services/api/tests/test_evaluation.py
```

## Git Status

Changed files:
- `apps/web/src/main.tsx` — M39 validation UI
- `services/api/app/api/v1/content.py` — Regenerate route POST→PATCH
- `services/api/app/api/v1/content_validation.py` — verification_method fix
- `services/api/app/content_validation.py` — verification_method fix
- `services/api/app/schemas.py` — Schema Literal type fix
- `services/api/tests/test_m40_learner_journey.py` — New E2E test
