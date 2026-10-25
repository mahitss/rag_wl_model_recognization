# M35 Offline-First Learning & Resilient Study Sessions

WonderLearn M35 adds offline-first capabilities to the browser UI, allowing beta
users to continue working when the local API server is unavailable or network
connectivity is intermittent. All offline capabilities are implemented
frontend-only; no changes were made to the backend API or SQLite database
schema.

## Offline Capability Matrix

| Capability                  | Offline Support | Storage Location | Notes                                                  |
|-----------------------------|-----------------|------------------|--------------------------------------------------------|
| Diagrams list               | Yes             | IndexedDB        | Cached on first load                                   |
| Document list               | Yes             | IndexedDB        | Cached on first load                                   |
| Document retrieval/citation | Yes             | IndexedDB        | Cached on first retrieve                               |
| Knowledge graph             | Yes             | IndexedDB        | Cached on first load                                   |
| Analytics dashboard         | Yes             | IndexedDB        | Cached on first load                                   |
| Image regions               | Yes             | IndexedDB        | Cached on first load                                   |
| Quiz generation             | No              | N/A              | Requires local tutor model                             |
| Tutor answers               | No              | N/A              | Requires local model server                            |
| Study session start/complete| No              | N/A              | Requires backend API                                   |
| Authentication              | No              | N/A              | Requires backend session cookie                        |

## Storage Strategy

**Storage backend:** Browser `IndexedDB` via `indexedDB` API (no external
dependencies).

**Database:** `wonderlearn-offline` database, version 1.

**Object stores:**

| Store Name                  | Purpose                        | Max Entries |
|-----------------------------|--------------------------------|-------------|
| documents                   | Cached document metadata       | 100         |
| retrieval                   | Cached retrieval results       | 200         |
| quizzes                     | Cached quiz data               | 100         |
| studySessions               | Cached study session data      | 100         |
| learningPaths               | Cached learning path data      | 50          |
| knowledgeGraph              | Cached graph concept data      | 100         |
| analytics                   | Cached analytics data          | 50          |
| images                      | Cached image metadata          | 50          |
| offlineQueue                | Queued state-changing requests | 200         |

**Retention:** Cache entries older than 30 days are evicted automatically.
Total cache size across all stores is capped at 50 MiB. When the limit is
exceeded, oldest entries (by `updatedAt`) are evicted first.

**Privacy:** The offline store contains only data the user has already fetched
from the local API while online. No credentials, session tokens, passwords, or
camera frames are cached. Feedback descriptions and tutor question text *are*
cached because they were already sent to the API by the user. Deleting an
account or logging out clears all offline data for that user.

## Synchronization Workflow

**Online detection:** `navigator.onLine` is checked on page load and on
`online`/`offline` browser events. State changes are immediately propagated
through React state and the offline banner UI.

**GET requests (read operations):**
1. Try network first (`fetch` to local API).
2. If network fails or client is offline, serve from IndexedDB cache.
3. Cache responses are marked with `X-From-Cache: true` header so UI can
   display "cached" badges if desired.

**State-changing requests (POST/PATCH/DELETE):**
1. When offline, request is queued in IndexedDB `offlineQueue` store with
   method, URL, body, headers, timestamp, and retry count.
2. Returns HTTP 202 "Queued" response to UI.
3. User sees offline banner with "Retry now" button.
4. On reconnect (`online` event), `flushQueue()` replays queued requests in
   FIFO order.
5. Each queued request is retried up to 3 times. Conflicts (HTTP 409) are
   counted as failures after 3 retries. Failed items remain in queue for
   manual retry.
6. Duplicate detection prevents the same method+URL+body from being queued
   twice.

**Account isolation:** Queue and cache entries are keyed by `username`. On
logout, `clearQueue()` and `clearUserCache()` wipe all offline data for that
user. Logging in as a different user starts with empty offline state.

## API: `fetchWithOffline(input, init, options)`

All API calls in the frontend go through `fetchWithOffline(input, init,
{ username, fetchFn })` instead of raw `fetch()`. This wrapper:

1. Checks `navigator.onLine` / `online`/`offline` events for connectivity
2. For state-changing requests when offline: queues via `enqueueItem()`,
   returns `202 Queued` response
3. For GET requests offline: reads from IndexedDB cache via `getCacheItem()`
4. For online requests: calls `fetchFn` (the user-provided fetch wrapper),
   caches successful GET responses via `setCacheItem()`

**Frontend integration:** `main.tsx` wraps its existing `fetch` wrapper with
`fetchWithOffline`, passing the CSRF-authenticated fetch function and the
current username. All existing API calls automatically get offline support
without modification.

## UI/UX

**Offline banner:** Appears at top of page when `isOffline === true`. Shows:
- "Offline" heading
- Explanation that study materials are still available and actions are queued
- "Syncing..." indicator when `flushQueue()` is running
- Sync summary: "Synced N item(s)" or "Synced N item(s), M failed"
- "Retry now" button to manually trigger `flushQueue()`

**Loading states:** `syncing` state is shown while `flushQueue()` is running.
No fake success messages - errors from failed syncs are displayed.

**Skip cache option:** `skipCache: true` option available for calls that
should always hit the network (e.g., health checks).

## Verification Results

All commands run from repository root:

### Frontend Tests
```
npm test --prefix apps/web
```
Result: 40 passed, 0 failed (4 test files)

### TypeScript Typecheck
```
npm run typecheck --prefix apps/web
```
Result: Passed (no errors)

### Production Build
```
npm run build --prefix apps/web
```
Result: Built successfully (dist/ emitted)

### Backend Tests (regression)
```
uv run --project services/api --extra dev pytest
```
Result: 169 passed, 1 skipped, 0 failed

### Evaluation Suite
```
uv run --locked --project services/api --extra dev --extra semantic python evaluations/run_suite.py
```
Result: Exit 0. Backend tests: 169 passed, 0 failed. Retrieval evaluation:
lexical offline mode, recall@5 = 1.000, citation validity = 1.0,
unsupported abstention recall = 1.0.

### Lint & Format
```
uv run --project services/api --extra dev ruff check services/api
uv run --project services/api --extra dev ruff format --check services/api
```
Result: All checks passed, 60 files already formatted

## Known Limitations

- **No full offline operation:** The backend API, SQLite database, and
  optional tutor model all require the local API server to be running.
  Offline mode only caches *previously fetched* data and queues
  state-changing requests for later replay.
- **No new data while offline:** Users cannot upload new PDFs, generate new
  quizzes, or get new tutor answers while offline. They can only view
  previously cached data.
- **Quiz attempts cannot be submitted offline:** Quiz answer submission is a
  state-changing POST and will be queued until online.
- **Study sessions cannot be started/completed offline:** These require
  backend API calls and will be queued.
- **Cache staleness:** Cached data may be stale if source data changed on the
  server while offline. Conflict detection (HTTP 409) is handled by marking
  for user review after 3 retry attempts.
- **localStorage vs IndexedDB:** Onboarding state remains in `localStorage`;
  only API response data uses IndexedDB.

## Files Changed

- `apps/web/src/offlineStore.js` (new) - IndexedDB wrapper for cache + queue
- `apps/web/src/offlineClient.js` (new) - Offline-aware fetch wrapper
- `apps/web/src/main.tsx` (modified) - Integrated offline UI banner and state
- `apps/web/src/style.css` (modified) - Added offline banner CSS
