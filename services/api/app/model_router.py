"""Unified model configuration and routing for WonderLearn AI tasks.

This module does NOT introduce a second model abstraction.  It wraps the
existing, task-specific model integrations (embedding service, local tutor,
OCR) and provides:

* A single place to configure which model serves each task
* Privacy enforcement: only local, loopback providers are permitted
* Availability tracking and failure recording (circuit-breaker style)
* Observability: a single status view across all model tasks

Task -> provider mapping (all local-only):

* tutor            -> app.tutor.local_chat_completion  (loopback OpenAI-compatible server)
* quiz_generation  -> app.quiz.generate_quiz_questions (same tutor endpoint)
* knowledge_graph  -> app.knowledge_graph.extract_chunk  (same tutor endpoint)
* embeddings       -> app.embeddings.service.EmbeddingService (fastembed, local files only)
* ocr              -> app.ingestion.image.recognize_text  (pytesseract + tesseract CLI)
"""

from __future__ import annotations

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
class ModelRoute:
    """Describes how a single model task is served."""

    task: ModelTask
    provider: Literal["local"]
    model_id: str
    timeout_seconds: float
    max_output_tokens: int | None
    enabled: bool
    privacy_level: Literal["local_only"] = "local_only"


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
        )

        # Quiz generation shares the tutor endpoint.
        routes["quiz_generation"] = ModelRoute(
            task="quiz_generation",
            provider="local",
            model_id=settings.tutor_model_id,
            timeout_seconds=settings.tutor_timeout_seconds,
            max_output_tokens=settings.tutor_max_tokens,
            enabled=settings.tutor_configured,
        )

        # Knowledge-graph extraction shares the tutor endpoint.
        routes["knowledge_graph_extraction"] = ModelRoute(
            task="knowledge_graph_extraction",
            provider="local",
            model_id=settings.tutor_model_id,
            timeout_seconds=settings.tutor_timeout_seconds,
            max_output_tokens=settings.tutor_max_tokens,
            enabled=settings.tutor_configured,
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
                "failure_count": len(self._failures.get(task, [])),
                "last_failure": self._failures.get(task, [None])[-1],
            }
        return status

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
