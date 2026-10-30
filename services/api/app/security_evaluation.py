"""M47: AI Security Red-Team & Adversarial Evaluation Framework.

Provides a deterministic, offline adversarial evaluation suite for WonderLearn's
AI safety controls. Every test case records:

* Attack category and test case ID
* Expected security behavior
* Actual observed behavior
* PASS/FAIL result (skipped tests are never counted as passed)
* Severity and reproducibility instructions

The framework computes pass rates by attack category and emits a structured
report suitable for CI consumption. It never sends private learning content
to external services and never uses real secrets or real users' private
documents in adversarial tests.

No security guarantee is claimed or certified. The results describe observed
behavior of the current implementation against synthetic attack fixtures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TestCaseResult:
    """Result of a single adversarial test case."""

    test_case_id: str
    category: str
    expected_behavior: str
    actual_behavior: str
    passed: bool
    severity: str
    reproducibility: str
    skipped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_case_id": self.test_case_id,
            "category": self.category,
            "expected_behavior": self.expected_behavior,
            "actual_behavior": self.actual_behavior,
            "passed": self.passed,
            "severity": self.severity,
            "reproducibility": self.reproducibility,
            "skipped": self.skipped,
        }


@dataclass
class CategorySummary:
    """Pass-rate summary for one attack category."""

    category: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def pass_rate(self) -> float:
        """Pass rate over non-skipped cases only. Skipped never counts as passed."""
        considered = self.total - self.skipped
        if considered == 0:
            return 0.0
        return round(self.passed / considered, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "pass_rate": self.pass_rate,
        }


@dataclass
class SecurityEvaluationReport:
    """Complete red-team evaluation report."""

    version: str = "1"
    total_cases: int = 0
    total_passed: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    categories: list[CategorySummary] = field(default_factory=list)
    results: list[TestCaseResult] = field(default_factory=list)

    @property
    def overall_pass_rate(self) -> float:
        considered = self.total_cases - self.total_skipped
        if considered == 0:
            return 0.0
        return round(self.total_passed / considered, 4)

    @property
    def critical_failures(self) -> list[TestCaseResult]:
        return [
            r for r in self.results if not r.passed and r.severity == "critical" and not r.skipped
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "total_cases": self.total_cases,
            "total_passed": self.total_passed,
            "total_failed": self.total_failed,
            "total_skipped": self.total_skipped,
            "overall_pass_rate": self.overall_pass_rate,
            "critical_failure_count": len(self.critical_failures),
            "categories": [c.to_dict() for c in self.categories],
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def add_result(self, result: TestCaseResult) -> None:
        self.total_cases += 1
        if result.skipped:
            self.total_skipped += 1
        elif result.passed:
            self.total_passed += 1
        else:
            self.total_failed += 1

        # Upsert category summary.
        for cat in self.categories:
            if cat.category == result.category:
                cat.total += 1
                if result.skipped:
                    cat.skipped += 1
                elif result.passed:
                    cat.passed += 1
                else:
                    cat.failed += 1
                break
        else:
            cat = CategorySummary(category=result.category, total=1)
            if result.skipped:
                cat.skipped += 1
            elif result.passed:
                cat.passed += 1
            else:
                cat.failed += 1
            self.categories.append(cat)

        self.results.append(result)

    def fails_ci(self) -> bool:
        """Return True if any critical test case failed (non-skipped)."""
        return len(self.critical_failures) > 0


def record(
    report: SecurityEvaluationReport,
    *,
    test_case_id: str,
    category: str,
    expected_behavior: str,
    actual_behavior: str,
    passed: bool,
    severity: str,
    reproducibility: str,
    skipped: bool = False,
) -> None:
    """Convenience helper to record a test case result into a report."""
    report.add_result(
        TestCaseResult(
            test_case_id=test_case_id,
            category=category,
            expected_behavior=expected_behavior,
            actual_behavior=actual_behavior,
            passed=passed,
            severity=severity,
            reproducibility=reproducibility,
            skipped=skipped,
        )
    )
