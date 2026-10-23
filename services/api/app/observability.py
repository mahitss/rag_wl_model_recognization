"""Privacy-conscious operational metrics for the WonderLearn API."""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from statistics import median
from typing import Any


class OperationalMetrics:
    """Bounded in-memory counters for request, error, and latency tracking."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._request_total = 0
        self._requests_by_route: Counter[str] = Counter()
        self._status_by_code: Counter[str] = Counter()
        self._error_by_category: Counter[str] = Counter()
        self._latencies_ms: deque[float] = deque(maxlen=2000)
        self._recent_failures: deque[dict[str, Any]] = deque(maxlen=50)

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
                        "timestamp": time.time(),
                        "method": method,
                        "route": route,
                        "status_code": status_code,
                        "category": error_category or f"http_{status_code}",
                    }
                )

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
                "uptime_seconds": round(time.monotonic() - self._started_at, 2),
                "requests_total": self._request_total,
                "requests_by_route": dict(sorted(self._requests_by_route.items())),
                "status_codes": dict(sorted(self._status_by_code.items())),
                "error_counts": dict(sorted(self._error_by_category.items())),
                "latency_ms": latency_summary,
                "recent_failures": list(self._recent_failures),
            }
