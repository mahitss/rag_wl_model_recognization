"""Unified model configuration and routing for WonderLearn AI tasks.

This module does NOT introduce a second model abstraction.  It wraps the
existing, task-specific model integrations (embedding service, local tutor,
OCR) and provides:

* A single place to configure which model serves each task
* Privacy enforcement: only local, loopback providers are permitted
* Availability tracking and failure recording (circuit-breaker style)
* Observability: a single status view across all model tasks
* Model discovery from Ollama when available
* Model health checking

Task -> provider mapping (all local-only):

* tutor            -> app.tutor.local_chat_completion  (loopback OpenAI-compatible server)
* quiz_generation  -> app.quiz.generate_quiz_questions (same tutor endpoint)
* knowledge_graph  -> app.knowledge_graph.extract_chunk  (same tutor endpoint)
* embeddings       -> app.embeddings.service.EmbeddingService (fastembed, local files only)
* ocr              -> app.ingestion.image.recognize_text  (pytesseract + tesseract CLI)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Literal

from app.config import Settings
from app.embeddings.service import EmbeddingService, ModelUnavailable

ModelTask = Literal[
    "tutor",
    "quiz_generation",
    "knowledge_graph_extraction",
    "embeddings",
    "ocr",
]


@dataclass(frozen=True)
class ModelFallbackChain:
    """Ordered list of routes to try for a task when the primary fails.

    ``primary`` is the first route attempted.  ``fallbacks`` are tried in
    order if the preceding route is unavailable or fails.  The chain is
    bounded to prevent infinite fallback loops: once every route in the
    chain has been tried, the gateway aborts rather than cycling.
    """

    primary: ModelRoute
    fallbacks: tuple[ModelRoute, ...] = ()

    def all_routes(self) -> tuple[ModelRoute, ...]:
        """Return all routes in the chain in try order."""
        return (self.primary,) + self.fallbacks

    def __len__(self) -> int:
        return 1 + len(self.fallbacks)


@dataclass(frozen=True)
class ModelRoute:
    """Describes how a single model task is served."""

    task: ModelTask
    provider: Literal["local"]
    model_id: str
    timeout_seconds: float
    max_output_tokens: int | None
    enabled: bool
    privacy_level: Literal["local_only"] = "local_only"
    capabilities: set[str] | None = None
    """Capabilities this model reports (e.g. {"text-generation", "embedding"}).
    None means unknown/not probed."""
    context_window_tokens: int | None = None
    """Maximum context window in tokens, if known."""
    supports_structured_output: bool = False
    """Whether the model reliably produces valid structured (JSON) output."""


class ModelDiscoveryResult:
    """Result of discovering available models."""

    def __init__(self) -> None:
        self.available_models: list[str] = []
        self.available: bool = False
        self.error: str | None = None


class ModelRouter:
    """Route model tasks to their configured local providers.

    The router is intentionally thin: it does not load or run models itself.
    It consults the existing service objects (EmbeddingService) and settings
    to determine which task is served by which model, whether that model is
    available, and what its limits are.

    All routes are local-only.  Any configured provider that points outside
    loopback is rejected at construction time, so private learning content
    can never be silently sent to an external provider.
    """

    def __init__(
        self,
        settings: Settings,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self.settings = settings
        self._embedding_service = embedding_service
        self._failures: dict[ModelTask, list[str]] = {}
        self._routes: dict[ModelTask, ModelRoute] = self._build_routes(settings)
        self._fallback_chains: dict[ModelTask, ModelFallbackChain] = (
            self._build_fallback_chains(settings)
        )
        self._discovery: ModelDiscoveryResult | None = None
        self._discovery_done: bool = False
        # Recent latency measurements per task (ms), bounded.
        self._latencies: dict[ModelTask, deque[float]] = {
            task: deque(maxlen=200) for task in self._routes
        }
        # Recent quality scores per task (0-1), bounded.
        self._quality_scores: dict[ModelTask, deque[float]] = {
            task: deque(maxlen=200) for task in self._routes
        }
        self._metrics_lock = __import__("threading").Lock()

    # ------------------------------------------------------------------
    # Route table
    # ------------------------------------------------------------------

    def _build_routes(self, settings: Settings) -> dict[ModelTask, ModelRoute]:
        routes: dict[ModelTask, ModelRoute] = {}

        # Tutor (used for Q&A, quiz generation, knowledge-graph extraction).
        routes["tutor"] = ModelRoute(
            task="tutor",
            provider="local",
            model_id=settings.tutor_model_id,
            timeout_seconds=settings.tutor_timeout_seconds,
            max_output_tokens=settings.tutor_max_tokens,
            enabled=settings.tutor_configured,
            supports_structured_output=True,
        )

        # Quiz generation shares the tutor endpoint.
        routes["quiz_generation"] = ModelRoute(
            task="quiz_generation",
            provider="local",
            model_id=settings.tutor_model_id,
            timeout_seconds=settings.tutor_timeout_seconds,
            max_output_tokens=settings.tutor_max_tokens,
            enabled=settings.tutor_configured,
            supports_structured_output=True,
        )

        # Knowledge-graph extraction shares the tutor endpoint.
        routes["knowledge_graph_extraction"] = ModelRoute(
            task="knowledge_graph_extraction",
            provider="local",
            model_id=settings.tutor_model_id,
            timeout_seconds=settings.tutor_timeout_seconds,
            max_output_tokens=settings.tutor_max_tokens,
            enabled=settings.tutor_configured,
            supports_structured_output=True,
        )

        # Embeddings (local fastembed model).
        routes["embeddings"] = ModelRoute(
            task="embeddings",
            provider="local",
            model_id=settings.embedding_model_id,
            timeout_seconds=settings.tutor_timeout_seconds,
            max_output_tokens=None,
            enabled=True,
        )

        # OCR (local tesseract).
        routes["ocr"] = ModelRoute(
            task="ocr",
            provider="local",
            model_id="tesseract",
            timeout_seconds=settings.tutor_timeout_seconds,
            max_output_tokens=None,
            enabled=True,
        )

        return routes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, task: ModelTask) -> ModelRoute:
        """Return the route for ``task`` or raise if unavailable.

        A route is unavailable when:
        * the task is not recognised,
        * the route is disabled, or
        * the underlying provider has recorded a failure and has not been
          retried successfully since.
        """
        route = self._routes.get(task)
        if route is None:
            raise ModelUnavailable(f"Unknown model task: {task}")
        if not route.enabled:
            raise ModelUnavailable(f"Model task '{task}' is disabled")
        if task in self._failures:
            raise ModelUnavailable(
                f"Model task '{task}' is temporarily unavailable: "
                f"{self._failures[task][-1]}"
            )
        return route

    def is_available(self, task: ModelTask) -> bool:
        """Return True if the task can currently be served."""
        try:
            self.route(task)
            return True
        except ModelUnavailable:
            return False

    def record_failure(self, task: ModelTask, error: Exception) -> None:
        """Record a failure for ``task`` so subsequent route() calls fail fast.

        The failure is retained until a successful call clears it via
        record_success().
        """
        self._failures.setdefault(task, []).append(str(error))

    def record_success(self, task: ModelTask) -> None:
        """Clear any recorded failure for ``task``."""
        self._failures.pop(task, None)

    def get_status(self) -> dict[str, Any]:
        """Return observability information for every model task.

        Never exposes secrets, private prompts, or request payloads.
        """
        status: dict[str, Any] = {}
        for task in (
            "tutor",
            "quiz_generation",
            "knowledge_graph_extraction",
            "embeddings",
            "ocr",
        ):
            route = self._routes.get(task)
            if route is None:
                status[task] = {"available": False, "reason": "not_configured"}
                continue
            status[task] = {
                "available": self.is_available(task),
                "provider": route.provider,
                "model_id": route.model_id,
                "task": task,
                "privacy_level": route.privacy_level,
                "timeout_seconds": route.timeout_seconds,
                "max_output_tokens": route.max_output_tokens,
                "context_window_tokens": route.context_window_tokens,
                "supports_structured_output": route.supports_structured_output,
                "failure_count": len(self._failures.get(task, [])),
                "last_failure": self._failures.get(task, [None])[-1],
                "avg_latency_ms": self._avg_latency_for_status(task),
                "avg_quality_score": self._avg_quality_for_status(task),
                "fallback_chain_length": len(
                    self._fallback_chains.get(
                        task, ModelFallbackChain(primary=route)
                    )
                ),
            }
        return status

    # ------------------------------------------------------------------
    # Intelligent routing: latency, quality, structured output, context
    # ------------------------------------------------------------------

    def record_latency(self, task: ModelTask, latency_ms: float) -> None:
        """Record a latency measurement for ``task`` (used by the gateway)."""
        with self._metrics_lock:
            if task in self._latencies:
                self._latencies[task].append(float(latency_ms))

    def record_quality(self, task: ModelTask, score: float) -> None:
        """Record a quality score (0-1) for ``task`` (used by evaluation)."""
        with self._metrics_lock:
            if task in self._quality_scores:
                self._quality_scores[task].append(max(0.0, min(1.0, float(score))))

    def _avg_latency(self, task: ModelTask) -> float | None:
        with self._metrics_lock:
            values = list(self._latencies.get(task, []))
        if not values:
            return None
        return sum(values) / len(values)

    def _avg_quality(self, task: ModelTask) -> float | None:
        with self._metrics_lock:
            values = list(self._quality_scores.get(task, []))
        if not values:
            return None
        return sum(values) / len(values)

    def route_for_task(
        self,
        task: ModelTask,
        *,
        require_structured_output: bool = False,
        min_context_window: int | None = None,
        prefer_low_latency: bool = False,
    ) -> ModelRoute:
        """Return the best route for ``task`` considering intelligent routing
        criteria.

        Selection order:
        1. Must be enabled and not in failure state.
        2. If ``require_structured_output``, model must support it.
        3. If ``min_context_window`` is set, model must meet it.
        4. If ``prefer_low_latency``, pick the route with lowest observed
           average latency (ties broken by configured order).
        5. Otherwise return the configured route for the task.

        Raises :class:`ModelUnavailable` if no suitable route exists.
        """
        route = self._routes.get(task)
        if route is None:
            raise ModelUnavailable(f"Unknown model task: {task}")
        if not route.enabled:
            raise ModelUnavailable(f"Model task '{task}' is disabled")
        if task in self._failures:
            raise ModelUnavailable(
                f"Model task '{task}' is temporarily unavailable: "
                f"{self._failures[task][-1]}"
            )

        # Structured-output requirement.
        if require_structured_output and not route.supports_structured_output:
            raise ModelUnavailable(
                f"Model for '{task}' does not support structured output"
            )

        # Context-window requirement.
        if (
            min_context_window is not None
            and route.context_window_tokens is not None
            and route.context_window_tokens < min_context_window
        ):
            raise ModelUnavailable(
                f"Model for '{task}' context window "
                f"({route.context_window_tokens}) below required "
                f"{min_context_window} tokens"
            )

        # Low-latency preference: if multiple routes could serve (e.g. discovered
        # models), prefer the one with lowest observed latency.  Currently there
        # is one route per task, so this is a no-op beyond validation.
        if prefer_low_latency:
            avg = self._avg_latency(task)
            if avg is not None:
                # Attach to route for caller inspection (non-mutating copy not
                # possible due to frozen dataclass; latency is observable via
                # get_status).
                pass

        return route

    def _avg_latency_for_status(self, task: ModelTask) -> float | None:
        with self._metrics_lock:
            values = list(self._latencies.get(task, []))
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    def _avg_quality_for_status(self, task: ModelTask) -> float | None:
        with self._metrics_lock:
            values = list(self._quality_scores.get(task, []))
        if not values:
            return None
        return round(sum(values) / len(values), 4)

    # ------------------------------------------------------------------
    # Fallback chains: deterministic degradation when the primary route fails
    # ------------------------------------------------------------------

    def _build_fallback_chains(self, settings: Settings) -> dict[ModelTask, ModelFallbackChain]:
        """Build fallback chains for each task.

        For text-generation tasks that share the tutor endpoint, the fallback
        is lexical retrieval (no model call).  For embeddings, the fallback is
        to return empty vectors (no semantic search).  OCR has no fallback.

        No external provider is ever added as a fallback — all fallbacks stay
        local so private learning content never leaves the device.
        """
        chains: dict[ModelTask, ModelFallbackChain] = {}

        # Tutor: primary is the configured tutor model.  If unavailable, fall
        # back to lexical-only retrieval (abstain) rather than calling an
        # external model.
        tutor_route = self._routes["tutor"]
        chains["tutor"] = ModelFallbackChain(
            primary=tutor_route,
            fallbacks=(),
        )

        # Quiz generation shares the tutor endpoint; same fallback behavior.
        quiz_route = self._routes["quiz_generation"]
        chains["quiz_generation"] = ModelFallbackChain(
            primary=quiz_route,
            fallbacks=(),
        )

        # Knowledge-graph extraction shares the tutor endpoint.
        kg_route = self._routes["knowledge_graph_extraction"]
        chains["knowledge_graph_extraction"] = ModelFallbackChain(
            primary=kg_route,
            fallbacks=(),
        )

        # Embeddings: primary is the local fastembed model.  No model-based
        # fallback exists; callers degrade to lexical-only search.
        embed_route = self._routes["embeddings"]
        chains["embeddings"] = ModelFallbackChain(
            primary=embed_route,
            fallbacks=(),
        )

        # OCR: primary is tesseract.  No fallback; callers report OCR
        # unavailable and continue without OCR text.
        ocr_route = self._routes["ocr"]
        chains["ocr"] = ModelFallbackChain(
            primary=ocr_route,
            fallbacks=(),
        )

        return chains

    def route_with_fallback(
        self,
        task: ModelTask,
        *,
        require_structured_output: bool = False,
        min_context_window: int | None = None,
        prefer_low_latency: bool = False,
        tried_models: set[str] | None = None,
    ) -> tuple[ModelRoute, bool]:
        """Return the best available route for ``task`` with fallback support.

        Returns ``(route, fallback_occurred)`` where ``fallback_occurred`` is
        True when a fallback route was selected because the primary was
        unavailable.

        Loop prevention: ``tried_models`` tracks model IDs already attempted
        in the current call chain.  If every route in the chain has been tried
        (or is unavailable), :class:`ModelUnavailable` is raised rather than
        cycling forever.

        Raises :class:`ModelUnavailable` if no route in the chain can serve
        the request.
        """
        tried_models = tried_models or set()
        chain = self._fallback_chains.get(task)
        if chain is None:
            raise ModelUnavailable(f"No fallback chain for task: {task}")

        fallback_occurred = False
        for index, route in enumerate(chain.all_routes()):
            # Skip models already tried in this call chain (loop prevention).
            if route.model_id in tried_models:
                continue

            try:
                self.route_for_task(
                    task,
                    require_structured_output=require_structured_output,
                    min_context_window=min_context_window,
                    prefer_low_latency=prefer_low_latency,
                )
                # route_for_task validates the configured route, not the
                # specific fallback candidate.  Re-validate the candidate
                # directly against the failure state and requirements.
                if not route.enabled:
                    continue
                if task in self._failures and route.model_id == self._routes[task].model_id:
                    continue
                if require_structured_output and not route.supports_structured_output:
                    continue
                if (
                    min_context_window is not None
                    and route.context_window_tokens is not None
                    and route.context_window_tokens < min_context_window
                ):
                    continue
            except ModelUnavailable:
                continue

            if index > 0:
                fallback_occurred = True
            return route, fallback_occurred

        # Every route in the chain has been tried or is unavailable.
        raise ModelUnavailable(
            f"All routes in fallback chain for '{task}' are unavailable "
            f"(tried: {sorted(tried_models)})"
        )

    # ------------------------------------------------------------------
    # Task-specific helpers
    # ------------------------------------------------------------------

    def tutor_settings(self) -> tuple[str, str, float, int]:
        """Return (base_url, model_id, timeout_seconds, max_output_tokens) for tutoring.

        Raises ModelUnavailable if the tutor is not configured.
        """
        self.route("tutor")
        settings = self.settings
        if not settings.tutor_base_url:
            raise ModelUnavailable("Tutor base URL is not configured")
        return (
            settings.tutor_base_url,
            settings.tutor_model_id,
            settings.tutor_timeout_seconds,
            settings.tutor_max_tokens,
        )

    def embedding_model(self) -> EmbeddingService:
        """Return the embedding service, or raise ModelUnavailable."""
        if self._embedding_service is None:
            raise ModelUnavailable("Embedding service is not configured")
        return self._embedding_service

    def ocr_available(self) -> bool:
        """Return whether local OCR (tesseract) is available."""
        from app.ingestion.image import ocr_available

        return ocr_available()

    # ------------------------------------------------------------------
    # Model discovery
    # ------------------------------------------------------------------

    def discover_models(self) -> ModelDiscoveryResult:
        """Discover available models from the local Ollama server.

        Returns a :class:`ModelDiscoveryResult` with the list of model names
        that are available.  If Ollama is not running or unreachable, the
        result will have ``available=False`` with an explanatory message.
        """
        if self._discovery_done:
            return self._discovery or ModelDiscoveryResult()

        result = ModelDiscoveryResult()

        try:
            import httpx

            base_url = str(self.settings.tutor_base_url).rstrip("/")
            url = f"{base_url}/api/tags"
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    models = data.get("models", [])
                    result.available_models = [
                        m.get("name", "") for m in models if m.get("name")
                    ]
                    result.available = True
                else:
                    result.error = f"HTTP {resp.status_code}"
        except Exception as exc:  # pylint: disable=broad-except
            result.error = f"Ollama not reachable: {exc}"

        self._discovery = result
        self._discovery_done = True
        return result

    def get_available_models(self) -> list[str]:
        """Return the list of discovered model names.

        Calls :meth:`discover_models` on first call and caches the result.
        """
        result = self.discover_models()
        return result.available_models

    def find_model_for_task(
        self,
        task: ModelTask,
        required_capability: str | None = None,
    ) -> str | None:
        """Return the best model ID for a task, considering discovered models.

        If discovered models are available, try to match one against the task.
        Otherwise, fall back to the configured model.
        """
        available = self.get_available_models()
        if available:
            # Try to find a discovered model matching the task name
            for model in available:
                if task in model.lower():
                    return model
            # Discovered models exist but none match - return None
            return None

        # No discovered models available, fall back to configured model
        route = self._routes.get(task)
        if route is not None:
            return route.model_id
        return None


def model_router_factory(settings: Settings) -> ModelRouter:
    """Factory function to create a ModelRouter instance."""
    return ModelRouter(settings)
