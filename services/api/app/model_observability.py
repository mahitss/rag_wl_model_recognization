"""Model observability: records model task outcomes for dashboards and alerts.

All data is privacy-safe: no user IDs, document IDs, prompts, secrets, or raw
model outputs are stored. Only aggregate metadata about model task execution is
retained, bounded in memory to avoid unbounded growth.

Task types: tutor, quiz, flashcard, progress, recommend, manage.
Outcomes: success, timeout, cancellation, failure.
"""

from __future__ import annotations

import threading
from collections import Counter, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median
from typing import Any, Literal

ModelTask = Literal["tutor", "quiz", "flashcard", "progress", "recommend", "manage"]
ModelOutcome = Literal["success", "timeout", "cancellation", "failure"]
CitationValidation = Literal["valid", "invalid", "not_applicable"]


@dataclass(frozen=True)
class ModelObservation:
    """One observed model task execution. No PII is stored."""

    task_type: ModelTask
    provider: Literal["local"]
    model_id: str
    outcome: ModelOutcome
    latency_ms: float
    fallback_occurred: bool
    citation_validation: CitationValidation | None
    abstention: bool | None
    timestamp: str


class ModelObservability:
    """Records and aggregates model task execution outcomes.

    Bounded in memory: keeps at most ``max_records`` observations and a small
    window of recent failures. Older observations are dropped as new ones
    arrive, so memory usage stays constant regardless of traffic volume.
    """

    def __init__(self, max_records: int = 2000) -> None:
        self._lock = threading.Lock()
        self._max_records = max_records
        self._observations: deque[ModelObservation] = deque(maxlen=max_records)
        self._task_counts: Counter[str] = Counter()
        self._outcome_counts: Counter[str] = Counter()
        self._provider_counts: Counter[str] = Counter()
        self._latencies_ms: deque[float] = deque(maxlen=max_records)
        self._fallback_count = 0
        self._recent_failures: deque[dict[str, Any]] = deque(maxlen=50)

    def record(
        self,
        *,
        task_type: ModelTask,
        provider: Literal["local"],
        model_id: str,
        outcome: ModelOutcome,
        latency_ms: float,
        fallback_occurred: bool,
        citation_validation: CitationValidation | None = None,
        abstention: bool | None = None,
    ) -> None:
        """Record one model task outcome."""
        observation = ModelObservation(
            task_type=task_type,
            provider=provider,
            model_id=model_id,
            outcome=outcome,
            latency_ms=float(latency_ms),
            fallback_occurred=bool(fallback_occurred),
            citation_validation=citation_validation,
            abstention=abstention,
            timestamp=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            self._observations.append(observation)
            self._task_counts[task_type] += 1
            self._outcome_counts[outcome] += 1
            self._provider_counts[provider] += 1
            self._latencies_ms.append(float(latency_ms))
            if fallback_occurred:
                self._fallback_count += 1
            if outcome in {"timeout", "cancellation", "failure"}:
                self._recent_failures.append(
                    {
                        "timestamp": observation.timestamp,
                        "task_type": task_type,
                        "model_id": model_id,
                        "outcome": outcome,
                        "latency_ms": float(latency_ms),
                        "fallback_occurred": bool(fallback_occurred),
                    }
                )

    def snapshot(self) -> dict[str, Any]:
        """Return an aggregate snapshot of all model task observations.

        Never exposes raw prompts, document content, user IDs, or secrets.
        """
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

            total = len(self._observations)
            success_count = self._outcome_counts.get("success", 0)
            failure_count = (
                self._outcome_counts.get("timeout", 0)
                + self._outcome_counts.get("cancellation", 0)
                + self._outcome_counts.get("failure", 0)
            )

            return {
                "task_counts": dict(sorted(self._task_counts.items())),
                "outcome_counts": dict(sorted(self._outcome_counts.items())),
                "provider_counts": dict(sorted(self._provider_counts.items())),
                "total_observations": total,
                "success_rate": round(success_count / total, 4) if total else None,
                "failure_rate": round(failure_count / total, 4) if total else None,
                "fallback_count": self._fallback_count,
                "fallback_rate": round(self._fallback_count / total, 4) if total else None,
                "latency_ms": latency_summary,
                "recent_failures": list(self._recent_failures),
            }


# Singleton instance used by the API routes. Created lazily to avoid
# importing this module at import time when settings may not be ready.
_model_observability: ModelObservability | None = None


def get_model_observability() -> ModelObservability:
    """Return the shared model observability instance."""
    global _model_observability
    if _model_observability is None:
        _model_observability = ModelObservability()
    return _model_observability
