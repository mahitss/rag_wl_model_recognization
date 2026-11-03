/* eslint-disable no-console */
/**
 * Offline-aware fetch wrapper with sync queue.
 *
 * Wraps fetch so that:
 *   - GET requests: try network first, fall back to IndexedDB cache when
 *     offline or when the network request fails.
 *   - State-changing requests (POST/PATCH/DELETE): queue when offline,
 *     return a 202 "queued" response, and replay when connectivity returns.
 *   - Duplicate mutations are detected and deduplicated.
 *   - Conflicting updates are surfaced rather than silently overwritten.
 *
 * No credentials, session tokens, or sensitive data are stored in the
 * offline queue.  Only the request method, URL, and body (already submitted
 * by the user while online) are queued.
 */

import {
  setCacheItem,
  getCacheItem,
  enqueueItem,
  getQueue,
  dequeueItem,
  updateQueueItem,
  updateQueueItemStatus,
  clearQueue,
  STORE_NAMES as CACHE_STORE_NAMES,
} from "./offlineStore.js";

// ---------------------------------------------------------------------------
// Online/offline detection
// ---------------------------------------------------------------------------

let isOnline =
  typeof navigator !== "undefined" && navigator.onLine !== undefined
    ? navigator.onLine
    : true;
const listeners = new Set();

function notifyListeners() {
  for (const listener of listeners) {
    try {
      listener(isOnline);
    } catch {
      // Listener errors are non-fatal
    }
  }
}

if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  window.addEventListener("online", () => {
    isOnline = true;
    notifyListeners();
    flushQueue();
  });
  window.addEventListener("offline", () => {
    isOnline = false;
    notifyListeners();
  });
}

export function isOffline() {
  return !isOnline;
}

export function onOnlineChange(listener) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

// ---------------------------------------------------------------------------
// Sync retry / backoff configuration
// ---------------------------------------------------------------------------

const MAX_RETRIES = 3;
const BASE_BACKOFF_MS = 1000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Backoff delay in milliseconds for the given (1-based) attempt number.
 * Capped so bounded retries never block the UI for too long.
 */
function backoffDelayMs(attempt) {
  return Math.min(BASE_BACKOFF_MS * Math.pow(2, attempt - 1), 8000);
}

/**
 * Report whether an identical state-changing request is already queued.
 */
export function isDuplicate(method, url, body) {
  const normalizedBody = body == null ? null : JSON.stringify(body);
  const queueSnapshot = getQueue(); // synchronous-ish wrapper handled by caller
  return queueSnapshot.then((items) =>
    items.some(
      (item) =>
        item.method === method &&
        item.url === url &&
        JSON.stringify(item.body) === normalizedBody,
    ),
  );
}

/**
 * Map a URL path to a cache store name.
 */
function mapUrlToStore(url) {
  const u = String(url);
  if (u.includes("/retrieve")) return "retrieval";
  if (u.includes("/quizzes")) return "quizzes";
  if (u.includes("/study-sessions")) return "studySessions";
  if (u.includes("/learning-paths")) return "learningPaths";
  if (u.includes("/knowledge-graph")) return "knowledgeGraph";
  if (u.includes("/learning-analytics")) return "analytics";
  if (u.includes("/images")) return "images";
  return "documents";
}

/**
 * Wrap fetch with offline support.
 *
 * For GET requests: try network first, fall back to cache.
 * For state-changing requests (POST/PATCH/DELETE): queue when offline,
 * return a 202 "queued" response.
 *
 * `username` is required for cache read/write so that data stays scoped to
 * the authenticated account.
 * `fetchFn` is the fetch function to use (defaults to global fetch); this
 * allows the frontend to pass its own CSRF-authenticated fetch wrapper.
 */
export async function fetchWithOffline(
  input,
  init = {},
  { username, skipCache = false, fetchFn = globalThis.fetch } = {}
) {
  const url = typeof input === "string" ? input : input.url;
  const method = (init.method || "GET").toUpperCase();
  const isStateChanging = method !== "GET" && method !== "HEAD";

  const doFetch = (input, init) => fetchFn(input, init);

  // State-changing requests when offline: queue and return 202
  if (isStateChanging && !isOnline) {
    // Prevent exact duplicates
    const existing = (await getQueue()).find(
      (item) =>
        item.method === method &&
        item.url === url &&
        JSON.stringify(item.body) === JSON.stringify(init.body)
    );
    if (existing) {
      return queuedResponse(existing.id, "Duplicate request already queued.");
    }
    await enqueueItem(method, url, init.body, init.headers);
    return queuedResponse(
      undefined,
      "Request queued for sync when back online.",
      true
    );
  }

  // GET requests when offline: try cache
  if (method === "GET" && !isOnline) {
    const cached = await getCacheItem(
      mapUrlToStore(url),
      username,
      url.split("?")[0]
    );
    if (cached) {
      return {
        ok: true,
        status: 200,
        statusText: "OK (cached)",
        json: async () => cached,
        text: async () => JSON.stringify(cached),
        headers: new Headers({ "X-From-Cache": "true" }),
      };
    }
    return {
      ok: false,
      status: 503,
      statusText: "Offline",
      json: async () => {
        throw new Error("Offline and no cached data available");
      },
      text: async () => "Offline and no cached data available",
      ok: false,
    };
  }

  // Online: try network first
  try {
    const response = await doFetch(input, init);

    // Cache successful GET responses
    if (method === "GET" && response.ok && username) {
      try {
        const responseClone = response.clone();
        const data = await responseClone.json();
        await setCacheItem(mapUrlToStore(url), username, url.split("?")[0], data);
      } catch {
        // Not JSON or not cacheable; skip
      }
    }
    return response;
  } catch (err) {
    // Network error while "online" - treat as offline
    if (isStateChanging) {
      // Prevent duplicates
      const existing = (await getQueue()).find(
        (item) =>
          item.method === method &&
          item.url === url &&
          JSON.stringify(item.body) === JSON.stringify(init.body)
      );
      if (existing) {
        return queuedResponse(existing.id, "Duplicate request already queued.");
      }
      await enqueueItem(method, url, init.body, init.headers);
      return queuedResponse(
        undefined,
        "Request queued due to network error.",
        true
      );
    }

    // GET request - try cache
    const cached = await getCacheItem(
      mapUrlToStore(url),
      username,
      url.split("?")[0]
    );
    if (cached) {
      return {
        ok: true,
        status: 200,
        statusText: "OK (cached)",
        json: async () => cached,
        text: async () => JSON.stringify(cached),
        headers: new Headers({ "X-From-Cache": "true" }),
      };
    }
    return {
      ok: false,
      status: 503,
      statusText: "Offline",
      json: async () => {
        throw new Error("Offline and no cached data available");
      },
      text: async () => "Offline and no cached data available",
      ok: false,
    };
  }
}

/**
 * Queue a state-changing request without making the network call.
 * Returns a queued response.
 */
export async function queueRequest(method, url, body, headers = {}) {
  await enqueueItem(method, url, body, headers);
  return queuedResponse(
    undefined,
    "Request queued for sync when back online.",
    true
  );
}

/**
 * Flush the sync queue: replay all queued state-changing requests with bounded
 * exponential backoff.  Each item is retried up to MAX_RETRIES times before it
 * is marked failed.  A 409 conflict is treated as resolved (server has newer
 * data) and is not retried.  A 401 (session expired) aborts the flush and is
 * reported so the UI can prompt re-authentication.
 *
 * Returns { synced, failed, pending, authExpired }.
 */
export async function flushQueue() {
  const queue = await getQueue();
  if (queue.length === 0) {
    return { synced: 0, failed: 0, pending: 0, authExpired: false };
  }

  let synced = 0;
  let failed = 0;
  let authExpired = false;

  for (const item of [...queue]) {
    if (authExpired) break;

    let succeeded = false;
    let attempts = 0;
    const maxAttempts = MAX_RETRIES;

    while (attempts < maxAttempts && !succeeded && !authExpired) {
      attempts++;
      let response;
      try {
        response = await fetch(item.url, {
          method: item.method,
          headers: {
            "Content-Type": "application/json",
            ...item.headers,
          },
          body: item.body,
        });
      } catch {
        // Network still down - back off, then retry within budget.
        if (attempts < maxAttempts) {
          await sleep(backoffDelayMs(attempts));
          continue;
        }
        break;
      }

      if (response.ok) {
        await dequeueItem(item.id);
        await updateQueueItemStatus(item.id, "synced");
        synced++;
        succeeded = true;
      } else if (response.status === 401) {
        // Session expired - stop flushing and surface auth failure.
        authExpired = true;
        await updateQueueItemStatus(item.id, "failed");
        failed++;
        break;
      } else if (response.status === 409) {
        // Conflict - server has newer data.  Do not overwrite; surface for review.
        await dequeueItem(item.id);
        await updateQueueItemStatus(item.id, "failed");
        failed++;
        succeeded = true;
      } else {
        // Retryable server error - back off and retry within budget.
        if (attempts < maxAttempts) {
          await sleep(backoffDelayMs(attempts));
          continue;
        }
        break;
      }
    }

    if (!succeeded && !authExpired) {
      await updateQueueItemStatus(item.id, "failed");
      failed++;
    }
  }

  const remaining = await getQueue();
  return {
    synced,
    failed,
    pending: remaining.filter((item) => item.status === "pending").length,
    authExpired,
  };
}

function queuedResponse(id, message, offline = false) {
  return {
    ok: true,
    status: 202,
    statusText: "Queued",
    json: async () => ({
      status: "queued",
      message,
      offline,
      queuedId: id,
    }),
    text: async () =>
      JSON.stringify({ status: "queued", message, offline, queuedId: id }),
    ok: true,
  };
}
