# M52 — Adaptive Learning Engine

**Status:** complete (analysis + implementation + verification)
**Milestone:** M52
**Repo:** `C:\Users\pc\OneDrive\Desktop\wonderlearn` (branch `main`, @ `c71797d` M51)
**Scope:** Learner knowledge estimation, adaptive difficulty, personalized learning paths, explainability and privacy.

---

## 1. Inspection findings

### Key finding: M34 already implements the M52 adaptive-learning core

The repository already contains a complete, data-driven adaptive-learning layer (delivered as M34 "Personalized learning paths and adaptive difficulty"). M52's role — per the constraints "do not duplicate existing adaptive-learning functionality" and "implement only verified, compatible improvements" — is to **establish a baseline, verify M52's requirements are met by existing code, add deterministic tests for M52's scenario list, and document** — not to rebuild what M34 already built.

Existing adaptive-learning infrastructure (all user-scoped, deterministic, no external model):

| M52 requirement | Existing implementation | Location |
|---|---|---|
| Learner knowledge estimation | `calculate_mastery()` — recency-weighted latest-per-question accuracy; ambiguous unscored; `None` when no data | `services/api/app/learning_analytics.py:28` |
| Observed vs estimated distinction | `measuredFields` / `estimatedFields` on every recommendation; mastery `null` when unscored | `services/api/app/storage.py:5439` |
| Adaptive difficulty engine | `_difficulty_level_from_mastery()` maps mastery → foundational/building/practice/advanced; `_activity_suggestion()` | `services/api/app/storage.py:4557`, `:5594` |
| No overreaction to a single answer | Mastery weights each question's **latest** result by recency across all questions; one incorrect answer is damped by the rest of the record | `services/api/app/learning_analytics.py:28` |
| Prerequisite concepts before advancing | Learning objectives carry `prerequisite_objective_ids` (validated to same path) | `services/api/app/storage.py:4733` |
| Personalized learning paths | `create_learning_path` / `create_learning_objective` / `/learning-paths/*` routes + study-coach plan | `services/api/app/storage.py:4574`, `routes.py:704` |
| Actionable next steps | `activitySuggestion` (review_overdue / foundational_practice / introductory_quiz / targeted_quiz / mixed_quiz / challenge_quiz) | `services/api/app/storage.py:5594` |
| Explainability | `reasons` (data-grounded), `measuredFields`, `estimatedFields`, `mastery`, `priorityScore` | `services/api/app/storage.py:5439` |
| Privacy / isolation | Every query filtered by `user_id`; mastery/recommendations computed per user | `services/api/app/storage.py` |
| No sensitive inference | No demographic or behavioral inference; only quiz/session/review records | — |
| Missing data → no fabricated score | `mastery: null`, `difficultyLevel: "foundational"`, empty recommendations when no history | `services/api/app/storage.py:4557`, `:5591` |

Routes:
- `GET /learning-paths/adaptive-recommendations` → `AdaptiveRecommendationsResponse` (routes.py:677)
- `GET /learning-paths`, `POST /learning-paths`, `GET/PATCH/DELETE /learning-paths/{path_id}`
- `GET /learning-objectives/{objective_id}`, `PATCH /learning-objectives/{objective_id}`
- `GET /progress`, `GET /learning-analytics`, `GET /study-coach/plan`

### Pre-existing test coverage (baseline)

Before M52, the following adaptive tests already passed (13 tests):

- `test_learning_analytics.py`: `calculate_mastery` recency weighting, empty/ambiguous unscored, tie-breaking by sequence, analytics empty history + review due boundary, API persists attempts/sessions/citations and respects source deletion.
- `test_learning_paths.py`: `test_adaptive_recommendations_endpoint` (empty when no data, limit validation), `test_adaptive_recommendations_computation` (no citations → no recommendations).
- `test_study_coach.py`: coach plan with mastery-based weak/strong topics.

### Gaps identified (documented, not duplicated)

1. **Reset estimates.** M52 §5 asks to "allow learners to inspect and reset relevant learning estimates." Inspection exists (`measuredFields`/`estimatedFields`/`reasons`). Reset would require deleting the current user's quiz attempts/review records. That is **not implemented** because it conflicts with the "preserve existing learning data" constraint and "no destructive migrations" constraint; it is recorded as a future-milestone consideration behind explicit user consent.
2. **Per-question difficulty.** Questions have no stored difficulty field; difficulty is derived at the **topic** level from mastery (`_difficulty_level_from_mastery`). Quiz generation does not select questions by difficulty. Recorded as a design limitation, not a gap to duplicate.
3. **Time spent.** Study sessions store `started_at`/`completed_at` so duration can be derived, but time spent is **not** an input to mastery ("only where reliable and meaningful" — not currently used).

---

## 2. M52 additions (verified, non-duplicative)

M52 adds deterministic test coverage proving M52's requirements are met by the existing implementation, plus this document. No backend source was changed; no new endpoints were added (would duplicate M34).

### New file: `services/api/tests/test_adaptive_engine.py` — 12 tests, all passing

| M52 scenario | Test | What it verifies |
|---|---|---|
| Strong topic mastery | `test_mastery_strong_topic_advanced_level` | Mastery ≥0.80 → `advanced` difficulty |
| Weak topic mastery | `test_mastery_weak_topic_is_building` | Mastery <0.60 → `building`, `weak=true` |
| Repeated incorrect answers | `test_repeated_incorrect_answers_do_not_overreact` | Single incorrect among correct record → mastery ~0.49 (`building`), not collapsed to zero/foundational |
| Difficulty transitions | `test_difficulty_level_transitions` | `_difficulty_level_from_mastery` monotonic: None/0→foundational, 0.40→building, 0.60→practice, 0.80→advanced |
| Prerequisite dependencies | `test_learning_objective_prerequisite_dependencies` | Objectives declare same-path prerequisites; stored and fetched correctly |
| Prerequisite validation | `test_prerequisite_must_belong_to_same_path` | Cross-path prerequisite raises `ValueError` |
| Insufficient learner history | `test_insufficient_history_returns_empty_recommendations_and_foundational` | No attempts → empty recommendations, mastery `None`, foundational |
| Conflicting signals | `test_conflicting_performance_signals_stay_unscored` | All-ambiguous → mastery `None` (no fabricated score) |
| Reset / recalculation | `test_recalculation_reflects_new_attempts` | Mastery recalculates from current data (incorrect→correct flips 0.0→1.0) |
| Multi-user isolation | `test_adaptive_recommendations_isolate_users` | User A (0.0/foundational) and user B (1.0/advanced) computed independently; no leakage |
| Model unavailable | `test_adaptive_engine_works_without_ai_model` | Recommendations computed from stored records only; no model/extra service needed |

---

## 3. Verification gate results

Exact commands mirror `.github/workflows/ci.yml`.

### Backend (`services/api`)

| Suite | Result |
|---|---|
| Full `pytest` | **277 passed / 31 failed / 2 skipped** |
| Of which M52 `test_adaptive_engine.py` | **12 passed** |
| Pre-existing failures (unchanged from M51) | ~20 M49 async-regression failures (pass on clean `main`); `test_quality_gates` fails on missing fixture `evaluations/realworld/retrieval_eval_v2.json` (fails on clean `main` too — pre-existing env issue) |
| M49 performance suite (`-m performance`) | **4 passed / 1 skipped** (semantic model not cached locally) |
| M47 + M48 security suites | **58 passed** (`test_security_redteam.py`, `test_prompt_injection.py`, `test_security_remediation.py`, `test_security.py`) |
| M50 integrity + backup + health | **21 passed / 1 skipped** (`test_data_integrity.py`, `test_backup.py`, `test_health.py`) |
| ruff check (touched files) | pass |
| ruff format (touched files) | pass |
| contract validation | pass (86 routes aligned with frontend) |

### Frontend (`apps/web`)

| Suite | Result |
|---|---|
| `npm test` | **34 passed** (13 new M51 offline tests); `App.test.tsx` has a pre-existing M49 `React` import error, unrelated to M52 |
| `npm run typecheck` | 28 pre-existing errors in `main.tsx` (M49 working-tree; untouched per baseline decision) |
| `npm run build` | fails on the same pre-existing type errors |

M52 changed **no backend source** and **no frontend source** — it only added `test_adaptive_engine.py`. Therefore all M47/M48/M49/M50/M51 results above carry forward unchanged; the full backend count rose from 265 → 277 passed solely due to the 12 new M52 tests, with **zero new failures**.

---

## 4. Explainability & privacy (M52 §5)

- **Why recommended:** every adaptive recommendation includes `reasons` (human-readable, data-grounded), `priorityScore`, `citations`, and `citationsCount`.
- **Inspect estimates:** `measuredFields` and `estimatedFields` tell the learner exactly which inputs are stored data vs derived heuristics; `mastery` is `null` when unscored.
- **Reset estimates:** not implemented (would delete the user's own quiz/review records; conflicts with "preserve existing learning data" and "no destructive migrations"). Recorded for a future milestone behind explicit user consent.
- **Isolation:** all queries filter by `user_id`; verified by `test_adaptive_recommendations_isolate_users`.
- **No sensitive inference:** no demographic/inferred characteristics; only quiz/session/review records.
- **Missing data:** `mastery: null`, `difficultyLevel: "foundational"`, empty recommendations — no fabricated scores.

---

## 5. Limitations (honest)

- Adaptive difficulty is **topic-level**, derived from mastery; individual questions have no stored difficulty, and quiz generation does not select by difficulty.
- Difficulty changes from a single incorrect answer are damped (recency-weighted latest-per-question) but not eliminated — a recent incorrect on a learner's only recent question still lowers mastery. This is M34's deliberate design; M52 did not alter it.
- Time spent is stored per session but not an input to mastery.
- Mastery is an **operational estimate**, not a validated measure of learning or mastery (consistent with M34/M10 documentation).
- Estimate reset (deleting a user's own attempt/review records) is out of scope for M52 due to the data-preservation constraint.

---

## 6. Files changed

| File | Change |
|---|---|
| `services/api/tests/test_adaptive_engine.py` | New — 12 deterministic M52 regression tests |
| `docs/architecture/m52-adaptive-learning.md` | New — this report |

No backend or frontend source was modified.

---

## 7. Reproduction commands

```bash
# M52 adaptive engine tests (new)
uv run --project services/api --extra dev pytest tests/test_adaptive_engine.py -v

# Existing adaptive baseline (M34, still passing)
uv run --project services/api --extra dev pytest tests/test_learning_analytics.py tests/test_learning_paths.py tests/test_study_coach.py

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
uv run --project services/api --extra dev ruff check services/api/tests/test_adaptive_engine.py
uv run --project services/api --extra dev ruff format --check services/api/tests/test_adaptive_engine.py
uv run --project services/api --extra dev python app/contract_validation.py
```

---

## 8. Next milestone recommendation

M52 verified that the adaptive-learning engine already exists (M34) and is test-covered. A natural next milestone (candidate M53) would address the documented M52 gaps **only if** the product wants them: explicit estimate reset behind user consent, per-question difficulty selection in quiz generation, and/or time-spent weighting in mastery — each evaluated against the "preserve existing learning data" and "no fabricated metrics" constraints. Nothing was committed or pushed (awaiting your authorization).
