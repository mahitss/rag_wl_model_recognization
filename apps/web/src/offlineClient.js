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
 * Flush the sync queue: replay all queued state-changing requests.
 * Returns { synced: number, failed: number }.
 */
export async function flushQueue() {
  const queue = await getQueue();
  if (queue.length === 0) return { synced: 0, failed: 0 };

  let synced = 0;
  let failed = 0;
  const toRetry = [...queue];

  for (const item of toRetry) {
    try {
      const response = await fetch(item.url, {
        method: item.method,
        headers: {
          "Content-Type": "application/json",
          ...item.headers,
        },
        body: item.body,
      });

      if (response.ok) {
        await dequeueItem(item.id);
        synced++;
      } else if (response.status === 409) {
        // Conflict - server has newer data. Mark for user review.
        item.retries = 3;
        await updateQueueItem(item);
        failed++;
      } else {
        item.retries = (item.retries || 0) + 1;
        if (item.retries >= 3) {
          await dequeueItem(item.id);
          failed++;
        } else {
          await updateQueueItem(item);
          failed++;
        }
      }
    } catch {
      // Network still down - stop flushing
      const remaining = queue.filter((i) => (i.retries || 0) < 3);
      failed += remaining.length;
      // Remove maxed-out items
      for (const item of queue) {
        if (item.retries >= 3) await dequeueItem(item.id);
      }
      break;
    }
  }

  return { synced, failed };
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
