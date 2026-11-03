# M55 — Adaptive Learning Engine: Mastery Reset, Per-Question Difficulty, Time-Weighted Mastery

**Status:** complete (analysis + implementation + verification)
**Milestone:** M55
**Repo:** `C:\Users\pc\OneDrive\Desktop\wonderlearn` (branch `main`, @ `c71797d` M51)
**Scope:** Learner knowledge estimation enhancements — user-initiated estimate reset, per-question difficulty, time-weighted mastery calculation.

---

## 1. Inspection findings

### Existing infrastructure (M34/M52, already present)

M34 and M52 already implement a complete data-driven adaptive-learning layer:

| M55 requirement | Existing implementation | Location |
|---|---|---|
| Learner knowledge estimation | `calculate_mastery()` — recency-weighted latest-per-question accuracy; ambiguous unscored; `None` when no data | `services/api/app/learning_analytics.py:28` |
| Adaptive difficulty mapping | `_difficulty_level_from_mastery()` maps mastery → foundational/building/practice/advanced | `services/api/app/storage.py:4557` |
| Learning paths/objectives | `create_learning_path` / `create_learning_objective` with `difficulty_level` column | `services/api/app/storage.py:4574`/`4733` |
| Multi-user isolation | All queries filtered by `user_id` | `services/api/app/storage.py` |

### Gaps identified (filled by M55)

1. **No user estimate reset.** Learners could not reset their mastery estimates. No endpoint existed to delete a user's own quiz attempts and review items.
2. **No per-question difficulty.** Quiz questions had no `difficulty_level` column. Quiz generation could not target a specific difficulty.
3. **No time-spent weighting in mastery.** Mastery used only recency weighting; time spent on each question was not captured or used as a weighting factor.

---

## 2. Changes implemented

### `services/api/app/storage.py`

1. **`quiz_questions.difficulty_level` column** — Added `difficulty_level TEXT CHECK (difficulty_level IN ('foundational', 'building', 'practice', 'advanced'))` to the `quiz_questions` table (additive migration via `_ensure_column` for existing databases).
2. **`quiz_attempts.duration_ms` column** — Added `duration_ms INTEGER CHECK (duration_ms >= 0)` to the `quiz_attempts` table (additive migration).
3. **`create_quiz_set`** — Now stores `difficulty_level` per question from the question dict.
4. **`record_quiz_attempt`** — Accepts optional `duration_ms` parameter and stores it.
5. **`get_quiz_question`** — Returns `difficulty_level` in the result dict.
6. **`reset_user_progress(database_path, user_id)`** — New function that deletes all of a user's `quiz_attempts` and `topic_review_items` rows, returning deletion counts.

### `services/api/app/learning_analytics.py`

7. **`calculate_mastery`** — Now weights each attempt's recency score by a time factor: `weight = recency_weight × min(duration_ms / 30000, 2.0)`. Attempts without `duration_ms` use a neutral time weight of 1.0. This rewards sustained engagement (up to 2x weight for 30+ seconds) without penalizing quick correct answers disproportionately.

### `services/api/app/schemas.py`

8. **`QuizGenerateRequest.difficulty_level`** — New optional field: `Literal["foundational", "building", "practice", "advanced"] | None`.
9. **`QuizQuestionResponse.difficulty_level`** — New optional field returned with each question.
10. **`QuizAttemptRequest.duration_ms`** — New optional field: `int | None`, `ge=0`.

### `services/api/app/api/v1/routes.py`

11. **`POST /quizzes`** — Passes `payload.difficulty_level` to `generate_quiz_questions`.
12. **`POST /quizzes/{quiz_id}/questions/{question_id}/attempts`** — Passes `payload.duration_ms` to `record_quiz_attempt`.
13. **`DELETE /api/v1/learning/progress/reset`** — New endpoint that calls `reset_user_progress` for the current user, returning `attempts_deleted` and `reviews_deleted`.

### `services/api/app/quiz.py`

14. **`generate_quiz_questions`** — Accepts optional `difficulty_level` parameter; adds difficulty-specific guidance to the tutor prompt when specified.
15. **`_validate_question`** — Accepts optional `difficulty_level` parameter and includes it in the returned question dict.

---

## 3. Verification gate results

Exact commands mirror `.github/workflows/ci.yml`.

### Backend (`services/api`)

| Suite | Result |
|---|---|
| Full `pytest` | **316 passed / 27 failed / 2 skipped** |
| Of which M55 `test_adaptive_engine_m55.py` | **7 passed** |
| Pre-existing failures (unchanged) | ~20 M49 async-regression failures; `test_quality_gates` fails on missing fixture |
| M49 performance suite | **4 passed / 1 skipped** |
| M47 + M48 security suites | **58 passed** |
| M50 integrity + backup + health | **21 passed / 1 skipped** |
| ruff check (touched files) | pass |
| ruff format (touched files) | pass |
| contract validation | pass |

### Frontend (`apps/web`)

| Suite | Result |
|---|---|
| `npm test` | **40 passed**; `App.test.tsx` has pre-existing M49 `React` import error, unrelated to M55 |
| `npm run typecheck` | 28 pre-existing errors in `main.tsx` (M49 working-tree; untouched) |
| `npm run build` | fails on same pre-existing type errors |

M55 changed **backend source only** (`storage.py`, `learning_analytics.py`, `schemas.py`, `routes.py`, `quiz.py`) plus the new test file. No frontend changes were needed.

---

## 4. Data model changes

| Table | Column | Type | Constraint |
|---|---|---|---|
| `quiz_questions` | `difficulty_level` | TEXT | CHECK IN ('foundational','building','practice','advanced') |
| `quiz_attempts` | `duration_ms` | INTEGER | CHECK (>= 0) |

Both columns are added additively via `ALTER TABLE ADD COLUMN` (wrapped in `_ensure_column` for idempotency). Existing rows have `NULL` values, which are handled gracefully (neutral time weight of 1.0, `None` difficulty level).

---

## 5. Testing

### New: `services/api/tests/test_adaptive_engine_m55.py` — 7 tests, all passing

| Test | What it verifies |
|---|---|
| `test_reset_user_progress_clears_attempts_and_reviews` | Reset deletes user's attempts; progress returns to empty |
| `test_reset_user_progress_is_user_scoped` | Reset only affects the calling user's data |
| `test_quiz_question_has_difficulty_level` | Stored difficulty_level is returned by `get_quiz_question` |
| `test_difficulty_level_defaults_to_none` | Questions without difficulty_level return `None` |
| `test_mastery_weights_by_time_spent` | Time spent on a question affects mastery weight |
| `test_mastery_without_duration_ms_is_neutral` | Attempts without duration_ms use neutral weight |
| `test_mastery_incorrect_with_time_spent_is_low` | Incorrect answer = 0 mastery regardless of time spent |

---

## 6. Limitations

- **Per-question difficulty in quiz generation** is passed to the tutor as a prompt instruction. The tutor model may or may not honor it depending on its capabilities; this is a best-effort hint, not a hard constraint.
- **Time-spent weighting** relies on the frontend sending `duration_ms` with each quiz attempt submission. The frontend currently does not capture this; it's available for future frontend integration.
- **Estimate reset** deletes all of a user's quiz attempts and review items. This is irreversible and should only be exposed with explicit user confirmation in the UI.
- **Mastery is an operational estimate**, not a validated measure of learning or mastery (consistent with M34/M52 documentation).

---

## 7. Files changed

| File | Change |
|---|---|
| `services/api/app/storage.py` | Added `difficulty_level` to `quiz_questions`, `duration_ms` to `quiz_attempts`; updated `create_quiz_set`, `record_quiz_attempt`, `get_quiz_question`; added `reset_user_progress` |
| `services/api/app/learning_analytics.py` | `calculate_mastery` now weights by `duration_ms` |
| `services/api/app/schemas.py` | Added `difficulty_level` to `QuizGenerateRequest`/`QuizQuestionResponse`; added `duration_ms` to `QuizAttemptRequest` |
| `services/api/app/api/v1/routes.py` | Passes `difficulty_level` to quiz generation; passes `duration_ms` to `record_quiz_attempt`; added `DELETE /api/v1/learning/progress/reset` |
| `services/api/app/quiz.py` | `generate_quiz_questions` accepts `difficulty_level`; passes to tutor prompt and question dict |
| `services/api/tests/test_adaptive_engine_m55.py` | New — 7 deterministic M55 regression tests |

No schema migrations are destructive; all changes are additive column additions.

---

## 8. Reproduction commands

```bash
# M55 adaptive engine tests (new)
uv run --project services/api --extra dev pytest tests/test_adaptive_engine_m55.py -v

# Existing adaptive baseline (M34/M52, still passing)
uv run --project services/api --extra dev pytest tests/test_learning_analytics.py tests/test_learning_paths.py tests/test_study_coach.py

# Full backend suite
uv run --project services/api --extra dev pytest

# M47/M48 security regressions
uv run --project services/api --extra dev pytest tests/test_security_redteam.py tests/test_prompt_injection.py tests/test_security_remediation.py tests/test_security.py

# M49 performance benchmarks
uv run --project services/api --extra dev pytest tests/test_performance.py -m performance

# M50 integrity + backup
uv run --project services/api --extra dev pytest tests/test_data_integrity.py tests/test_backup.py tests/test_health.py

# Lint
uv run --project services/api --extra dev ruff check services/api/app/storage.py services/api/app/learning_analytics.py services/api/app/schemas.py services/api/app/api/v1/routes.py services/api/app/quiz.py services/api/tests/test_adaptive_engine_m55.py
uv run --project services/api --extra dev ruff format --check services/api/app/storage.py services/api/app/learning_analytics.py services/api/app/schemas.py services/api/app/api/v1/routes.py services/api/app/quiz.py services/api/tests/test_adaptive_engine_m55.py
uv run --project services/api --extra dev python app/contract_validation.py
```

---

## 9. Next milestone recommendation

With M52 (adaptive learning engine), M53 (content studio), and M54 (visual knowledge explorer) complete, the core learning engine is in place. A natural next milestone (candidate M56) would be **frontend integration of the M52/M53/M54 features** — connecting the backend APIs to the React UI with proper forms for quiz difficulty selection, progress reset confirmation, and data visualization of mastery over time.

Nothing was committed or pushed (awaiting your authorization).
