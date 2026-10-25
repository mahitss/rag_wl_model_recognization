/* eslint-disable no-console */
/**
 * Offline storage layer using IndexedDB.
 *
 * Provides a Promise-based wrapper around IndexedDB for:
 *   1. Caching API responses (GET responses) so the app remains usable when
 *      the local API server is unavailable.
 *   2. Queuing state-changing requests (POST/PATCH/DELETE) made while
 *      offline, for replay when connectivity returns.
 *
 * All data is scoped to the authenticated user (keyed by username) so
 * accounts never leak across logout/login on the same device.
 *
 * Nothing sensitive is stored: no passwords, session tokens, or camera
 * frames.  Only API response bodies (document metadata, citations, quiz
 * data, etc.) and already-user-submitted request bodies are cached.
 *
 * Limits:
 *   - Max 200 entries per cache store
 *   - Max 50 MiB total across all cache stores
 *   - Max 200 queued mutations
 *   - Entries older than 30 days are eligible for eviction
 */

const DB_NAME = "wonderlearn-offline";
const DB_VERSION = 1;

// Cache stores (name -> max entries)
const CACHE_STORES = {
  documents: 100,
  retrieval: 200,
  quizzes: 100,
  studySessions: 100,
  learningPaths: 50,
  knowledgeGraph: 100,
  analytics: 50,
  images: 50,
};

const QUEUE_STORE = "offlineQueue";
const MAX_QUEUE_SIZE = 200;
const MAX_TOTAL_BYTES = 50 * 1024 * 1024; // 50 MiB
const MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000; // 30 days

const ALL_STORE_NAMES = [...Object.keys(CACHE_STORES), QUEUE_STORE];

/**
 * Open (or create) the IndexedDB database with one object store per cache
 * category plus one queue store.  Each cache store uses a composite key of
 * [username, key] so different users sharing a device never see each other's
 * cached data.  The queue store uses its own auto-generated id.
 */
function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onerror = () => reject(req.error);
    req.onsuccess = () => resolve(req.result);
    req.onupgradeneeded = (event) => {
      const db = event.target.result;
      for (const storeName of ALL_STORE_NAMES) {
        if (!db.objectStoreNames.contains(storeName)) {
          if (storeName === QUEUE_STORE) {
            const store = db.createObjectStore(storeName, { keyPath: "id" });
            store.createIndex("timestamp", "timestamp", { unique: false });
          } else {
            const store = db.createObjectStore(storeName, {
              keyPath: ["username", "key"],
            });
            store.createIndex("username", "username", { unique: false });
            store.createIndex("updatedAt", "updatedAt", { unique: false });
          }
        }
      }
    };
  });
}

/**
 * Execute an operation within a transaction on the given stores.
 */
async function withStore(storeNames, mode, operation) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeNames, mode);
    tx.onerror = () => reject(tx.error);
    tx.oncomplete = () => resolve();
    operation(tx);
  });
}

/**
 * Store a cache entry under [username, key] in the given cache store.
 */
async function setCacheItem(storeName, username, key, value) {
  if (!CACHE_STORES[storeName]) {
    throw new Error(`Unknown cache store: ${storeName}`);
  }
  const size = JSON.stringify(value).length;
  await withStore([storeName], "readwrite", (tx) => {
    tx.objectStore(storeName).put({
      username,
      key,
      value,
      updatedAt: Date.now(),
      size,
    });
  });
  await enforceCacheLimits();
}

/**
 * Get a cached entry by [username, key].  Returns null if absent or expired.
 */
async function getCacheItem(storeName, username, key) {
  const result = await withStore([storeName], "readonly", (tx) => {
    return tx.objectStore(storeName).get([username, key]);
  });
  if (!result) return null;
  if (Date.now() - result.updatedAt > MAX_AGE_MS) {
    await removeCacheItem(storeName, username, key);
    return null;
  }
  return result.value;
}

/**
 * Remove a single cache entry.
 */
async function removeCacheItem(storeName, username, key) {
  await withStore([storeName], "readwrite", (tx) => {
    tx.objectStore(storeName).delete([username, key]);
  });
}

/**
 * Remove all cached items for a given user across all stores.
 * Called on logout.
 */
async function clearUserCache(username) {
  await withStore(Object.keys(CACHE_STORES), "readwrite", (tx) => {
    for (const storeName of Object.keys(CACHE_STORES)) {
      const store = tx.objectStore(storeName);
      const index = store.index("username");
      const request = index.openCursor(IDBKeyRange.only(username));
      request.onsuccess = (event) => {
        const cursor = event.target.result;
        if (cursor) {
          cursor.delete();
          cursor.continue();
        }
      };
    }
  });
}

/**
 * Enforce per-store entry count and total byte limits by evicting oldest
 * entries first.
 */
async function enforceCacheLimits() {
  for (const [storeName, maxEntries] of Object.entries(CACHE_STORES)) {
    await enforceStoreLimit(storeName, maxEntries);
  }
  await enforceTotalSizeLimit();
}

async function enforceStoreLimit(storeName, maxEntries) {
  await withStore([storeName], "readwrite", (tx) => {
    const store = tx.objectStore(storeName);
    const index = store.index("updatedAt");
    const request = index.openCursor(null, "prev");
    request.onsuccess = (event) => {
      const cursor = event.target.result;
      let count = 0;
      const toDelete = [];
      while (cursor) {
        count++;
        if (count > maxEntries) {
          toDelete.push(cursor.primaryKey);
        }
        cursor.continue();
      }
      for (const key of toDelete) {
        store.delete(key);
      }
    };
  });
}

async function enforceTotalSizeLimit() {
  let totalBytes = 0;
  const allEntries = [];
  for (const storeName of Object.keys(CACHE_STORES)) {
    const entries = await withStore([storeName], "readonly", (tx) => {
      const store = tx.objectStore(storeName);
      const request = store.getAll();
      return new Promise((resolve, reject) => {
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
    });
    for (const entry of entries) {
      totalBytes += entry.size || 0;
      allEntries.push({
        storeName,
        key: [entry.username, entry.key],
        size: entry.size || 0,
        updatedAt: entry.updatedAt,
      });
    }
  }

  if (totalBytes <= MAX_TOTAL_BYTES) return;

  allEntries.sort((a, b) => a.updatedAt - b.updatedAt);
  let bytesToRemove = totalBytes - MAX_TOTAL_BYTES;
  let removedBytes = 0;
  const byStore = {};
  for (const entry of allEntries) {
    if (removedBytes >= bytesToRemove) break;
    byStore[entry.storeName] = byStore[entry.storeName] || [];
    byStore[entry.storeName].push(entry.key);
    removedBytes += entry.size;
  }

  for (const [storeName, keys] of Object.entries(byStore)) {
    await withStore([storeName], "readwrite", (tx) => {
      const store = tx.objectStore(storeName);
      for (const key of keys) {
        store.delete(key);
      }
    });
  }
}

/**
 * List all cached items for a user in a given store.
 */
async function listCacheForUser(storeName, username) {
  return withStore([storeName], "readonly", (tx) => {
    const store = tx.objectStore(storeName);
    const index = store.index("username");
    const request = index.getAll(IDBKeyRange.only(username));
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result.map((r) => r.value));
      request.onerror = () => reject(request.error);
    });
  });
}

/**
 * Queue a state-changing request for later replay.
 */
async function enqueueItem(method, url, body, headers = {}) {
  const id = `${method}:${url}:${Date.now()}:${Math.random().toString(36).slice(2, 10)}`;
  const item = {
    id,
    method,
    url,
    body,
    headers: headers || {},
    timestamp: Date.now(),
    retries: 0,
  };
  await withStore([QUEUE_STORE], "readwrite", (tx) => {
    tx.objectStore(QUEUE_STORE).put(item);
  });
  return id;
}

/**
 * Get all queued items.
 */
async function getQueue() {
  return withStore([QUEUE_STORE], "readonly", (tx) => {
    const request = tx.objectStore(QUEUE_STORE).getAll();
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  });
}

/**
 * Remove a queued item.
 */
async function dequeueItem(id) {
  await withStore([QUEUE_STORE], "readwrite", (tx) => {
    tx.objectStore(QUEUE_STORE).delete(id);
  });
}

/**
 * Update an existing queued item (e.g., increment retry count).
 */
async function updateQueueItem(item) {
  await withStore([QUEUE_STORE], "readwrite", (tx) => {
    tx.objectStore(QUEUE_STORE).put(item);
  });
}

/**
 * Clear all queued items for all users (called on logout).
 */
async function clearQueue() {
  await withStore([QUEUE_STORE], "readwrite", (tx) => {
    tx.objectStore(QUEUE_STORE).clear();
  });
}

export {
  setCacheItem,
  getCacheItem,
  removeCacheItem,
  clearUserCache,
  listCacheForUser,
  enqueueItem,
  getQueue,
  dequeueItem,
  clearQueue,
  updateQueueItem,
};
export const STORE_NAMES = Object.keys(CACHE_STORES);
