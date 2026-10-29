"""Quality evaluation framework for M45 AI observability.

Runs offline regression suites against bundled datasets to measure retrieval
quality, citation validity, evidence coverage, and quiz schema validity.
All evaluation is local-only: no external model calls, no paid services,
no automatic model downloads.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
API = ROOT / "services" / "api"
DATASET_DIR = ROOT / "evaluations" / "semantic"
OUTPUT_DIR = ROOT / "evaluations" / "generated"


class ModelEvaluationError(RuntimeError):
    """Raised when an offline evaluation cannot complete."""


def _run_python(command: list[str]) -> dict[str, Any]:
    """Run a python subprocess and return stdout/stderr/exit code."""
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_json(stdout: str) -> dict[str, Any]:
    """Extract a JSON object from subprocess stdout, tolerating surrounding text."""
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ModelEvaluationError("No JSON object found in evaluator output.")
    return json.loads(stdout[start:end + 1])


def _run_retrieval_eval(lexical_only: bool = True) -> dict[str, Any]:
    """Run the semantic retrieval evaluator and return its JSON report."""
    script = DATASET_DIR / "evaluate_retrieval.py"
    command = [sys.executable, str(script)]
    if lexical_only:
        command.append("--lexical-only")
    result = _run_python(command)
    if result["exit_code"] != 0:
        raise ModelEvaluationError(
            f"Retrieval evaluator failed (exit {result['exit_code']}): "
            f"{result['stderr'][-1000:]}"
        )
    try:
        return _extract_json(result["stdout"])
    except (json.JSONDecodeError, ValueError) as exc:
        raise ModelEvaluationError(f"Retrieval evaluator did not emit JSON: {exc}") from exc


def _run_quality_eval(lexical_only: bool = True) -> dict[str, Any]:
    """Run the M18 real-world quality evaluator and return its JSON report."""
    script = ROOT / "evaluations" / "realworld" / "evaluate_quality.py"
    command = [sys.executable, str(script)]
    if lexical_only:
        command.append("--lexical-only")
    result = _run_python(command)
    if result["exit_code"] != 0:
        raise ModelEvaluationError(
            f"Quality evaluator failed (exit {result['exit_code']}): {result['stderr'][-1000:]}"
        )
    try:
        return _extract_json(result["stdout"])
    except (json.JSONDecodeError, ValueError) as exc:
        raise ModelEvaluationError(f"Quality evaluator did not emit JSON: {exc}") from exc


def evaluate_retrieval_quality(lexical_only: bool = True) -> dict[str, Any]:
    """Measure retrieval quality across retrieval modes.

    Returns metrics keyed by mode: recall@5, precision@5, MRR, citation
    validity rate, unsupported abstention recall, answerable evidence coverage.
    """
    report = _run_retrieval_eval(lexical_only=lexical_only)
    metrics = report.get("metrics", {})
    return {
        "dataset": "retrieval_eval",
        "metric": "retrieval_quality",
        "mode": "lexical_only" if lexical_only else "semantic_hybrid",
        "metrics": metrics,
        "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def evaluate_citation_validity(lexical_only: bool = True) -> dict[str, Any]:
    """Measure citation validity rate from the retrieval evaluator."""
    report = _run_retrieval_eval(lexical_only=lexical_only)
    metrics = report.get("metrics", {})
    rates: dict[str, Any] = {}
    for mode, values in metrics.items():
        rates[mode] = values.get("citation_validity_rate")
    return {
        "dataset": "retrieval_eval",
        "metric": "citation_validity",
        "mode": "lexical_only" if lexical_only else "semantic_hybrid",
        "citation_validity_rate": rates,
        "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def evaluate_evidence_coverage(lexical_only: bool = True) -> dict[str, Any]:
    """Measure answerable evidence coverage from the retrieval evaluator."""
    report = _run_retrieval_eval(lexical_only=lexical_only)
    metrics = report.get("metrics", {})
    coverage: dict[str, Any] = {}
    for mode, values in metrics.items():
        coverage[mode] = values.get("answerable_evidence_coverage")
    return {
        "dataset": "retrieval_eval",
        "metric": "evidence_coverage",
        "mode": "lexical_only" if lexical_only else "semantic_hybrid",
        "answerable_evidence_coverage": coverage,
        "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def evaluate_quiz_schema_validity(lexical_only: bool = True) -> dict[str, Any]:
    """Measure quiz schema validity via the quality evaluator.

    The quality evaluator emits a human-readable markdown report rather than
    machine-parseable JSON. When it cannot be parsed as JSON, return a
    placeholder result so the endpoint stays available (telemetry failures
    never break core workflows).
    """
    try:
        report = _run_quality_eval(lexical_only=lexical_only)
        parsed = True
    except ModelEvaluationError:
        report = {
            "error": "quality evaluator did not emit machine-parseable JSON",
            "note": "Run evaluations/realworld/evaluate_quality.py manually for the full report.",
        }
        parsed = False
    return {
        "dataset": "quality_v2",
        "metric": "quiz_schema_validity",
        "mode": "lexical_only" if lexical_only else "semantic_hybrid",
        "report": report,
        "parsed": parsed,
        "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def evaluate_model_failure_rate() -> dict[str, Any]:
    """Return model failure/timeout rates from current observability snapshot.

    Computed from in-memory telemetry only; no external calls made.
    """
    from app.model_observability import get_model_observability

    snapshot = get_model_observability().snapshot()
    total = snapshot.get("total_observations") or 0
    failure_rate = snapshot.get("failure_rate") or 0.0
    failure_count = sum(
        snapshot.get("outcome_counts", {}).get(k, 0)
        for k in ("timeout", "cancellation", "failure")
    )
    return {
        "dataset": "observability",
        "metric": "model_failure_rate",
        "mode": "runtime_telemetry",
        "total_observations": total,
        "failure_count": failure_count,
        "failure_rate": failure_rate,
        "outcome_counts": snapshot.get("outcome_counts", {}),
        "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def run_evaluation(
    task_type: str,
    dataset: str,
    metric: str,
    lexical_only: bool = True,
) -> dict[str, Any]:
    """Run a single named evaluation and return a normalised result."""
    if metric == "retrieval_quality":
        return evaluate_retrieval_quality(lexical_only=lexical_only)
    if metric == "citation_validity":
        return evaluate_citation_validity(lexical_only=lexical_only)
    if metric == "evidence_coverage":
        return evaluate_evidence_coverage(lexical_only=lexical_only)
    if metric == "quiz_schema_validity":
        return evaluate_quiz_schema_validity(lexical_only=lexical_only)
    if metric in ("model_failure_rate", "model_timeout_rate"):
        return evaluate_model_failure_rate()
    raise ModelEvaluationError(f"Unknown metric: {metric}")
