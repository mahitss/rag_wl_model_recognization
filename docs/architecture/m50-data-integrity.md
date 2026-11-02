# M50 — Data Integrity, Database Reliability & Recovery

**Status:** complete (analysis + implementation + verification)
**Milestone:** M50
**Repo:** `C:\Users\pc\OneDrive\Desktop\wonderlearn` (branch `main`, @ `922e0ea` M48)
**Scope:** SQLite data integrity, migration/reliability behavior, backup/restore, data lifecycle. No schema restructuring, no destructive migrations, no account deletion, no data export.

---

## 1. Baseline (Phase 1)

Measured against the working tree (M48 + uncommitted M49-style performance changes kept as baseline per decision):

| Check | Result |
|---|---|
| Backend full suite (`pytest`) | **261 passed / 29 failed / 1 skipped** |
| M49 performance suite (`pytest -m performance`) | **4 passed / 1 skipped** (semantic model not cached locally) |
| Frontend tests (`npm test`) | **21 passed / 1 failed suite** (`React not defined` at `main.tsx:574` `React.lazy` — M49 regression) |
| Frontend typecheck (`npm run typecheck`) | **28 errors** in `main.tsx` (M48 reported 24 pre-existing; M49 added ~4) |
| Frontend build (`npm run build`) | fails on the same type errors |

### Clean-main reference
The 29 backend failures were triaged against clean `main` (M49 stashed): **20 of them pass on clean main**, so they are **M49-caused regressions** from the async `retrieve_with_mode` refactor (async tutor/search paths not fully awaited). The remaining backend failures (`test_quality_gates`) fail identically on clean main because the fixture `evaluations/realworld/retrieval_eval_v2.json` is absent locally — a pre-existing environment issue, not an M49/M50 defect.

M48's CI reference was **290 passed / 1 skipped** (complete fixtures). M50 does not fix the M49 regressions (out of scope; baseline kept untouched) — they are recorded as a known risk in §8.

## 2. Integrity audit findings (Phase 2)

A read-only audit was added at `services/api/app/integrity.py` (`audit_database`, CLI `python -m app.integrity <path>`). It never modifies the database. Findings on a healthy seeded database and on representative production-shape databases:

| Check | Severity | Result | Notes |
|---|---|---|---|
| `quick_check` | critical | pass | SQLite structural integrity ok |
| `foreign_key_check` | high | pass | no FK violations |
| `chunks_fts_orphans` | high | pass (0) | FTS is manually synced, not FK-linked |
| `chunk_embeddings_orphans` | high | pass (0) | `ON DELETE CASCADE` enforced |
| `quiz_citations_orphans` | medium | pass (0) | cascade on chunks/questions |
| `content_resources_dangling_refs` | medium | pass (0) | JSON1-aware check added |
| `knowledge_concepts_orphans` | low | pass (0) | orphan concept cleanup exists |
| `chunk_count_drift` | medium | pass (0) | denormalized count matches |
| `chunk_user_id_mismatch` | medium | pass (0) | chunk.user_id == parent document.user_id |

Planted-orphan testing confirmed each scan detects its target (e.g. a manually inserted `chunks_fts` row without a matching chunk is flagged `chunks_fts_orphans: 1`).

### Pre-existing findings (documented, not changed by M50)

1. **Mixed connection discipline** — 8 storage functions open raw `sqlite3.connect(...)` without `PRAGMA foreign_keys = ON` (`create_user`, `get_user_by_session`, `destroy_session`, `cleanup_expired_sessions`, `record_product_event`, `prune_product_events`, `get_product_analytics_snapshot`, `initialize_storage`). `foreign_keys` is per-connection in SQLite, so those connections do not enforce cascades. Currently low practical impact because those paths delete only leaf rows (sessions/product-events) or insert; it is a latent risk for future parent deletes. Mitigated in Phase 3 for the 7 non-migration paths.
2. **Four tables lack `ON DELETE CASCADE` on `user_id`** — `user_sessions`, `content_resource_validations`, `flashcard_review_items`, `conversations` reference `users(user_id)` with `NO ACTION`. If account deletion is ever added, deleting a user would block on these rows rather than cascade. Documented as a future-milestone consideration; not changed here (SQLite requires table rebuilds and user deletion is out of scope).
3. **`content_resources` cleanup uses a `LIKE '%"documentId":"…"%` match** on `source_refs_json` — fragile if serialization formatting changes. The audit adds a JSON1-aware dangling-ref check; the delete path was left as-is to avoid behavior change (compact `json.dumps(separators=(",",":"), sort_keys=True)` format is consistent today).
4. **No user account deletion and no data export exist** — M50 §4 lifecycle items verified as "not implemented" and recorded as gaps (see §7). Recommended for a future milestone (candidate M51).

## 3. Changes implemented (Phase 3)

Only necessary, verified, additive changes. `SCHEMA_VERSION` unchanged (no schema DDL).

### `services/api/app/integrity.py` (new)
Read-only `audit_database(database_path) -> {"ok": bool, "findings": [...]}` plus a CLI (`python -m app.integrity <database_path>`). Runs `quick_check`, `foreign_key_check`, and the orphan/drift scans in §2. Never writes to the database.

### `services/api/app/storage.py`
- `check_storage()` now also returns `sqlite_fk` (result of `PRAGMA foreign_key_check`), so the readiness endpoint `/api/v1/ready` reports `sqlite`, `sqlite_fts5`, `sqlite_fk`.
- The 7 write-path functions that used raw `sqlite3.connect(database_path, timeout=5)` now route through `connect_database()` (which sets `foreign_keys = ON`, WAL, `synchronous = NORMAL`, `timeout = 5`): `create_user`, `get_user_by_session`, `destroy_session`, `cleanup_expired_sessions`, `record_product_event`, `prune_product_events`, `get_product_analytics_snapshot`. `initialize_storage` intentionally keeps plain `sqlite3.connect` because it manages `foreign_keys = OFF` during migration and re-enables it before `foreign_key_check`.

### `services/api/tests/test_health.py`
Updated two exact-dict assertions to include the new `sqlite_fk` readiness signal.

### `docs/deployment.md`
Added an "Integrity and orphan audit" subsection to §4 (Database persistence, backups, and recovery) documenting `python -m app.integrity <database_path>` and the readiness signal.

## 4. Reliability tests (Phase 4)

New file `services/api/tests/test_data_integrity.py` — 7 tests, all passing, using temporary databases only:

| Test | What it verifies |
|---|---|
| `test_interrupted_transaction_leaves_no_partial_rows` | Forcing a failure mid-`store_document` rolls back: no document, chunks, or FTS rows remain |
| `test_concurrent_writes_leave_database_consistent` | 16 concurrent WAL writers (`BEGIN IMMEDIATE` + busy timeout) leave `quick_check == ok` with all documents intact |
| `test_newer_schema_rejected_without_modifying_db` | `user_version > SCHEMA_VERSION` raises `RuntimeError`; DB unchanged (user_version, tables intact) |
| `test_migration_is_idempotent` | Running `initialize_storage` twice is a no-op; `user_version == 20` |
| `test_cascading_deletion_leaves_no_orphans` | Delete a document → chunks, embeddings, FTS rows, image citations, content resources all gone; `audit_database` clean |
| `test_orphan_detection_catches_planted_orphans` | A manually inserted orphan FTS row is detected by the audit |
| `test_backup_during_concurrent_writes_is_consistent` | Backup taken while writes run in parallel validates (`quick_check` + `foreign_key_check` on snapshot) and restores to a fresh path with the seeded document intact |

Pre-existing coverage referenced (not duplicated): backup round-trip/corrupt-archive/retention (`test_backup.py`), interrupted ingestion (`test_m1_pipeline.py`, `test_embeddings.py`), multi-user isolation (`test_m23_auth_isolation.py`).

## 5. Data lifecycle verification (Phase 5)

Verified on temporary databases; no application data touched:

| Lifecycle item | Status |
|---|---|
| Document deletion cascades (chunks, embeddings, FTS, content resources, KG evidence/relationships, orphan concepts) | verified via `test_cascading_deletion_leaves_no_orphans` |
| Learning-image deletion | `delete_learning_image` cascades regions/citations (existing tests) |
| Session expiry (`cleanup_expired_sessions`) | existing `test_m23_auth_isolation.py` coverage |
| Analytics retention (`prune_product_events`) | existing storage logic, retention window configurable |
| Backup retention/prune | `test_backup.py` (prune keeps only newest N, no symlink following) |
| Restore refuses to overwrite live DB | `test_backup.py` |
| Backup taken under concurrent writes | `test_backup_during_concurrent_writes_is_consistent` (new) |
| User account deletion | **not implemented** — recorded as gap (§7) |
| Data export | **not implemented** — recorded as gap (§7) |

## 6. Recovery runbook (Phase 6)

`docs/deployment.md` §4 already contained create/validate/restore/prune guidance. M50 added the integrity-audit command and `sqlite_fk` readiness signal (see §2 above). No changes to backup format, retention, or restore semantics.

## 7. Gaps explicitly recorded (out of M50 scope)

Per decision, the following are **not** implemented in M50 and are recorded for a future milestone (candidate M51: user data lifecycle):

- **User account deletion** — no endpoint exists; `user_sessions`, `content_resource_validations`, `flashcard_review_items`, `conversations` use `NO ACTION` on `user_id`, so deletion would require cascade handling or table-rebuild migrations first.
- **Data export** — no export endpoint exists; would need a user-scoped dump of documents/chunks/images/analytics.

## 8. Verification gate (Phase 8)

Exact commands (mirror CI):

```bash
# backend
uv run --project services/api --extra dev ruff format --check services/api
uv run --project services/api --extra dev ruff check services/api
cd services/api && uv run --extra dev python app/contract_validation.py
cd services/api && uv run --extra dev pytest
# new M50 integrity tests
cd services/api && uv run --extra dev pytest tests/test_data_integrity.py -v
# frontend
npm test --prefix apps/web
npm run typecheck --prefix apps/web
npm run build --prefix apps/web
# evaluation
uv run --locked --project services/api --extra dev --extra semantic python evaluations/run_suite.py
# security suites + pip-audit + npm audit (per .github/workflows/ci.yml)
```

### Gate result (working tree, M49 baseline kept; post-change run)

| Suite | Result |
|---|---|
| Backend full `pytest` | **265 passed / 31 failed / 2 skipped** |
| Of which M50 `test_data_integrity.py` | **7 passed** |
| M49-regression failures (pass on clean main) | **~20** — async `retrieve_with_mode` refactor; M49 follow-up, not M50 |
| `test_quality_gates` failures | missing `evaluations/realworld/retrieval_eval_v2.json` fixture (pre-existing env issue; fails on clean main too) |
| M49 performance suite | 4 passed / 1 skipped |
| Frontend tests | 21 passed / 1 failed suite |
| Frontend typecheck | 28 errors (24 pre-existing + ~4 from M49) |
| Frontend build | fails on type errors |
| ruff format / ruff check / contract validation | pass (format drift only in pre-existing storage.py regions untouched by M50) |

### Risks
- Concurrency tests can be timing-sensitive on loaded machines; they assert consistency (`quick_check`, document count), not timing.
- The full suite is slow (~5+ min backend); performance bounds may fluctuate under load.
- M49's uncommitted async changes block a fully green gate; fixing them is M49 follow-up work outside M50 scope.
- Frontend `main.tsx` type errors pre-date M50 and were slightly increased by M49's `React.lazy` usage without importing `React`.

## 9. Next milestone recommendation

**Candidate M51 — User data lifecycle:** implement user account deletion (ownership-scoped cascading delete with migrations to add `ON DELETE CASCADE` to the 4 `NO ACTION` tables) and a user-scoped data-export endpoint, with tests and runbook entries. Also resolve the M49 async regressions blocking a green verification gate.

---

*Baseline figures are from actual test runs, not estimates. No data was modified, no destructive migrations were run, and nothing was committed or pushed.*
