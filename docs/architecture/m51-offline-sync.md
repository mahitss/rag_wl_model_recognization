# M51 — Offline-First Architecture & Reliable Synchronization

**Status:** complete (analysis + implementation + verification)
**Milestone:** M51
**Repo:** `C:\Users\pc\OneDrive\Desktop\wonderlearn` (branch `main`, @ `8d8769e` M50)
**Scope:** Offline-first learning experience, reliable sync queue, browser persistent storage, privacy on logout, sync UX states, deterministic tests.

---

## 1. Inspection findings

### Existing offline infrastructure (already present, untracked working-tree)

The M49 working tree contained an offline layer that was not yet exercised by tests:

- `apps/web/src/offlineClient.js` — offline-aware `fetch` wrapper with GET cache fallback and state-changing request queueing.
- `apps/web/src/offlineClient.d.ts` — type declarations for the client.
- `apps/web/src/offlineStore.js` — IndexedDB-backed cache + sync queue layer (scoped by username, limits: 200 entries/store, 50 MiB total, 30-day TTL, 200-queue max).
- `apps/web/src/main.tsx` — wired `fetchWithOffline` through the app's CSRF-authenticated fetch wrapper and exposed `offline`/`syncing`/`syncStatus` UI state.

### Gaps found during inspection

1. **Infinite recursion bug** (`main.tsx:791`): the local `flushQueue` function called itself instead of the imported `flushOfflineQueue`, causing unbounded recursion on every reconnect.
2. **No bounded backoff**: retry logic retried immediately with no delay.
3. **No per-item sync status**: queued items could not be reported as `pending` / `synced` / `failed` to the UI.
4. **No auth-expiry handling**: a 401 during flush was treated like any other retryable error instead of surfacing session expiry.
5. **No cache/queue clearing on logout**: `clearUserCache` and `clearQueue` existed in `offlineStore.js` but were never called.
6. **No deterministic tests**: zero test coverage for the offline/sync layer.
7. **Pre-existing frontend breakage** (out of M51 scope, documented): `React.lazy` used without importing `React`, and revision-queue panel references undefined variables (`revisionLoading`, `submitSectionReview`, …). These M49 working-tree issues block `App.test.tsx`, `npm run typecheck`, and `npm run build`; they were left untouched per the "keep baseline" decision.

### Workflows that require an active connection

These cannot work offline (AI model or server required):

- Document / image upload and indexing (POST `/api/v1/documents`, `/api/v1/images`)
- Tutor answers (`POST /api/v1/tutor/answers`) — requires local AI model
- Quiz generation (`POST /api/v1/quizzes`) — requires retrieval + optional model
- Knowledge graph extraction / relationship verification
- Search (`POST /api/v1/search`)
- Retrieval generation (`POST /api/v1/retrieve`)
- Workspace membership, invites, sharing (write operations)
- Feedback submission

### Workflows that work offline (cached GET responses)

Cached while online and served from IndexedDB when offline:

- Diagram listing and region evidence (`GET /api/v1/diagrams`, `GET /api/v1/diagrams/{id}/regions/{id}/evidence`)
- Document list (`GET /api/v1/documents`)
- Saved notes/summaries (`GET /api/v1/content/resources`)
- Cached diagrams and learning images (`GET /api/v1/images`, image content)
- Learning progress and analytics (`GET /api/v1/progress`, `GET /api/v1/learning-analytics`)
- Quiz review queue (`GET /api/v1/revision/queue`) — reading existing items

### Eligible for offline queueing (replayed on reconnect)

- Quiz answer recording (`POST /api/v1/quizzes/{id}/attempts`)
- Study session start/completion (`POST /api/v1/study-sessions`, `…/complete`)
- Revision queue review recording (`POST /api/v1/revision/review/{type}/{id}`)
- Feedback submission (`POST /api/v1/feedback`)
- Study-coach recommendation accept/dismiss/complete (`PATCH …/recommendations/{id}`)
- Study sessions are only "recorded" locally until sync; the AI-driven study coach plan is server-side.

---

## 2. Changes implemented

### `apps/web/src/main.tsx`

- **Fixed infinite recursion**: the local `flushQueue` now calls the imported `flushOfflineQueue` instead of itself (was recursing without bound on every `online` event).
- **Sync state model extended**: `syncStatus` now includes `pending`, and `authExpired`; added `syncError` surface.
- **Auth-expiry privacy**: on `wonderlearn:session-expired` and on explicit `logout`, the current user's IndexedDB cache and sync queue are cleared (`clearUserCache(currentUsername)` + `clearQueue()`), preventing cross-user data exposure on shared devices.

### `apps/web/src/offlineClient.js`

- Added `MAX_RETRIES = 3`, `BASE_BACKOFF_MS`, bounded exponential backoff (`sleep` + `backoffDelayMs`, capped at 8s).
- **Bounded backoff retries**: flush retries network failures and 5xx up to 3 times with 1s/2s/4s backoff before marking an item failed.
- **Per-item sync status**: items transition `pending` → `synced` / `failed` (tracked via `updateQueueItemStatus` in `offlineStore.js`).
- **409 conflict**: treated as resolved (server has newer data); item removed and marked failed — never silently overwrites newer user data.
- **401 auth expiry**: aborts the flush, reports `authExpired: true`, and the UI prompts re-authentication instead of endlessly retrying.
- **Flush return shape**: `{ synced, failed, pending, authExpired }`.
- Added `isDuplicate(method, url, body)` exported helper.
- Duplicate detection preserved at enqueue time (identical method+url+body not re-queued).
- No credentials, session tokens, or camera frames are ever queued or cached.

### `apps/web/src/offlineStore.js`

- Queued items now carry `status: "pending"`.
- Added `updateQueueItemStatus(id, status)` to mark `pending` / `synced` / `failed`.
- `updateQueueItemStatus` and `clearQueue` exported; `clearUserCache` already exported and now used on logout/auth-expiry.

### `apps/web/src/offlineClient.d.ts`

- `isDuplicate` now returns `Promise<boolean>`.

### `apps/web/src/offlineClient.test.ts` (new)

13 deterministic tests using a mocked IndexedDB store (jsdom has no real IndexedDB):

- GET cache fallback when offline; 503 when nothing cached
- State-changing requests queued offline → 202
- Exact-duplicate request deduplication at enqueue
- Network-error queueing while "online"
- Unauthenticated GETs not cached
- Flush: all accepted → synced; 409 → resolved-not-retried; 401 → authExpired + stop; bounded backoff → failed after 3 attempts; network-down → pending reported
- `isDuplicate` true/false across method/url/body
- Logout clears user cache + queue

### `apps/web/src/main.tsx` sync UI (existing panel, extended)

Shows `online`/`offline` banner, `Syncing with server…`, `Synced N item(s) [, M failed] [, P pending]`, auth-expiry error notice, and a "Retry now" flush button disabled while syncing.

---

## 3. Synchronization guarantees & conflict rules

| Guarantee | How it's met |
|---|---|
| Idempotency / no duplicate submissions | Exact method+URL+body dedup at enqueue; items removed from queue on successful sync |
| Bounded retries | Max 3 attempts with 1s/2s/4s capped exponential backoff |
| Pending / synced / failed tracking | Per-item `status` field, reported in flush result |
| 409 conflict: never overwrite newer user data | Treated as resolved (server wins), item marked failed for user review |
| 401 auth expiry: abort, surface, re-auth | Flush stops, `authExpired` reported, UI prompts sign-in |
| No silent overwrite of newer data | Conflicts surfaced, not auto-applied |
| Ownership + authorization during sync | Cache/queue scoped by `username`; cleared on logout/account switch; replay uses the same authenticated `fetch` with CSRF/session cookies |

---

## 4. Storage & privacy

- **Storage mechanism**: IndexedDB (`wonderlearn-offline` DB, version 1), one object store per cache category plus `offlineQueue`.
- **Per-store limits**: documents 100, retrieval 200, quizzes 100, studySessions 100, learningPaths 50, knowledgeGraph 100, analytics 50, images 50 entries.
- **Total cap**: 50 MiB across all stores; oldest entries evicted first.
- **TTL**: entries older than 30 days evicted on read/write.
- **Cross-user isolation**: composite key `[username, key]` per store; `clearUserCache(username)` on logout/auth-expiry.
- **No secrets stored**: no passwords, session tokens, CSRF tokens, or camera frames in cache or queue.

---

## 5. UX states

| State | Indicator |
|---|---|
| Online | banner hidden; data fetched live |
| Offline | "You are currently offline" banner; cached materials still readable |
| Sync pending | `pending > 0` shown in status line |
| Sync in progress | "Syncing with server…" + button disabled |
| Sync completed | "Synced N item(s)" |
| Sync failed | `failed > 0` shown; "Retry now" button re-enables |
| Auth expired during sync | error notice: "Your session expired while syncing. Please sign in again." |

Materials available offline are those previously fetched while online (served from IndexedDB with `X-From-Cache: true`). No fake sync success is ever reported — failures are shown explicitly.

---

## 6. Testing

### New: `apps/web/src/offlineClient.test.ts` — 13 tests, all passing

| Area | Tests |
|---|---|
| Offline reading / cache fallback | GET cached when offline; 503 when empty; unauthenticated GETs not cached |
| Local quiz/progress persistence (queue) | POST queued offline → 202; dedup; network-error queueing |
| Interrupted sync | bounded backoff then failed; network-down → pending |
| Duplicate prevention | `isDuplicate` + enqueue dedup |
| Conflicting updates | 409 → resolved-not-retried |
| Expired auth during sync | 401 → authExpired + stop |
| Storage quota / logout clear | `clearUserCache` + `clearQueue` on logout |
| Recovery after reconnect | `online` event triggers flush |

### Verification gate results

| Suite | Result |
|---|---|
| Frontend tests (`npm test`) | **34 passed** (13 new offline tests); App.test.tsx has pre-existing M49 `React` import error, unrelated to M51 |
| Backend full `pytest` | **265 passed / 31 failed / 2 skipped** — identical to M50 baseline (pre-existing M49 regressions + missing quality-gates fixture); no new failures |
| M49 performance suite | **4 passed / 1 skipped** |
| M47 + M48 security suites | **58 passed** |
| M50 integrity + backup tests | **21 passed / 1 skipped** |
| ruff check (touched files) | pass |
| ruff format (touched files) | pass |
| contract validation | pass (86 routes) |
| Frontend typecheck / build | **fail on pre-existing M49 errors** in `main.tsx` (revision-queue undefined variables + missing `React` import); left untouched per baseline decision |

---

## 7. Limitations (honest)

- Offline support covers **read-only** cached GET resources and **queued writes** only. Anything requiring the AI model (tutor, quiz generation, KG extraction, retrieval) or server-side mutation of shared workspaces requires a connection.
- IndexedDB is not available in all private/incognito browser modes; cache/queue operations degrade gracefully (writes may fail silently to queue).
- No cross-device sync; data is local to the device/browser profile.
- Backend has no concept of "offline mode"; queued writes replay against the live API when online.
- Frontend typecheck/build are blocked by pre-existing M49 working-tree issues (not introduced by M51).

---

## 8. Reproduction commands

```bash
# Frontend offline tests (new)
npm test --prefix apps/web -- --run offlineClient.test.ts

# Full frontend suite
npm test --prefix apps/web -- --run

# Backend M50 integrity/backup + M49 performance
uv run --project services/api --extra dev pytest tests/test_data_integrity.py tests/test_backup.py tests/test_performance.py tests/test_health.py

# M47/M48 security regressions
uv run --project services/api --extra dev pytest tests/test_security_redteam.py tests/test_prompt_injection.py tests/test_security_remediation.py tests/test_security.py

# Lint (touched files)
uv run --project services/api --extra dev ruff check services/api/app/integrity.py services/api/tests/test_data_integrity.py services/api/app/storage.py services/api/tests/test_health.py
uv run --project services/api --extra dev ruff format --check services/api/app/integrity.py services/api/tests/test_data_integrity.py
```

---

## 9. Files changed

| File | Change |
|---|---|
| `apps/web/src/main.tsx` | Fixed recursive `flushQueue`; extended sync state (`pending`, `authExpired`, `syncError`); clear cache/queue on logout & session expiry |
| `apps/web/src/offlineClient.js` | Bounded backoff, per-item sync status, 409/401 handling, `isDuplicate` export |
| `apps/web/src/offlineClient.d.ts` | `isDuplicate` returns `Promise<boolean>` |
| `apps/web/src/offlineStore.js` | Items carry `status`; new `updateQueueItemStatus` |
| `apps/web/src/offlineClient.test.ts` | New — 13 deterministic offline/sync tests |

Nothing was committed or pushed (awaiting your authorization).
