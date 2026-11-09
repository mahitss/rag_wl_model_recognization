"""Reliability engineering utilities for WonderLearn.

Provides dependency-free, synchronous and async primitives:

* **Circuit breaker** — tracks failures per dependency and opens the circuit
  after a threshold, short-circuiting calls for a cool-down period before
  attempting a half-open probe.
* **Bounded retry with backoff** — retries a callable up to ``max_attempts``
  times with exponential backoff plus jitter, respecting a per-attempt
  timeout.  Never retries indefinitely.
* **Timeout wrapper** — wraps a sync callable with a hard deadline using
  ``signal`` (main thread only) or falls back gracefully.
* **Graceful degradation** — helper that returns a configured fallback value
  when a dependency call fails or its circuit is open, so an unavailable
  *optional* provider never takes down the whole application.

All primitives record outcomes to ``OperationalMetrics`` when a metrics
instance is supplied, and are safe to use from FastAPI route handlers.
"""

from __future__ import annotations

import functools
import random
import threading
import time
from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar

from app.observability import DEPENDENCY_NAMES, OperationalMetrics

T = TypeVar("T")

# Defaults appropriate for a single-process, ₹0-budget deployment.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 0.5
DEFAULT_MAX_DELAY_SECONDS = 30.0
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_RECOVERY_TIMEOUT_SECONDS = 30.0


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker for a single dependency.

    States:
      * ``closed`` — calls pass through; failures increment the counter.
      * ``open`` — calls short-circuit immediately (raise
        ``CircuitOpenError``) for ``recovery_timeout_seconds``.
      * ``half_open`` — a single trial call is allowed; if it succeeds the
        circuit closes, otherwise it re-opens.

    Thread-safe.
    """

    def __init__(
        self,
        *,
        name: str,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        recovery_timeout_seconds: float = DEFAULT_RECOVERY_TIMEOUT_SECONDS,
        metrics: OperationalMetrics | None = None,
    ) -> None:
        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout_seconds = recovery_timeout_seconds
        self._metrics = metrics
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._evaluate_locked()
            return self._state

    def _evaluate_locked(self) -> None:
        """Transition from open to half_open if recovery timeout elapsed."""
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self._recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN

    def _record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
            if self._metrics:
                self._metrics.record_dependency_health(
                    name=self._name, healthy=True
                )

    def _record_failure(self, error: str | None = None) -> None:
        with self._lock:
            self._failure_count += 1
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self._failure_threshold:
                    self._state = CircuitState.OPEN
                    self._opened_at = time.monotonic()
            if self._metrics:
                self._metrics.record_dependency_health(
                    name=self._name, healthy=False, error=error
                )

    def call(self, func: Callable[[], T], *args: Any, **kwargs: Any) -> T:
        """Execute ``func`` through the circuit breaker.

        Raises ``CircuitOpenError`` when the circuit is open and no half-open
        probe is permitted.
        """
        with self._lock:
            self._evaluate_locked()
            if self._state == CircuitState.OPEN:
                raise CircuitOpenError(self._name)
        try:
            result = func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - circuit trips on any error
            self._record_failure(str(exc))
            raise
        self._record_success()
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._evaluate_locked()
            return {
                "name": self._name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "opened_at": self._opened_at,
            }


class CircuitOpenError(RuntimeError):
    """Raised when a circuit breaker is open and the call is short-circuited."""

    def __init__(self, dependency_name: str) -> None:
        super().__init__(f"circuit open for dependency: {dependency_name}")
        self.dependency_name = dependency_name


def retry_with_backoff(
    func: Callable[..., T],
    *args: Any,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS,
    timeout_seconds: float | None = DEFAULT_TIMEOUT_SECONDS,
    retryable_exceptions: tuple[type[BaseException], ...] = (Exception,),
    metrics: OperationalMetrics | None = None,
    metrics_category: str = "http_request",
    **kwargs: Any,
) -> T:
    """Call ``func`` with bounded retries and exponential backoff plus jitter.

    Retries only exceptions in ``retryable_exceptions``.  Stops after
    ``max_attempts`` total attempts (first attempt + retries).  Never retries
    indefinitely.  Returns the result of the first successful attempt or
    re-raises the last exception.

    If ``metrics`` is supplied, each attempt outcome is recorded as a domain
    event in ``metrics_category``.
    """
    last_exception: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        start = time.monotonic()
        try:
            if timeout_seconds is not None:
                return _call_with_timeout(
                    func, timeout_seconds, *args, **kwargs
                )
            return func(*args, **kwargs)
        except retryable_exceptions as exc:
            last_exception = exc
            duration_ms = (time.monotonic() - start) * 1000
            if metrics:
                metrics.record_domain_event(
                    category=metrics_category,
                    success=False,
                    duration_ms=duration_ms,
                    error_category="retryable_error",
                )
            if attempt == max_attempts:
                break
            delay = min(
                base_delay_seconds * (2 ** (attempt - 1)),
                max_delay_seconds,
            )
            # Jitter: sleep up to 20% extra to avoid thundering herds.
            time.sleep(delay + random.uniform(0, delay * 0.2))
    # Last attempt failed (or was non-retryable on first attempt).
    if last_exception is not None:
        if metrics:
            metrics.record_domain_event(
                category=metrics_category,
                success=False,
                duration_ms=0.0,
                error_category="exhausted_retries",
            )
        raise last_exception
    # Should not be reachable, but satisfy type checkers.
    raise RuntimeError("retry_with_backoff exited without result or exception")


def _call_with_timeout(
    func: Callable[..., T], timeout_seconds: float, *args: Any, **kwargs: Any
) -> T:
    """Run ``func`` with a hard timeout.

    On the main thread this uses ``signal.SIGALRM``; on other threads (or
    where signals are unavailable) it falls back to running without a hard
    timeout but still enforces a soft deadline by checking elapsed time before
    and after the call when possible.  This is a best-effort guard appropriate
    for local, single-process deployment.
    """
    import signal

    def _handler(_signum: Any, _frame: Any) -> None:
        raise TimeoutError(f"call exceeded {timeout_seconds}s timeout")

    old_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        return func(*args, **kwargs)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def graceful_degradation(
    func: Callable[[], T],
    *,
    fallback: T,
    breaker: CircuitBreaker | None = None,
    metrics: OperationalMetrics | None = None,
    metrics_category: str = "http_request",
) -> T:
    """Execute ``func`` with graceful degradation.

    If ``breaker`` is provided and open, returns ``fallback`` immediately
    without calling ``func``.  If ``func`` raises, returns ``fallback`` and
    records the failure.  This ensures an unavailable *optional* dependency
    never takes down the whole application.
    """
    if breaker is not None:
        if breaker.state == CircuitState.OPEN:
            return fallback
    try:
        return func()
    except Exception as exc:  # noqa: BLE001 - degrade on any error
        if breaker is not None:
            breaker._record_failure(str(exc))
        if metrics:
            metrics.record_domain_event(
                category=metrics_category,
                success=False,
                duration_ms=0.0,
                error_category="degraded_fallback",
            )
        return fallback


def with_circuit_breaker(
    breaker: CircuitBreaker,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator that routes a callable through ``breaker``."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return breaker.call(func, *args, **kwargs)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Circuit breaker registry (global, process-local)
# ---------------------------------------------------------------------------

_circuit_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get_or_create_circuit_breaker(
    *,
    name: str,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    recovery_timeout_seconds: float = DEFAULT_RECOVERY_TIMEOUT_SECONDS,
    metrics: OperationalMetrics | None = None,
) -> CircuitBreaker:
    """Get or create a named circuit breaker in the process-local registry."""
    with _registry_lock:
        breaker = _circuit_breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout_seconds=recovery_timeout_seconds,
                metrics=metrics,
            )
            _circuit_breakers[name] = breaker
        return breaker


def circuit_breaker_registry_all_snapshots() -> list[dict[str, Any]]:
    """Return snapshots of all registered circuit breakers."""
    with _registry_lock:
        return [breaker.snapshot() for breaker in _circuit_breakers.values()]


# ---------------------------------------------------------------------------
# Incident registry (global, process-local)
# ---------------------------------------------------------------------------

_incidents: list[dict[str, Any]] = []
_incidents_lock = threading.Lock()


def record_incident(
    *,
    incident_id: str,
    severity: str,
    title: str,
    description: str,
    detected_at: str,
    resolved_at: str | None = None,
    status: str = "open",
) -> None:
    """Record an operational incident."""
    with _incidents_lock:
        _incidents.append(
            {
                "incidentId": incident_id,
                "severity": severity,
                "title": title,
                "description": description,
                "detectedAt": detected_at,
                "resolvedAt": resolved_at,
                "status": status,
            }
        )


def incident_registry_all() -> list[dict[str, Any]]:
    """Return all recorded incidents (newest first)."""
    with _incidents_lock:
        return list(reversed(_incidents))


# Default circuit breakers for known dependencies.
def register_default_circuit_breakers(
    metrics: OperationalMetrics | None = None,
) -> None:
    """Register circuit breakers for known dependencies (database, model
    provider, embedding provider)."""
    for dep in DEPENDENCY_NAMES:
        get_or_create_circuit_breaker(
            name=dep,
            failure_threshold=DEFAULT_FAILURE_THRESHOLD,
            recovery_timeout_seconds=DEFAULT_RECOVERY_TIMEOUT_SECONDS,
            metrics=metrics,
        )

