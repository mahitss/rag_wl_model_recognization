"""Privacy-conscious operational metrics and SLI tracking for WonderLearn.

Extends the existing ``OperationalMetrics`` with:

* **Service-level indicators** (availability, latency, error rate) tracked per
  operation category (ingestion, retrieval, model_inference, webhook_delivery,
  background_job, database).
* **Dependency health** tracking for database and model provider, with graceful
  degradation: an unavailable *optional* provider (e.g. the model tutor) does
  *not* mark the whole application unhealthy.
* **Domain event logging** helpers for ingestion, retrieval, model inference,
  webhook delivery, and background jobs.  All helpers are privacy-conscious:
  they never record user IDs, document IDs, prompts, credentials, or private
  learning content in labels or metrics.  High-cardinality identifiers are
  deliberately excluded.

All state is bounded in-memory and resets on restart, appropriate for a
single-process, ₹0-budget deployment.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from statistics import median
from typing import Any

# Operation categories used for SLI tracking.  These are low-cardinality,
# privacy-safe labels.
OPERATION_CATEGORIES = (
    "ingestion",
    "retrieval",
    "model_inference",
    "webhook_delivery",
    "background_job",
    "database",
    "http_request",
)

# Dependency names tracked for health checks.  Low cardinality by design.
DEPENDENCY_NAMES = ("database", "model_provider", "embedding_provider")

# SLI measurement window in seconds (rolling).
_SLI_WINDOW_SECONDS = 3600

# Maximum number of SLI buckets retained per category.
_MAX_SLI_BUCKETS = 144  # 1 hour at 25-second resolution


def _now() -> float:
    return time.time()


def _to_camel(snake: str) -> str:
    """Convert snake_case to camelCase."""
    parts = snake.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _monotonic() -> float:
    return time.monotonic()


class _SLIWindow:
    """Rolling window of success/error counts and latency samples for one
    operation category.  Bounded to ``_MAX_SLI_BUCKETS`` buckets."""

    def __init__(self) -> None:
        self._buckets: deque[dict[str, Any]] = deque(maxlen=_MAX_SLI_BUCKETS)

    def record(self, *, success: bool, duration_ms: float) -> None:
        now = _now()
        # Reuse the current bucket if it is within 25 seconds.
        bucket_duration = 25.0
        if self._buckets and (now - self._buckets[-1]["t"]) < bucket_duration:
            b = self._buckets[-1]
        else:
            b = {"t": now, "ok": 0, "err": 0, "latencies": []}
            self._buckets.append(b)
        if success:
            b["ok"] += 1
        else:
            b["err"] += 1
        b["latencies"].append(float(duration_ms))

    def snapshot(self) -> dict[str, Any]:
        total_ok = 0
        total_err = 0
        all_latencies: list[float] = []
        now = _now()
        for b in self._buckets:
            # Drop buckets older than the measurement window.
            if now - b["t"] > _SLI_WINDOW_SECONDS:
                continue
            total_ok += b["ok"]
            total_err += b["err"]
            all_latencies.extend(b["latencies"])
        total = total_ok + total_err
        availability = (total_ok / total * 100.0) if total else 100.0
        error_rate = (total_err / total * 100.0) if total else 0.0
        latencies = sorted(all_latencies)
        if latencies:
            p95_index = max(0, min(len(latencies) - 1, int(len(latencies) * 0.95)))
            latency_summary = {
                "count": len(latencies),
                "p50_ms": round(float(median(latencies)), 2),
                "p95_ms": round(float(latencies[p95_index]), 2),
                "max_ms": round(float(latencies[-1]), 2),
            }
        else:
            latency_summary = {
                "count": 0,
                "p50_ms": 0.0,
                "p95_ms": 0.0,
                "max_ms": 0.0,
            }
        return {
            "availabilityPercent": round(availability, 2),
            "errorRatePercent": round(error_rate, 2),
            "totalRequests": total,
            "latencyMs": latency_summary,
        }


class OperationalMetrics:
    """Bounded in-memory counters for request, error, latency, and SLI
    tracking, plus dependency health."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started_at = _monotonic()
        self._request_total = 0
        self._requests_by_route: Counter[str] = Counter()
        self._status_by_code: Counter[str] = Counter()
        self._error_by_category: Counter[str] = Counter()
        self._latencies_ms: deque[float] = deque(maxlen=2000)
        self._recent_failures: deque[dict[str, Any]] = deque(maxlen=50)
        # Per-category SLI windows.
        self._sli_windows: dict[str, _SLIWindow] = {
            cat: _SLIWindow() for cat in OPERATION_CATEGORIES
        }
        # Dependency health: name -> {healthy, last_check, last_error}.
        self._dependency_health: dict[str, dict[str, Any]] = {
            name: {"healthy": True, "last_check": None, "last_error": None}
            for name in DEPENDENCY_NAMES
        }

    # ------------------------------------------------------------------
    # HTTP request tracking (existing interface, preserved)
    # ------------------------------------------------------------------
    def record_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_ms: float,
        error_category: str | None = None,
    ) -> None:
        with self._lock:
            self._request_total += 1
            self._requests_by_route[route] += 1
            self._status_by_code[str(status_code)] += 1
            if error_category or status_code >= 400:
                category = error_category or f"http_{status_code}"
                self._error_by_category[category] += 1
            self._latencies_ms.append(float(duration_ms))
            if status_code >= 400:
                self._recent_failures.append(
                    {
                        "timestamp": _now(),
                        "method": method,
                        "route": route,
                        "status_code": status_code,
                        "category": error_category or f"http_{status_code}",
                    }
                )
            # Also feed the http_request SLI window.
            self._sli_windows["http_request"].record(
                success=status_code < 400, duration_ms=duration_ms
            )

    # ------------------------------------------------------------------
    # Domain event tracking (privacy-safe SLIs)
    # ------------------------------------------------------------------
    def record_domain_event(
        self,
        *,
        category: str,
        success: bool,
        duration_ms: float,
        provider: str | None = None,
        model: str | None = None,
        error_category: str | None = None,
    ) -> None:
        """Record a domain-level event for SLI tracking.

        ``category`` must be one of ``OPERATION_CATEGORIES``.  ``provider`` and
        ``model`` are optional low-cardinality identifiers (e.g. ``local``,
        ``openai_compatible``) and are only stored as counts, never as
        high-cardinality labels.
        """
        if category not in OPERATION_CATEGORIES:
            category = "http_request"
        with self._lock:
            self._sli_windows[category].record(
                success=success, duration_ms=duration_ms
            )
            if not success:
                cat = error_category or f"{category}_failure"
                self._error_by_category[cat] += 1
                self._recent_failures.append(
                    {
                        "timestamp": _now(),
                        "category": cat,
                        "provider": provider,
                        "model": model,
                    }
                )

    # ------------------------------------------------------------------
    # Dependency health
    # ------------------------------------------------------------------
    def record_dependency_health(
        self,
        *,
        name: str,
        healthy: bool,
        error: str | None = None,
    ) -> None:
        """Record the health of a dependency (database, model provider, …)."""
        if name not in DEPENDENCY_NAMES:
            return
        with self._lock:
            entry = self._dependency_health[name]
            entry["healthy"] = healthy
            entry["last_check"] = _now()
            if error:
                entry["last_error"] = error
            elif healthy:
                entry["last_error"] = None

    def get_dependency_health(self, name: str) -> dict[str, Any] | None:
        """Return the current health entry for a dependency."""
        with self._lock:
            entry = self._dependency_health.get(name)
            if entry is None:
                return None
            return {
                "healthy": entry["healthy"],
                "lastCheck": entry["last_check"],
                "lastError": entry["last_error"],
            }

    def all_dependency_health(self) -> dict[str, dict[str, Any]]:
        """Return a copy of all dependency health entries."""
        with self._lock:
            result: dict[str, dict[str, Any]] = {}
            for name, entry in self._dependency_health.items():
                result[name] = {
                    "name": name,
                    "healthy": entry["healthy"],
                    "lastCheck": entry["last_check"],
                    "lastError": entry["last_error"],
                }
            return result

    # ------------------------------------------------------------------
    # SLI snapshots
    # ------------------------------------------------------------------
    def sli_snapshot(self, category: str | None = None) -> dict[str, Any]:
        """Return the SLI snapshot for one category, or all categories."""
        with self._lock:
            if category:
                if category not in self._sli_windows:
                    category = "http_request"
                result = self._sli_windows[category].snapshot()
                result["category"] = category
                return result
            return {
                cat: {**self._sli_windows[cat].snapshot(), "category": cat}
                for cat in OPERATION_CATEGORIES
            }

    # ------------------------------------------------------------------
    # Full snapshot (existing interface, extended)
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latencies = sorted(self._latencies_ms)
            if not latencies:
                latency_summary = {
                    "count": 0,
                    "p50_ms": 0.0,
                    "p95_ms": 0.0,
                    "max_ms": 0.0,
                }
            else:
                p95_index = max(0, min(len(latencies) - 1, int(len(latencies) * 0.95)))
                latency_summary = {
                    "count": len(latencies),
                    "p50_ms": round(float(median(latencies)), 2),
                    "p95_ms": round(float(latencies[p95_index]), 2),
                    "max_ms": round(float(latencies[-1]), 2),
                }
            return {
                "status": "ok",
                "uptimeSeconds": round(_monotonic() - self._started_at, 2),
                "requestsTotal": self._request_total,
                "requestsByRoute": dict(sorted(self._requests_by_route.items())),
                "statusCodes": dict(sorted(self._status_by_code.items())),
                "errorCounts": dict(sorted(self._error_by_category.items())),
                "latencyMs": latency_summary,
                "recentFailures": list(self._recent_failures),
                "sli": self.sli_snapshot(),
                "dependencies": self.all_dependency_health(),
            }
