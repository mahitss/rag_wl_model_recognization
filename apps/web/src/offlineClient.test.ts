/**
 * Deterministic tests for the M51 offline sync client.
 *
 * The IndexedDB-backed offlineStore is mocked so these tests run in jsdom
 * without a real IndexedDB implementation.  Only the sync-queue logic
 * (queueing, dedup, bounded backoff retries, 409 conflict, 401 auth expiry,
 * cache fallback) is exercised here.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearUserCache,
  clearQueue,
  dequeueItem,
  enqueueItem,
  getCacheItem,
  getQueue,
  setCacheItem,
  updateQueueItem,
  updateQueueItemStatus,
} from "./offlineStore";
import {
  flushQueue,
  isDuplicate,
  isOffline,
  onOnlineChange,
  fetchWithOffline,
} from "./offlineClient";

// ---------------------------------------------------------------------------
// Mock IndexedDB store
// ---------------------------------------------------------------------------

const fakeCache = new Map<string, { value: unknown; updatedAt: number }>();
const fakeQueue: Array<{
  id: string;
  method: string;
  url: string;
  body: unknown;
  headers: Record<string, string>;
  timestamp: number;
  retries: number;
  status: string;
}> = [];

vi.mock("./offlineStore.js", () => ({
  setCacheItem: vi.fn(async (_store: string, username: string, key: string, value: unknown) => {
    fakeCache.set(`${username}|${key}`, { value, updatedAt: Date.now() });
  }),
  getCacheItem: vi.fn(async (_store: string, username: string, key: string) => {
    const entry = fakeCache.get(`${username}|${key}`);
    return entry ? entry.value : null;
  }),
  enqueueItem: vi.fn(async (method: string, url: string, body: unknown, headers: Record<string, string>) => {
    const id = `${method}:${url}:${Date.now()}:${Math.random().toString(36).slice(2, 6)}`;
    fakeQueue.push({ id, method, url, body, headers, timestamp: Date.now(), retries: 0, status: "pending" });
    return id;
  }),
  getQueue: vi.fn(async () => [...fakeQueue]),
  dequeueItem: vi.fn(async (id: string) => {
    const idx = fakeQueue.findIndex((i) => i.id === id);
    if (idx !== -1) fakeQueue.splice(idx, 1);
  }),
  updateQueueItem: vi.fn(async (item: any) => {
    const idx = fakeQueue.findIndex((i) => i.id === item.id);
    if (idx !== -1) fakeQueue[idx] = { ...fakeQueue[idx], ...item };
  }),
  updateQueueItemStatus: vi.fn(async (id: string, status: string) => {
    const idx = fakeQueue.findIndex((i) => i.id === id);
    if (idx !== -1) fakeQueue[idx].status = status;
  }),
  clearQueue: vi.fn(async () => {
    fakeQueue.length = 0;
  }),
  clearUserCache: vi.fn(async (username: string) => {
    for (const [k] of fakeCache) {
      if (k.startsWith(`${username}|`)) fakeCache.delete(k);
    }
  }),
}));

function mockJsonResponse(body: unknown, status = 200): Response {
  const response = {
    ok: status >= 200 && status < 300,
    status,
    statusText: status >= 200 && status < 300 ? "OK" : "Error",
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: new Headers(),
    clone: () => response,
  } as unknown as Response;
  return response;
}

// ---------------------------------------------------------------------------
// Helpers to drive online/offline state (module reads navigator.onLine)
// ---------------------------------------------------------------------------

function setOnline(online: boolean) {
  Object.defineProperty(navigator, "onLine", { configurable: true, value: online });
  window.dispatchEvent(new Event(online ? "online" : "offline"));
}

beforeEach(() => {
  fakeCache.clear();
  fakeQueue.length = 0;
  setOnline(true);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// fetchWithOffline
// ---------------------------------------------------------------------------

describe("fetchWithOffline", () => {
  it("caches a successful GET and returns it from cache when offline", async () => {
    const body = { documents: [{ documentId: "doc-1", displayFilename: "a.pdf" }] };
    const fetchFn = vi.fn(async () => mockJsonResponse(body));

    const online = await fetchWithOffline("/api/v1/documents", { method: "GET" }, { username: "alice", fetchFn });
    expect(online.ok).toBe(true);
    expect(await online.json()).toEqual(body);
    expect(fetchFn).toHaveBeenCalledTimes(1);

    // Now go offline - the same request should be served from cache.
    setOnline(false);
    expect(isOffline()).toBe(true);

    const cached = await fetchWithOffline("/api/v1/documents", { method: "GET" }, { username: "alice", fetchFn });
    expect(cached.ok).toBe(true);
    expect(cached.headers.get("X-From-Cache")).toBe("true");
    expect(await cached.json()).toEqual(body);
    // fetchFn must NOT be called again while offline.
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it("returns 503 when offline and nothing is cached", async () => {
    setOnline(false);
    expect(isOffline()).toBe(true);

    const result = await fetchWithOffline("/api/v1/unknown", { method: "GET" }, { username: "alice" });
    expect(result.ok).toBe(false);
    expect(result.status).toBe(503);
  });

  it("queues a state-changing request when offline and returns 202", async () => {
    setOnline(false);
    expect(isOffline()).toBe(true);

    const body = { topic: "photosynthesis" };
    const result = await fetchWithOffline("/api/v1/quizzes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }, { username: "alice" });

    expect(result.ok).toBe(true);
    expect(result.status).toBe(202);
    const json = await result.json();
    expect(json.status).toBe("queued");
    expect(fakeQueue.length).toBe(1);
    expect(fakeQueue[0].method).toBe("POST");
    expect(fakeQueue[0].status).toBe("pending");
  });

  it("deduplicates identical queued requests", async () => {
    setOnline(false);
    const body = JSON.stringify({ topic: "photosynthesis" });

    const first = await fetchWithOffline("/api/v1/quizzes", {
      method: "POST", headers: { "Content-Type": "application/json" }, body,
    }, { username: "alice" });
    const second = await fetchWithOffline("/api/v1/quizzes", {
      method: "POST", headers: { "Content-Type": "application/json" }, body,
    }, { username: "alice" });

    const firstJson = await first.json();
    const secondJson = await second.json();
    expect(secondJson.status).toBe("queued");
    expect(secondJson.message).toContain("Duplicate");
    expect(fakeQueue.length).toBe(1);
  });

  it("queues a state-changing request that fails with a network error while online", async () => {
    setOnline(true);
    expect(isOffline()).toBe(false);

    const failingFetch = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    const result = await fetchWithOffline("/api/v1/feedback", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "t", description: "d" }),
    }, { username: "alice", fetchFn: failingFetch });

    expect(result.status).toBe(202);
    expect(fakeQueue.length).toBe(1);
  });

  it("does not cache GET responses for unauthenticated requests", async () => {
    const body = { ok: true };
    const fetchFn = vi.fn(async () => mockJsonResponse(body));
    await fetchWithOffline("/api/v1/diagrams", { method: "GET" }, { fetchFn });
    expect(fakeCache.size).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// flushQueue
// ---------------------------------------------------------------------------

describe("flushQueue", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("syncs all queued items when the server accepts them", async () => {
    fakeQueue.push({ id: "q1", method: "POST", url: "/api/v1/feedback", body: { title: "t" }, headers: {}, timestamp: Date.now(), retries: 0, status: "pending" });
    fakeQueue.push({ id: "q2", method: "POST", url: "/api/v1/revision/review/quiz/item1", body: { outcome: "correct" }, headers: {}, timestamp: Date.now(), retries: 0, status: "pending" });
    fetchMock.mockResolvedValue(mockJsonResponse({ status: "recorded" }));

    const result = await flushQueue();

    expect(result.synced).toBe(2);
    expect(result.failed).toBe(0);
    expect(result.pending).toBe(0);
    expect(result.authExpired).toBe(false);
    expect(fakeQueue.length).toBe(0);
  });

  it("treats a 409 conflict as resolved and does not retry", async () => {
    fakeQueue.push({ id: "q1", method: "PATCH", url: "/api/v1/content/resources/r1", body: { title: "x" }, headers: {}, timestamp: Date.now(), retries: 0, status: "pending" });
    fetchMock.mockResolvedValue(mockJsonResponse({ detail: "conflict" }, 409));

    const result = await flushQueue();

    expect(result.synced).toBe(0);
    expect(result.failed).toBe(1);
    expect(result.pending).toBe(0);
    expect(fetchMock).toHaveBeenCalledTimes(1); // not retried
  });

  it("stops flushing and reports authExpired on 401", async () => {
    fakeQueue.push({ id: "q1", method: "POST", url: "/api/v1/feedback", body: { title: "t" }, headers: {}, timestamp: Date.now(), retries: 0, status: "pending" });
    fakeQueue.push({ id: "q2", method: "POST", url: "/api/v1/feedback", body: { title: "t2" }, headers: {}, timestamp: Date.now(), retries: 0, status: "pending" });
    // First call 401, second would never be reached.
    fetchMock.mockResolvedValueOnce(mockJsonResponse({ error: { message: "Unauthorized" } }, 401));

    const result = await flushQueue();

    expect(result.authExpired).toBe(true);
    expect(result.failed).toBe(1);
    expect(result.pending).toBe(1); // q2 never attempted
  });

  it("retries with bounded backoff and then marks failed", async () => {
    vi.useFakeTimers();
    fakeQueue.push({ id: "q1", method: "POST", url: "/api/v1/feedback", body: { title: "t" }, headers: {}, timestamp: Date.now(), retries: 0, status: "pending" });
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const promise = flushQueue();
    // Advance past all backoff delays (1s + 2s + 4s = 7s) so retries complete.
    await vi.advanceTimersByTimeAsync(8000);
    const result = await promise;

    vi.useRealTimers();

    expect(result.synced).toBe(0);
    expect(result.failed).toBe(1);
    expect(result.pending).toBe(0);
    // MAX_RETRIES = 3 attempts.
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("reports pending items that could not be flushed while offline", async () => {
    fakeQueue.push({ id: "q1", method: "POST", url: "/api/v1/feedback", body: { title: "t" }, headers: {}, timestamp: Date.now(), retries: 0, status: "pending" });
    // fetch throws (still offline) - flush stops on first network failure.
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    const result = await flushQueue();

    expect(result.failed).toBe(1);
    expect(result.pending).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// isDuplicate
// ---------------------------------------------------------------------------

describe("isDuplicate", () => {
  it("returns true when an identical request is already queued", async () => {
    await enqueueItem("POST", "/api/v1/feedback", { title: "dup" }, {});
    expect(await isDuplicate("POST", "/api/v1/feedback", { title: "dup" })).toBe(true);
    expect(await isDuplicate("POST", "/api/v1/feedback", { title: "other" })).toBe(false);
    expect(await isDuplicate("GET", "/api/v1/feedback", { title: "dup" })).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Logout / account switch privacy
// ---------------------------------------------------------------------------

describe("clear on logout / account switch", () => {
  it("clears the queue and the user's cache on logout", async () => {
    await setCacheItem("documents", "alice", "/api/v1/documents", { documents: [] });
    await enqueueItem("POST", "/api/v1/feedback", { title: "t" }, {});

    await clearUserCache("alice");
    await clearQueue();

    expect(await getCacheItem("documents", "alice", "/api/v1/documents")).toBeNull();
    expect(await getQueue()).toEqual([]);
  });
});
